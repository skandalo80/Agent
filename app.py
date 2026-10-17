"""Webhook de Instagram + agente de Claude.

Flujo de un DM:
1. Meta llama a POST /webhook. Se valida la firma, se guarda el mensaje como
   pendiente y se responde 200 al instante (si tardamos, Meta reintenta).
2. El Dispatcher despierta la conversación de ese usuario: espera unos
   segundos por si manda más mensajes y luego los contesta juntos y en orden.
3. El agente arma el contexto (datos guardados + resumen + últimos mensajes),
   le pide la respuesta a Claude y se envía por la API de Instagram.
"""
import asyncio
import hashlib
import hmac
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from igbot import admin, agent, botones, comentarios, config, limites, reporte
from igbot.dispatcher import Dispatcher
from igbot.google import Google
from igbot.instagram import Instagram
from igbot.store import Store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("igbot")

store = Store(config.DB_PATH)
instagram = Instagram(store)
# Agenda y correo solo si Google está configurado en .env.
google = (
    Google(config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.GOOGLE_REFRESH_TOKEN)
    if config.GOOGLE_REFRESH_TOKEN
    else None
)


async def handle(user_id: str, rows: list[tuple[int, str]]) -> None:
    """Contesta todos los mensajes pendientes de un usuario de una sola vez.

    Si mientras Claude pensaba llegaron más mensajes de la misma persona, la
    respuesta se descarta y se vuelve a generar con todos, para mandar una
    sola. No se descarta si ya agendó, canceló o mandó un correo.
    """
    if (until := await store.paused_until(user_id)) > time.time():
        # La conversación la lleva una persona: se guarda pero no se contesta.
        log.info("Conversación con %s en pausa %.1f h más; no se contesta", user_id, (until - time.time()) / 3600)
        await store.mark_seen([i for i, _ in rows])
        return
    # Un administrador habla con su propio asistente (publicar, reactivar chats).
    is_admin = admin.is_key(user_id)
    if not is_admin and await over_limits(user_id, rows):
        return
    respond = admin.reply if is_admin else agent.reply
    for attempt in range(MAX_REDRAFTS + 1):
        text, acted = await respond(store, google, user_id, [t for _, t in rows], instagram)
        latest = await store.pending(user_id)
        if acted or len(latest) == len(rows) or attempt == MAX_REDRAFTS:
            break
        log.info("Llegaron más mensajes de %s; se rehace la respuesta", user_id)
        rows = latest
    await instagram.send_text(admin.real_id(user_id), text)
    await store.mark_answered(user_id, [i for i, _ in rows], text)
    try:
        await agent.summarize_if_needed(store, user_id)
    except Exception:
        log.exception("No se pudo actualizar el resumen de %s", user_id)


MAX_REDRAFTS = 2


async def over_limits(user_id: str, rows: list[tuple[int, str]]) -> bool:
    """Si la persona o la cuenta ya pasaron su tope del día, no se llama a Claude."""
    if await limites.over_budget(store):
        log.warning("Presupuesto diario de Claude agotado; no se contesta a %s", user_id)
        await store.mark_seen([i for i, _ in rows])
        if await limites.first_budget_alert(store) and google and config.OWNER_EMAIL:
            try:
                await google.send_email(
                    config.MAIL_FROM, config.MAIL_FROM_NAME, config.OWNER_EMAIL, "",
                    "Instagram: el bot llegó a su gasto máximo de hoy",
                    f"El bot ya gastó US${config.DAILY_BUDGET_USD:g} en Claude hoy y dejó de contestar"
                    " hasta mañana. Si fue un pico normal, sube DAILY_BUDGET_USD en el .env y reinicia.",
                )
            except Exception:
                log.exception("No se pudo avisar al dueño del presupuesto")
        return True
    used = await limites.replies_today(store, user_id)
    if used < config.MAX_REPLIES_PER_USER_PER_DAY:
        return False
    log.warning("%s llegó a %d respuestas hoy; no se contesta", user_id, used)
    if used == config.MAX_REPLIES_PER_USER_PER_DAY:
        # Se avisa una sola vez; el aviso cuenta como respuesta y los siguientes se ignoran.
        await instagram.send_text(user_id, limites.LIMIT_NOTICE)
        await store.mark_answered(user_id, [i for i, _ in rows], limites.LIMIT_NOTICE)
    else:
        await store.mark_seen([i for i, _ in rows])
    return True

dispatcher = Dispatcher(store, handle, config.DEBOUNCE_SECONDS, config.MAX_CONCURRENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.open()
    limites.store = store
    await my_account()
    # Mensajes, toques a los botones de inicio y, si hay reglas, comentarios.
    fields = "messages,messaging_postbacks"
    if google and comentarios.available() or config.HIDE_SPAM:
        fields += ",comments"
    try:
        await instagram.subscribe(fields)
    except Exception:
        log.exception("No se pudo suscribir la cuenta a los avisos")
    await botones.sync(store, instagram)
    await dispatcher.resume_pending()
    loops = [asyncio.create_task(instagram.refresh_loop())]
    if config.ADMIN_IG_IDS:
        loops.append(asyncio.create_task(admin.publisher_loop(store, instagram)))
    if config.ADMIN_IG_IDS or (config.REPORT_EMAIL and google):
        loops.append(asyncio.create_task(reporte.loop(store, instagram, google)))
    yield
    for task in loops:
        task.cancel()
    await dispatcher.stop()
    await instagram.close()
    await store.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


PRIVACY_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Política de privacidad</title>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5">
<h1>Política de privacidad</h1>
<p>Este servicio responde automáticamente a los mensajes directos que recibe la cuenta de Instagram asociada.</p>
<p>Solo se procesan el identificador de Instagram de quien escribe y el contenido del mensaje, únicamente para generar la respuesta. No se venden ni se comparten estos datos con terceros.</p>
<p>Para pedir que se borre cualquier dato, escribe un mensaje directo a la cuenta de Instagram indicándolo.</p>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PRIVACY_HTML


QUOTE_NAME = re.compile(r"^[A-Za-z0-9_-]{20,40}$")


@app.get("/cotizaciones/{token}.pdf")
def quote_pdf(token: str):
    """PDF de una cotización; el nombre es secreto y aleatorio, así nadie adivina otras."""
    path = Path(config.QUOTES_DIR) / f"{token}.pdf"
    if not QUOTE_NAME.match(token) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/pdf", filename="cotizacion.pdf")


@app.get("/cotizaciones/{slug}/{token}.pdf")
def client_quote_pdf(slug: str, token: str):
    """PDF de otra cuenta (p. ej. Nortex): Instagram lo pide al dominio, que llega al proceso principal."""
    path = Path("data") / slug / "cotizaciones" / f"{token}.pdf"
    if not admin.SLUG.match(slug) or not QUOTE_NAME.match(token) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/pdf", filename="cotizacion.pdf")


@app.get("/media/{slug}/{token}.jpg")
def media(slug: str, token: str):
    """Fotos que mandan los administradores para publicar (Instagram las descarga de aquí)."""
    path = admin.media_path(slug, token)
    if not path or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


ECHO_WAIT = 5  # segundos para que el bot termine de anotar sus propios envíos


async def owner_replied(user_id: str, mid: str, text: str) -> None:
    """Eco de un DM: si no lo mandó el bot, lo escribió el dueño desde Instagram.

    En ese caso el bot se calla en esa conversación por HUMAN_PAUSE_HOURS.
    """
    await asyncio.sleep(ECHO_WAIT)
    since = time.time() - 600
    if await store.was_sent(mid) or (text and (
        await store.bot_said(user_id, text, since) or await store.bot_said(admin.key(user_id), text, since)
    )):
        return
    log.info("El dueño contestó a %s; el bot se pausa en esa conversación", user_id)
    await store.pause(user_id, time.time() + config.HUMAN_PAUSE_HOURS * 3600)
    await store.add_owner_message(user_id, mid, text)


background: set[asyncio.Task] = set()
account_id = config.IG_ACCOUNT_ID
# Con varias cuentas, un proceso que no sabe cuál es la suya no contesta nada
# (si no, contestaría los DMs de las otras cuentas).
MULTI_ACCOUNT = bool(config.CLIENTE or config.FORWARD_WEBHOOKS)


async def my_account() -> str:
    global account_id
    if not account_id:
        try:
            account_id = await instagram.account_id()
        except Exception:
            log.exception("No se pudo saber el id de la cuenta de Instagram")
    return account_id


def in_background(coro) -> None:
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


async def forward(body: bytes, signature: str) -> None:
    """Reenvía el aviso tal cual (con su firma) a los procesos de las otras cuentas."""
    async with httpx.AsyncClient(timeout=10) as http:
        for url in config.FORWARD_WEBHOOKS:
            try:
                r = await http.post(url, content=body, headers={
                    "X-Hub-Signature-256": signature, "Content-Type": "application/json",
                })
                r.raise_for_status()
            except Exception:
                log.exception("No se pudo reenviar el aviso a %s", url)


async def handle_comment(value: dict) -> None:
    try:
        await comentarios.handle(value, store, instagram, google, account_id)
    except Exception:
        log.exception("Falló el comentario %s", value.get("id"))


@app.get("/webhook")
def verify(request: Request):
    """Meta llama aquí una vez para confirmar que el webhook es nuestro."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == config.VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="verify token incorrecto")


def signature_ok(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(config.APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def message_text(message: dict) -> str:
    if message.get("text"):
        return message["text"]
    kinds = {a.get("type", "archivo") for a in message.get("attachments", [])}
    return f"[envió: {', '.join(sorted(kinds)) or 'algo que no es texto'}]"


@app.post("/webhook")
async def receive(request: Request):
    body = await request.body()
    if not signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="firma inválida")
    if config.FORWARD_WEBHOOKS:
        in_background(forward(body, request.headers["X-Hub-Signature-256"]))

    data = await request.json()
    mine = await my_account() if MULTI_ACCOUNT else account_id
    if MULTI_ACCOUNT and not mine:
        return {"ok": True}
    for entry in data.get("entry", []):
        if mine and str(entry.get("id")) != mine:
            log.debug("Aviso de otra cuenta (%s); lo atiende su proceso", entry.get("id"))
            continue
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            value = change.get("value") or {}
            log.info("Comentario de %s: %r", (value.get("from") or {}).get("username"), value.get("text", "")[:80])
            if google and comentarios.available() or config.HIDE_SPAM:
                in_background(handle_comment(value))
            else:
                log.info("Comentarios sin configurar (falta SHEET_COMMENTS o Google); se ignora")
        for event in entry.get("messaging", []):
            message = event.get("message")
            if postback := event.get("postback"):
                # Tocaron un botón de inicio: se contesta como si hubiera escrito la pregunta.
                message = {"mid": postback.get("mid") or f"pb-{event.get('timestamp')}",
                           "text": postback.get("title", "")}
            if not message or message.get("is_deleted"):
                continue
            # Eco = un DM que salió de la cuenta. Nunca se contesta (sería un
            # bucle), pero si lo escribió el dueño a mano, el bot se calla.
            if message.get("is_echo"):
                in_background(
                    owner_replied(event["recipient"]["id"], message["mid"], message_text(message))
                )
                continue
            user_id = event["sender"]["id"]
            log.info("DM de %s", user_id)
            text = message_text(message)
            if not admin.is_admin(user_id):
                text = limites.trim(text)
            else:
                if new_mode := admin.command(message.get("text", "")):
                    in_background(admin.set_mode(store, instagram, user_id, new_mode))
                    continue
                if await admin.mode(store, user_id) == "admin":
                    text, photos = await admin.incoming_text(store, user_id, message)
                    for photo in photos:
                        in_background(admin.save_photo_quietly(store, instagram, photo))
                    user_id = admin.key(user_id)
            if await store.add_incoming(user_id, message["mid"], text):
                dispatcher.notify(user_id)

    return {"ok": True}
