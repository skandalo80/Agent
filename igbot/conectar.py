"""Conectar el Instagram de un cliente sin pasarle tokens a mano.

Mientras la app de Meta no tenga App Review, solo pueden conectarse las cuentas
invitadas como Instagram Tester (eso se hace a mano en el panel de Meta). La
página skandar.online/conectar guía al cliente:
1. Deja su @ en un formulario (POST /conectar/solicitud) y al dueño le llega
   un aviso para invitarlo como tester.
2. Acepta la invitación en Instagram.
3. Pulsa "Conectar tu Instagram" (GET /conectar/instagram): inicia sesión en
   Instagram, autoriza la app y vuelve a /conectar/callback, donde el código
   se cambia por un token de 60 días que se guarda en la tabla `conexiones`.
Con ese token su cuenta se da de alta sola, en pausa, y el cliente entra a su
panel (/panel, ver panel.py) para configurar y encender su asistente. Iniciar
sesión en el panel es este mismo flujo.
"""
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, panel
from .cuenta import cuenta
from .google import Google
from .instagram import GRAPH, Instagram
from .store import Store

log = logging.getLogger("igbot.conectar")

router = APIRouter(prefix="/conectar")
# Los pone app.py al arrancar.
store: Store | None = None
instagram: Instagram | None = None
google: Google | None = None

AUTHORIZE = "https://www.instagram.com/oauth/authorize"
TOKEN = "https://api.instagram.com/oauth/access_token"
SCOPES = ",".join([
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
])
STATE_COOKIE = "conectar_state"
USUARIO = re.compile(r"^[a-z0-9._]{1,30}$")
CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.I)
MAX_SOLICITUDES_POR_HORA = 20  # contra bots que llenen el formulario


def enabled() -> bool:
    return bool(config.IG_APP_ID and config.PUBLIC_URL)


def redirect_uri() -> str:
    # Debe ser idéntica a la que se registra en el panel de Meta.
    return f"{config.PUBLIC_URL}/conectar/callback"


def back(path: str) -> RedirectResponse:
    """De vuelta al sitio (303 para que el navegador cambie el POST por GET)."""
    return RedirectResponse(f"{config.SITE_URL}{path}", status_code=303)


async def avisar(asunto: str, texto: str) -> None:
    """Al dueño por DM (si escribió hace menos de 24 h) y por correo."""
    for admin_id in cuenta().admin_ig_ids:
        try:
            await instagram.send_text(admin_id, texto)
        except Exception:
            log.warning("No se pudo avisar por DM a %s (¿pasaron 24 h sin escribirle al bot?)", admin_id)
    if google and cuenta().owner_email:
        try:
            await google.send_email(config.MAIL_FROM, cuenta().mail_from_name, cuenta().owner_email, "", asunto, texto)
        except Exception:
            log.exception("No se pudo avisar por correo")


def clean_usuario(text: str) -> str:
    text = text.strip().lower().removeprefix("@")
    # También aceptan que peguen el enlace a su perfil.
    if m := re.search(r"instagram\.com/([a-z0-9._]+)", text):
        text = m.group(1)
    return text


@router.post("/solicitud")
async def solicitud(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}
    if form.get("sitio_web"):  # campo oculto: solo lo llenan los bots
        return back("/conectar?enviado=1#paso-2")
    usuario = clean_usuario(form.get("usuario", ""))
    nombre = form.get("nombre", "").strip()[:80]
    correo = form.get("correo", "").strip()[:254]
    if not USUARIO.match(usuario) or not nombre or not CORREO.match(correo):
        return back("/conectar?error=datos#paso-1")
    if await store.solicitudes_since(time.time() - 3600) >= MAX_SOLICITUDES_POR_HORA:
        log.warning("Demasiadas solicitudes en la última hora; se ignora @%s", usuario)
        return back("/conectar?enviado=1#paso-2")
    await store.add_solicitud(usuario, nombre, correo)
    log.info("Solicitud de conexión de @%s", usuario)
    await avisar(
        f"Nuevo cliente por conectar: @{usuario}",
        f"{nombre} (@{usuario}, {correo}) quiere conectar su Instagram.\n"
        f"Invítalo como tester: panel de Meta > Roles de la app > Instagram Testers > agrega {usuario}.",
    )
    return back("/conectar?enviado=1#paso-2")


@router.get("/instagram")
def start():
    """Manda al cliente a iniciar sesión en Instagram y autorizar la app."""
    if not enabled():
        return back("/conectar-error")
    state = secrets.token_urlsafe(24)
    url = AUTHORIZE + "?" + urlencode({
        "client_id": config.IG_APP_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "enable_fb_login": "0",
        "force_authentication": "1",
    })
    response = RedirectResponse(url, status_code=303)
    # Se compara al volver para que nadie conecte una cuenta con un enlace armado.
    response.set_cookie(STATE_COOKIE, state, max_age=900, path="/conectar",
                        secure=True, httponly=True, samesite="lax")
    return response


def check(r: httpx.Response) -> None:
    """Como raise_for_status, pero deja en el log lo que contestó Instagram (dice qué falló)."""
    if r.is_error:
        log.error("Instagram respondió %s en %s: %s", r.status_code, r.url.path, r.text[:500])
    r.raise_for_status()


async def exchange(code: str) -> tuple[str, float]:
    """Código de un solo uso -> token corto -> token de 60 días."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(TOKEN, data={
            "client_id": config.IG_APP_ID,
            "client_secret": config.IG_OAUTH_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
            "code": code,
        })
        check(r)
        data = r.json()
        short = (data.get("data") or [data])[0]["access_token"]
        r = await http.get(f"{GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token",
            "client_secret": config.IG_OAUTH_SECRET,
            "access_token": short,
        })
        check(r)
        data = r.json()
        return data["access_token"], time.time() + float(data.get("expires_in", 60 * 24 * 3600))


async def whoami(token: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{GRAPH}/{config.API_VERSION}/me",
                           params={"fields": "user_id,username", "access_token": token})
        check(r)
        data = r.json()
        return str(data["user_id"]), data["username"]


@router.get("/callback")
async def callback(request: Request):
    p = request.query_params
    expected = request.cookies.get(STATE_COOKIE)
    if p.get("error") or not p.get("code"):
        # Canceló, o su cuenta no es tester todavía.
        log.info("Conexión cancelada o rechazada: %s", p.get("error_reason") or p.get("error"))
        return back("/conectar-error")
    if not expected or not secrets.compare_digest(expected, p.get("state", "")):
        log.warning("Callback con state inválido")
        return back("/conectar-error")
    try:
        token, expires_at = await exchange(p["code"].removesuffix("#_"))
        ig_id, usuario = await whoami(token)
    except Exception:
        log.exception("No se pudo cambiar el código por un token")
        return back("/conectar-error")
    nueva = await store.save_conexion(ig_id, usuario, token, expires_at)
    log.info("Cuenta conectada: @%s (%s)", usuario, ig_id)
    try:
        c = await panel.alta(ig_id, usuario, token) if panel.alta else None
    except Exception:
        log.exception("No se pudo dar de alta a @%s; se hace a mano con scripts/alta_cliente.py", usuario)
        c = None
    if nueva:
        await avisar(
            f"Instagram conectado: @{usuario}",
            f"@{usuario} conectó su Instagram"
            + (". Su asistente quedó en pausa hasta que lo encienda desde su panel." if c else
               ". Dale de alta en el servidor con:\n"
               f"python scripts/alta_cliente.py {usuario} <nombre-corto>"),
        )
    if c:
        # Entra directo a su panel para configurar su asistente.
        response = RedirectResponse(f"{config.PUBLIC_URL}/panel", status_code=303)
        panel.iniciar_sesion(response, ig_id)
    else:
        response = back("/conectado")
    response.delete_cookie(STATE_COOKIE, path="/conectar")
    return response
