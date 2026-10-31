"""Webhook de Instagram + agente de Claude.

Un mismo proceso atiende varias cuentas de Instagram: la de .env y las de
CUENTAS (clientes/<nombre>/.env). Cada aviso de Meta trae el id de su cuenta;
con él se elige la Cuenta y todo lo demás corre dentro de usar(cuenta), con su
base, su token, su prompt y sus límites.

Flujo de un DM:
1. Meta llama a POST /webhook. Se valida la firma, se guarda el mensaje como
   pendiente en la base de su cuenta y se responde 200 al instante (si
   tardamos, Meta reintenta).
2. El Dispatcher de esa cuenta despierta la conversación de ese usuario: espera unos
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
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from igbot import (acceso, admin, agenda_google, agent, ajustes, anuncios_meta, asistente, botones, campanas, cobros,
                   comentarios, comprimir, conectar, config, contenido, empresa, errores, facebook, historias, lentitud,
                   limites, marca, metricas, nueva_campana, panel, reporte, resultados, semana, skandi, sugeridos, tienda_shopify)
from igbot.cuenta import Cuenta, cargar, cuenta, de_carpeta, usar
from igbot.dispatcher import Dispatcher
from igbot.google import Google
from igbot.instagram import Instagram
from igbot.pagina_fb import de_facebook
from igbot.store import Store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("igbot")

cuentas = cargar()
for c in cuentas:
    c.store = Store(c.db_path)
    c.instagram = Instagram(c.store)
principal = cuentas[0]
# Atajos a la cuenta principal (la de .env).
store, instagram = principal.store, principal.instagram
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
    store, instagram = cuenta().store, cuenta().instagram
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
    if not is_admin:
        # Los links a su tienda llevan el código del chat, para saber qué compró quién.
        text = await tienda_shopify.etiquetar(cuenta(), user_id, text)
    if text:  # vacío: lo pasó a tu equipo con "Sin mensaje"
        await instagram.send_text(admin.real_id(user_id), text)
    await store.mark_answered(user_id, [i for i, _ in rows], text)
    try:
        await agent.summarize_if_needed(store, user_id)
    except Exception:
        log.exception("No se pudo actualizar el resumen de %s", user_id)


MAX_REDRAFTS = 2


async def over_limits(user_id: str, rows: list[tuple[int, str]]) -> bool:
    """Si la persona o la cuenta ya pasaron su tope del día, no se llama a Claude."""
    store, instagram = cuenta().store, cuenta().instagram
    if await limites.over_budget(store):
        log.warning("Presupuesto diario de Claude agotado; no se contesta a %s", user_id)
        await store.mark_seen([i for i, _ in rows])
        if await limites.first_budget_alert(store) and google and cuenta().owner_email:
            try:
                await google.send_email(
                    config.MAIL_FROM, cuenta().mail_from_name, cuenta().owner_email, "",
                    "Instagram: el bot llegó a su gasto máximo de hoy",
                    f"El bot ya gastó US${cuenta().daily_budget_usd:g} en Claude hoy y dejó de contestar"
                    " hasta mañana. Si fue un pico normal, sube DAILY_BUDGET_USD en el .env y reinicia.",
                )
            except Exception:
                log.exception("No se pudo avisar al dueño del presupuesto")
        return True
    used = await limites.replies_today(store, user_id)
    if used < cuenta().max_replies_per_user_per_day:
        return False
    log.warning("%s llegó a %d respuestas hoy; no se contesta", user_id, used)
    if used == cuenta().max_replies_per_user_per_day:
        # Se avisa una sola vez; el aviso cuenta como respuesta y los siguientes se ignoran.
        await instagram.send_text(user_id, limites.LIMIT_NOTICE)
        await store.mark_answered(user_id, [i for i, _ in rows], limites.LIMIT_NOTICE)
    else:
        await store.mark_seen([i for i, _ in rows])
    return True

def atender(c: Cuenta):
    async def handler(user_id: str, rows: list[tuple[int, str]]) -> None:
        with usar(c):
            await handle(user_id, rows)
    return handler


# Una cola por cuenta (orden por persona) con un tope común de respuestas a la vez.
shared_slots = asyncio.Semaphore(config.MAX_CONCURRENT)


def cola(c: Cuenta) -> Dispatcher:
    return Dispatcher(c.store, atender(c), config.DEBOUNCE_SECONDS,
                      config.MAX_CONCURRENT_PER_ACCOUNT, shared=shared_slots)


dispatchers = {c.slug: cola(c) for c in cuentas}


def nombre(c: Cuenta) -> str:
    return c.slug or "principal"


async def arrancar(c: Cuenta) -> None:
    with usar(c):
        await c.store.open()
        await ajustes.cargar(c)  # lo que cambió en su panel manda sobre su .env
        await resolver_id(c)
        # Mensajes, toques a los botones de inicio y, si hay reglas, comentarios.
        fields = "messages,messaging_postbacks"
        if google and comentarios.available() or c.hide_spam or c.imagenes_mes:
            fields += ",comments"
        try:
            await c.instagram.subscribe(fields)
        except Exception:
            log.exception("No se pudo suscribir la cuenta %s a los avisos", nombre(c))
        await conectar_pagina(c)
        await botones.sync(c.store, c.instagram)
        await dispatchers[c.slug].resume_pending()
        if await empresa.revision_pendiente(c):  # guardó su perfil de empresa justo antes de reiniciar
            empresa.programar_revision(c, ya=True)


async def conectar_pagina(c: Cuenta) -> None:
    """Si conectó su página de Facebook: desde aquí le llegan sus avisos (Messenger y comentarios)."""
    c.fb_page_id = await c.store.get("fb_page_id") or ""
    if not c.fb_page_id or c.ig_via == "facebook":  # con el login de Facebook ya se suscribió arriba
        return
    try:
        await c.instagram.pagina.suscribir()
    except Exception:
        log.exception("No se pudo suscribir la página de Facebook de %s", nombre(c))


async def renovar_token(c: Cuenta) -> None:
    await c.instagram.refresh_if_due()


async def publicar(c: Cuenta) -> None:
    # Las programan sus administradores por DM o el cliente desde su panel (Contenido).
    if c.admin_ig_ids or c.imagenes_mes:
        await admin.publish_due(c.store, c.instagram)


async def reporte_semanal(c: Cuenta) -> None:
    if c.admin_ig_ids or (c.report_email and google):
        await reporte.tick(c.store, c.instagram, google)


async def sugeridos_del_lunes(c: Cuenta) -> None:
    await sugeridos.tick(c)  # más opciones de Objetivo y Mensaje para Crear campaña


async def metricas_del_dia(c: Cuenta) -> None:
    await metricas.tick(c)  # seguidores, alcance y vistas de cada día, para Resultados


async def cada_cuenta(paso, every: float) -> None:
    """Un solo bucle de fondo por tarea, que pasa por todas las cuentas."""
    while True:
        for c in cuentas:
            with usar(c):
                try:
                    await paso(c)
                except Exception:
                    log.exception("Falló %s en la cuenta %s", paso.__name__, nombre(c))
        await asyncio.sleep(every)


def fondo() -> list[asyncio.Task]:
    return [
        asyncio.create_task(cada_cuenta(renovar_token, 3600)),
        asyncio.create_task(cada_cuenta(publicar, 30)),
        asyncio.create_task(cada_cuenta(reporte_semanal, 600)),
        asyncio.create_task(cada_cuenta(sugeridos_del_lunes, 600)),
        asyncio.create_task(cada_cuenta(metricas_del_dia, 600)),
        asyncio.create_task(lentitud.vigilar_bucle()),
    ]


def buscar(ig_id: str) -> Cuenta | None:
    """La cuenta con ese id, la principal incluida (en su panel no se editan las instrucciones)."""
    return next((c for c in cuentas if ig_id and c.ig_account_id == ig_id), None)


async def alta(ig_id: str, usuario: str, token: str, grande: bool = False, via: str = "") -> Cuenta | None:
    """Al conectarse por /conectar: su cuenta, que se crea (en pausa) si es nueva.
    Si es `grande`, además queda en revisión (no puede encenderse sola). `via` = "facebook" si entró con
    el login de Facebook (entonces `token` es el de su página)."""
    if not (c := buscar(ig_id)):
        slug = panel.preparar_carpeta(ig_id, usuario, token, await store.correo_de(usuario),
                                      {c.slug for c in cuentas}, revision=grande, via=via)
        await store.set_conexion_slug(ig_id, slug)
        c = de_carpeta(slug)
        c.store = Store(c.db_path)
        c.instagram = Instagram(c.store)
        dispatchers[slug] = cola(c)
        await arrancar(c)
        await c.store.set("bot_activo", "0")  # no contesta hasta que lo encienda en su panel
        cuentas.append(c)  # desde aquí le llegan sus avisos
        log.info("Cuenta nueva desde el panel: @%s (clientes/%s), en pausa", usuario, slug)
    else:
        # Ya existía: el token nuevo (con los permisos de ahora, p. ej. estadísticas) sustituye
        # al que tenía. Va a su base, igual que cuando se renueva solo cada semana.
        await c.store.set("ig_token", token)
        await c.store.set("ig_token_refreshed_at", str(time.time()))
    await c.store.set("account_username", usuario)
    return c


@asynccontextmanager
async def lifespan(app: FastAPI):
    conectar.store, conectar.instagram, conectar.google = store, instagram, google
    panel.buscar, panel.alta = buscar, alta
    panel.conectar_pagina = conectar_pagina
    acceso.todas = lambda: cuentas
    skandi.principal = lambda: principal
    tienda_shopify.cuentas = cobros.cuentas = lambda: cuentas
    for c in cuentas:
        await arrancar(c)
    await skandi.cargar()  # el historial de Skandi de cada cuenta, a la memoria
    loops = fondo()
    yield
    for task in loops:
        task.cancel()
    for c in cuentas:
        await dispatchers[c.slug].stop()
        await c.instagram.close()
        await c.store.close()


# Sin /docs ni /openapi.json: no hace falta publicar la lista de rutas.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Las peticiones de más de 2 s quedan en el registro ("LENTO"), con su tiempo en base, IG e IA.
lentitud.instalar()
app.add_middleware(comprimir.Comprimir)
app.add_middleware(lentitud.Medir)
if not config.CLIENTE:
    # La página de conectar clientes la atiende solo el proceso principal.
    app.include_router(conectar.router)
    app.include_router(panel.router)
    app.include_router(acceso.router)
    app.include_router(ajustes.router)
    app.include_router(asistente.router)
    app.include_router(contenido.router)
    app.include_router(empresa.router)
    app.include_router(marca.router)
    app.include_router(semana.router)
    app.include_router(nueva_campana.router)  # antes que campanas: /campanas/nueva no es /campanas/{cid}
    app.include_router(campanas.router)
    app.include_router(resultados.router)
    app.include_router(skandi.router)
    app.include_router(skandi.web_router)
    app.include_router(tienda_shopify.router)
    app.include_router(facebook.router)
    app.include_router(anuncios_meta.router)
    app.include_router(tienda_shopify.webhooks)
    app.include_router(agenda_google.router)
    app.include_router(cobros.router)
    app.include_router(cobros.publico)

    @app.exception_handler(StarletteHTTPException)
    async def pagina_404(request: Request, exc: StarletteHTTPException):
        """En el panel, una página 404 de verdad (con su menú) en vez del JSON de FastAPI."""
        if exc.status_code == 404 and request.url.path.startswith(("/panel", "/conectar")):
            return await panel.no_encontrada(request)
        return await http_exception_handler(request, exc)

    # Un error que nadie atrapó: página amable en vez de "Internal Server Error" y correo a soporte.
    app.add_exception_handler(Exception, errores.inesperado)


@app.get("/health")
def health():
    return {"ok": True}


# Los íconos y el logo de skandar.online (web/), para que el panel lleve los mismos.
WEB = Path(__file__).resolve().parent / "web"
ICONOS = {"favicon.ico": "image/x-icon", "favicon-32.png": "image/png", "apple-touch-icon.png": "image/png",
          "logo-skandar.svg": "image/svg+xml", "logo-sk.svg": "image/svg+xml"}


def _icono(nombre: str):
    def ver():
        return FileResponse(WEB / nombre, media_type=ICONOS[nombre],
                            headers={"Cache-Control": "public, max-age=604800"})
    return ver


for _nombre in ICONOS:
    app.get(f"/{_nombre}", include_in_schema=False)(_icono(_nombre))


@app.get("/", include_in_schema=False)
def inicio():
    """console.skandar.online a secas lleva al panel."""
    return RedirectResponse("/panel", status_code=307)


@app.get("/privacy", include_in_schema=False)
def privacy():
    """El aviso completo vive en la web; esta URL vieja puede seguir en el panel de Meta."""
    return RedirectResponse(f"{config.SITE_URL}/privacidad", status_code=301)


QUOTE_NAME = re.compile(r"^[A-Za-z0-9_-]{20,40}$")


@app.get("/cotizaciones/{token}.pdf")
def quote_pdf(token: str):
    """PDF de una cotización; el nombre es secreto y aleatorio, así nadie adivina otras."""
    path = Path(cuenta().quotes_dir) / f"{token}.pdf"
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
    store = cuenta().store
    await asyncio.sleep(ECHO_WAIT)
    since = time.time() - 600
    if await store.was_sent(mid) or (text and (
        await store.bot_said(user_id, text, since) or await store.bot_said(admin.key(user_id), text, since)
    )):
        return
    log.info("El dueño contestó a %s; el bot se pausa en esa conversación", user_id)
    await store.pause(user_id, time.time() + cuenta().human_pause_hours * 3600)
    await store.add_owner_message(user_id, mid, text)


background: set[asyncio.Task] = set()
# Con varias cuentas en la app, un aviso de una cuenta cuyo id no se conoce no
# se contesta (si no, se contestaría con la cuenta equivocada).
MULTI_ACCOUNT = bool(config.CLIENTE or config.FORWARD_WEBHOOKS or len(cuentas) > 1 or conectar.enabled())


async def resolver_id(c: Cuenta) -> None:
    """Id de la cuenta (el que traen los avisos de Meta); si no está en su .env se pregunta."""
    if not c.ig_account_id:
        try:
            c.ig_account_id = await c.instagram.account_id()
        except Exception:
            log.exception("No se pudo saber el id de la cuenta %s", nombre(c))


async def cuenta_de(entry_id: str) -> Cuenta | None:
    for c in cuentas:
        if not c.ig_account_id:
            with usar(c):
                await resolver_id(c)
        if entry_id in (c.ig_account_id, c.fb_page_id):  # su Instagram o su página de Facebook
            return c
    if not MULTI_ACCOUNT and not principal.ig_account_id:
        return principal  # una sola cuenta y no se supo su id: todo es suyo
    return None


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


async def handle_comment(value: dict, de_pagina: bool = False) -> None:
    c = cuenta()
    # Los de la página de Facebook siguen las mismas reglas, pero se contestan por la página.
    canal, propia = (c.instagram.pagina, de_facebook(c.fb_page_id)) if de_pagina else (c.instagram, c.ig_account_id)
    try:
        await comentarios.handle(value, c.store, canal, google, propia)
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
    # Meta firma con la llave de la app de Meta o con la de Instagram (la de
    # /conectar) según cómo se conectó la cuenta; se aceptan las dos.
    given = header.removeprefix("sha256=")
    return any(hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), given)
               for secret in {config.APP_SECRET, config.IG_OAUTH_SECRET})


def message_text(message: dict) -> str:
    if message.get("text"):
        return message["text"]
    kinds = {a.get("type", "archivo") for a in message.get("attachments", [])}
    return f"[envió: {', '.join(sorted(kinds)) or 'algo que no es texto'}]"


@app.post("/webhook")
async def receive(request: Request):
    body = await request.body()
    if not signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        log.warning("Webhook con firma inválida; ¿IG_APP_SECRET o IG_OAUTH_SECRET mal copiados?")
        raise HTTPException(status_code=403, detail="firma inválida")
    if config.FORWARD_WEBHOOKS:
        in_background(forward(body, request.headers["X-Hub-Signature-256"]))

    data = await request.json()
    de_pagina = data.get("object") == "page"  # de una página de Facebook (Messenger y sus comentarios)
    for entry in data.get("entry", []):
        c = await cuenta_de(str(entry.get("id")))
        if not c:
            log.debug("Aviso de otra cuenta (%s); no es de este proceso", entry.get("id"))
            continue
        with usar(c):
            await receive_entry(c, de_messenger(entry) if de_pagina else entry)
    return {"ok": True}


def comentario_de_pagina(value: dict) -> dict | None:
    """Un comentario nuevo en la página, con la forma de los de Instagram (lo que espera comentarios.handle)."""
    if value.get("item") != "comment" or value.get("verb") != "add":
        return None
    return {"id": value.get("comment_id", ""), "from": {"id": de_facebook(str((value.get("from") or {}).get("id", "")))},
            "media": {"id": value.get("post_id", "")}, "text": value.get("message", ""),
            "pagina": True}


def de_messenger(entry: dict) -> dict:
    """Un aviso de la página: quien escribe por Messenger queda como fb:<id> (su chat no se cruza con los
    de Instagram y al contestarle se manda por Messenger) y los comentarios, con la forma de Instagram."""
    eventos = []
    for event in entry.get("messaging", []):
        event = dict(event)
        for lado in ("sender", "recipient"):
            if (quien := event.get(lado) or {}).get("id"):
                event[lado] = {**quien, "id": de_facebook(str(quien["id"]))}
        eventos.append(event)
    cambios = [{"field": "comments", "value": v} for ch in entry.get("changes", [])
               if ch.get("field") == "feed" and (v := comentario_de_pagina(ch.get("value") or {}))]
    return {**entry, "messaging": eventos, "changes": cambios}


async def entrar_mencion(c: Cuenta, user_id: str, mid: str, url: str) -> None:
    with usar(c):
        text = await historias.mencion(c, user_id, url)
        if await c.store.add_incoming(user_id, mid, text):
            dispatchers[c.slug].notify(user_id)


async def receive_entry(c: Cuenta, entry: dict) -> None:
    """Un aviso de una cuenta. Corre dentro de usar(c): lo que se lance aquí es de esa cuenta."""
    store, instagram = c.store, c.instagram
    if not await panel.activo(c):
        log.info("Aviso para %s, que está en pausa desde su panel; no se contesta", nombre(c))
        return
    for change in entry.get("changes", []):
        if change.get("field") != "comments":
            continue
        value = change.get("value") or {}
        log.info("Comentario de %s: %r", (value.get("from") or {}).get("username"), value.get("text", "")[:80])
        if google and comentarios.available() or c.hide_spam or c.imagenes_mes:
            in_background(handle_comment(value, de_pagina=bool(value.pop("pagina", False))))
        else:
            log.info("Comentarios sin configurar (falta SHEET_COMMENTS o Google); se ignora")
    for event in entry.get("messaging", []):
        message = event.get("message")
        if postback := event.get("postback"):
            # Tocaron un botón de inicio: se contesta como si hubiera escrito la pregunta.
            message = {"mid": postback.get("mid") or f"pb-{event.get('timestamp')}",
                       "text": postback.get("title", "")}
        ref = (message or {}).get("referral") or event.get("referral") or {}
        if (ref.get("source") or "").upper() == "ADS" and ref.get("ad_id") and not admin.is_admin(
                event.get("sender", {}).get("id", "")):
            # Llegó por un anuncio de "Enviar mensaje": se guarda cuál, para verlo en Conversaciones.
            datos = ref.get("ads_context_data") or {}
            await store.guardar_anuncio(event["sender"]["id"], str(ref["ad_id"]), datos.get("ad_title") or "",
                                        datos.get("photo_url") or "")
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
        log.info("DM de %s (%s)", user_id, nombre(c))
        text = message_text(message)
        if (url := historias.url_de_mencion(message)) is not None:
            # Lo mencionaron en una historia: se describe la imagen (tarda unos segundos) y luego entra a la cola.
            in_background(entrar_mencion(c, user_id, message["mid"], url))
            continue
        if story := ((message.get("reply_to") or {}).get("story") or {}).get("id"):
            # Respondió a una historia: el asistente debe saber cuál era para contestar sobre esa.
            text = await historias.respuesta(c, user_id, str(story), message.get("text", ""))
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
            dispatchers[c.slug].notify(user_id)
