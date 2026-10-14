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

from igbot import agent, comentarios, config
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
    if await store.paused_until(user_id) > time.time():
        # La conversación la lleva una persona: se guarda pero no se contesta.
        await store.mark_seen([i for i, _ in rows])
        return
    for attempt in range(MAX_REDRAFTS + 1):
        text, acted = await agent.reply(store, google, user_id, [t for _, t in rows], instagram)
        latest = await store.pending(user_id)
        if acted or len(latest) == len(rows) or attempt == MAX_REDRAFTS:
            break
        log.info("Llegaron más mensajes de %s; se rehace la respuesta", user_id)
        rows = latest
    await instagram.send_text(user_id, text)
    await store.mark_answered(user_id, [i for i, _ in rows], text)
    try:
        await agent.summarize_if_needed(store, user_id)
    except Exception:
        log.exception("No se pudo actualizar el resumen de %s", user_id)


MAX_REDRAFTS = 2

dispatcher = Dispatcher(store, handle, config.DEBOUNCE_SECONDS, config.MAX_CONCURRENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.open()
    await my_account()
    if google and comentarios.available():
        try:
            await instagram.subscribe("messages,comments")
        except Exception:
            log.exception("No se pudo suscribir la cuenta a los comentarios")
    await dispatcher.resume_pending()
    refresher = asyncio.create_task(instagram.refresh_loop())
    yield
    refresher.cancel()
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


ECHO_WAIT = 5  # segundos para que el bot termine de anotar sus propios envíos


async def owner_replied(user_id: str, mid: str, text: str) -> None:
    """Eco de un DM: si no lo mandó el bot, lo escribió el dueño desde Instagram.

    En ese caso el bot se calla en esa conversación por HUMAN_PAUSE_HOURS.
    """
    await asyncio.sleep(ECHO_WAIT)
    if await store.was_sent(mid) or (
        text and await store.bot_said(user_id, text, time.time() - 600)
    ):
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
            continue  # es de otra cuenta de la app; lo atiende su propio proceso
        for change in entry.get("changes", []):
            if change.get("field") == "comments" and google and comentarios.available():
                in_background(handle_comment(change.get("value") or {}))
        for event in entry.get("messaging", []):
            message = event.get("message")
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
            if await store.add_incoming(user_id, message["mid"], message_text(message)):
                dispatcher.notify(user_id)

    return {"ok": True}
