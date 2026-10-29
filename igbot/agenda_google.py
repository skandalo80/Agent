"""Conecta tu Google Calendar: las citas que agenda el asistente van al calendario del cliente.

Antes solo había un Google (el tuyo, GOOGLE_REFRESH_TOKEN en .env) y solo la cuenta principal
agendaba. Aquí cada cuenta da permiso a la app de Skandar con el correo de su negocio (OAuth de
Google) y desde entonces el asistente:

1. Revisa los huecos libres de SU calendario dentro de su horario de atención (Configura tu asistente).
2. Crea la cita en su calendario: quien organiza y manda la invitación es el correo del negocio.
3. Lista y cancela solo las citas de la persona del chat (las reglas siguen en tools.py).

Permisos: calendar.events (crear y cancelar sus eventos) y calendar.freebusy (ver si está libre, sin
leer los eventos), más openid y email para saber con qué correo se conectó. No manda correos: la
invitación la manda Google Calendar.

Va con un cliente OAuth tipo "Aplicación web" del mismo proyecto de Google Cloud (GOOGLE_WEB_CLIENT_ID y
GOOGLE_WEB_CLIENT_SECRET), con {PUBLIC_URL}/panel/google/callback en sus URI de redireccionamiento. No es
el de GOOGLE_CLIENT_ID: ese es de escritorio (el permiso de tu Gmail) y no acepta URI.

kv "google_calendar": {"email", "refresh_token", "permisos", "fecha", "tipo" (presencial o video),
"minutos", "revocado"}.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime
from html import escape
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, panel
from .cuenta import Cuenta
from .google import Google

log = logging.getLogger("igbot.agenda_google")

router = APIRouter(prefix="/panel/google")

CLAVE = "google_calendar"
EVENTOS = "https://www.googleapis.com/auth/calendar.events"
LIBRE = "https://www.googleapis.com/auth/calendar.freebusy"
SCOPES = f"openid email {EVENTOS} {LIBRE}"
AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOCAR = "https://oauth2.googleapis.com/revoke"
STATE_COOKIE = "google_state"
STATE_TTL = 900
TIPOS = {"presencial": "En tu negocio", "video": "Videollamada (Google Meet)"}
MINUTOS = (15, 30, 45, 60, 90)
_transporte: httpx.AsyncBaseTransport | None = None  # las pruebas ponen uno falso


def enabled() -> bool:
    return bool(config.GOOGLE_WEB_CLIENT_ID and config.GOOGLE_WEB_CLIENT_SECRET and config.PUBLIC_URL)


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/panel/google/callback"


async def conexion(c: Cuenta) -> dict:
    """El calendario conectado ({} si no hay)."""
    return json.loads(await c.store.get(CLAVE) or "{}")


async def guardar(c: Cuenta, datos: dict) -> None:
    await c.store.set(CLAVE, json.dumps(datos, ensure_ascii=False) if datos else "")
    _calendarios.pop(c.slug, None)


# --- el calendario para el asistente ---

class SinPermiso(Exception):
    """El cliente quitó el permiso en su cuenta de Google."""


class Calendario(Google):
    """El Google de una cuenta: si Google ya no acepta su permiso, se marca para pedir reconectar."""

    def __init__(self, c: Cuenta, con: dict):
        super().__init__(config.GOOGLE_WEB_CLIENT_ID, config.GOOGLE_WEB_CLIENT_SECRET, con["refresh_token"])
        self.cuenta = c

    async def token(self) -> str:
        from google.auth.exceptions import RefreshError

        try:
            return await super().token()
        except RefreshError:
            con = await conexion(self.cuenta)
            if con:
                await guardar(self.cuenta, {**con, "revocado": True})
            log.warning("@%s quitó el permiso de su Google Calendar", self.cuenta.slug or "principal")
            raise SinPermiso("El calendario ya no está conectado.")


_calendarios: dict[str, Calendario] = {}


async def calendario(c: Cuenta) -> tuple[Calendario | None, dict]:
    """(el calendario de la cuenta o None, su conexión) para armar las herramientas del asistente."""
    con = await conexion(c)
    if not enabled() or not con.get("refresh_token") or con.get("revocado"):
        return None, con
    if c.slug not in _calendarios:
        _calendarios[c.slug] = Calendario(c, con)
    return _calendarios[c.slug], con


PROMPT = (
    "<agenda>\nPuedes agendar {que} en el calendario del negocio ({minutos} min). Pregunta qué día le"
    " acomoda, revisa con `ver_disponibilidad`, ofrece 2 o 3 horarios y confirma fecha, hora y nombre antes de"
    " usar `agendar_reunion`. {correo} La hora que muestres es la del negocio.\n</agenda>"
)


def prompt(con: dict) -> str:
    presencial = con.get("tipo", "presencial") == "presencial"
    return PROMPT.format(
        que="citas en el negocio" if presencial else "videollamadas",
        minutos=con.get("minutos") or 30,
        correo="Pide su correo solo si quiere recibir la invitación." if presencial
        else "Pide su correo: ahí le llega la invitación con el enlace de Meet.")


# --- conectar (OAuth de Google) ---

def _firmar(texto: str) -> str:
    return hmac.new(config.GOOGLE_WEB_CLIENT_SECRET.encode(), texto.encode(), hashlib.sha256).hexdigest()


def nuevo_state(c: Cuenta) -> str:
    base = f"{c.slug or '-'}.{int(time.time())}.{secrets.token_urlsafe(12)}"
    return f"{base}.{_firmar(base)[:32]}"


def state_valido(state: str, cookie: str | None, c: Cuenta) -> bool:
    partes = state.split(".")
    if len(partes) != 4 or not cookie or not secrets.compare_digest(cookie, state):
        return False
    slug, ts, nonce, firma = partes
    return (slug == (c.slug or "-") and ts.isdigit() and 0 <= time.time() - int(ts) < STATE_TTL
            and secrets.compare_digest(firma, _firmar(f"{slug}.{ts}.{nonce}")[:32]))


_Cliente = httpx.AsyncClient  # el de verdad, aunque las pruebas de otras partes cambien httpx.AsyncClient


def cliente() -> httpx.AsyncClient:
    return _Cliente(timeout=20, transport=_transporte)


def correo_de(id_token: str) -> str:
    """El correo del id_token. Llega directo de Google por HTTPS al cambiar el código, así que no hace
    falta revisar su firma (así lo dice Google para este caso)."""
    try:
        cuerpo = id_token.split(".")[1]
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (IndexError, ValueError):
        return ""
    return str(datos.get("email", ""))[:200] if datos.get("email_verified", True) else ""


async def cambiar_codigo(code: str) -> dict:
    async with cliente() as http:
        r = await http.post(TOKEN, data={
            "code": code, "client_id": config.GOOGLE_WEB_CLIENT_ID,
            "client_secret": config.GOOGLE_WEB_CLIENT_SECRET, "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code"})
    if r.is_error:
        log.error("Google respondió %s al cambiar el código: %s", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json()


async def revocar(token: str) -> None:
    try:
        async with cliente() as http:
            await http.post(REVOCAR, data={"token": token})
    except httpx.HTTPError:
        log.warning("No se pudo revocar el permiso de Google")


AVISOS = {
    "conectado": "Listo: conectamos tu calendario. Tu asistente ya puede agendar citas en él.",
    "cancelado": "No se conectó tu calendario. Puedes intentarlo de nuevo cuando quieras.",
    "fallo": "No pudimos conectar tu calendario esta vez. Intenta de nuevo en un momento.",
    "permisos": "Falta un permiso: al conectar, deja marcadas las casillas de ver y editar tu calendario.",
    "desconectado": "Desconectamos tu calendario. Tu asistente ya no agenda citas.",
    "guardado": "Guardado: tu asistente agenda así desde ahora.",
}


@router.get("")
async def ver(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    return await pagina(c, request.query_params.get("aviso", ""))


def _opciones(con: dict) -> str:
    tipo, minutos = con.get("tipo", "presencial"), con.get("minutos") or 30
    return ('<form method="post" action="/panel/google/guardar" class="ajustes-agenda">'
            '<label>Tus citas son</label><div class="opciones">'
            + "".join(f'<label class="opcion"><input type="radio" name="tipo" value="{k}"'
                      f'{" checked" if k == tipo else ""}><b>{v}</b></label>' for k, v in TIPOS.items())
            + '</div><label for="minutos">Cada cita dura</label><select id="minutos" name="minutos">'
            + "".join(f'<option value="{m}"{" selected" if m == minutos else ""}>{m} minutos</option>'
                      for m in MINUTOS)
            + '</select><p class="ayuda">Tu asistente solo ofrece horarios dentro de tu horario de atención'
            ' (Configura tu asistente, Tu equipo) y donde tu calendario esté libre.</p>'
            '<div class="acciones"><button class="boton">Guardar</button></div></form>')


async def pagina(c: Cuenta, aviso: str = ""):
    con = await conexion(c)
    if not enabled():
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu Google Calendar</h2><p class="ayuda">Muy pronto vas a poder'
                  " conectar tu calendario desde aquí.</p></div>")
    elif con.get("refresh_token"):
        desde = datetime.fromtimestamp(con.get("fecha", 0)).strftime("%d/%m/%Y")
        cuerpo = ('<div class="tarjeta"><h2>Tu Google Calendar está conectado</h2>'
                  f'<p class="ayuda"><b>{escape(con.get("email") or "Tu cuenta de Google")}</b> · desde el {desde}</p>'
                  + ('<p class="aviso">Google ya no acepta el permiso (lo quitaste o cambiaste tu contraseña).'
                     " Vuelve a conectar tu calendario para que tu asistente siga agendando.</p>"
                     '<form method="post" action="/panel/google/conectar" class="acciones">'
                     '<button class="boton">Volver a conectar</button></form>' if con.get("revocado") else "")
                  + '<ul class="lista-shopify"><li>Tu asistente ve en qué horarios estás libre, sin leer tus eventos.</li>'
                  "<li>Las citas se crean en tu calendario: tú las organizas y la invitación sale de tu correo.</li>"
                  "<li>Cada persona solo puede ver y cancelar sus propias citas.</li></ul>"
                  + _opciones(con)
                  + '<form method="post" action="/panel/google/desconectar" class="acciones">'
                  '<button class="boton secundario chico">Desconectar tu calendario</button></form></div>')
    else:
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu Google Calendar</h2>'
                  '<p class="ayuda">Con tu permiso, tu asistente agenda citas en tu calendario desde el chat: revisa'
                  " en qué horarios estás libre, ofrece opciones y crea la cita con el correo de tu negocio. No lee"
                  " tus eventos ni manda correos por ti.</p>"
                  '<form method="post" action="/panel/google/conectar">'
                  '<div class="acciones"><button class="boton">Conectar Google Calendar</button></div>'
                  '<p class="ayuda">Te llevamos a Google para que elijas la cuenta de tu negocio y des el permiso;'
                  " al aceptar vuelves aquí.</p></form></div>")
    aviso_html = f'<div class="aviso">{escape(AVISOS[aviso])}</div>' if aviso in AVISOS else ""
    return panel.pagina(
        await panel.menu_de(c, "cuenta", sub="conexion")
        + f'<main class="contenido">{panel.encabezado("Tu calendario")}{aviso_html}{cuerpo}</main>')


@router.post("/conectar")
async def conectar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if not enabled():
        return RedirectResponse("/panel/google", status_code=303)
    state = nuevo_state(c)
    url = AUTORIZAR + "?" + urlencode({
        "client_id": config.GOOGLE_WEB_CLIENT_ID, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent select_account",
        "include_granted_scopes": "true", "state": state})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, path="/panel/google", secure=True, httponly=True,
                 samesite="lax")
    return r


@router.get("/callback")
async def callback(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    p = dict(request.query_params)
    if not p.get("code"):
        return _volver("cancelado")
    if not (enabled() and state_valido(p.get("state", ""), request.cookies.get(STATE_COOKIE), c)):
        log.warning("Vuelta de Google con state inválido")
        return _volver("fallo")
    try:
        datos = await cambiar_codigo(p["code"])
    except Exception:
        log.exception("No se pudo conectar el Google Calendar de @%s", c.slug or "principal")
        return _volver("fallo")
    permisos = set(str(datos.get("scope", "")).split())
    if not datos.get("refresh_token") or not {EVENTOS, LIBRE} <= permisos:
        # Google deja desmarcar permisos al aceptar; sin los dos el asistente no puede agendar.
        if datos.get("access_token"):
            await revocar(datos["access_token"])
        return _volver("permisos")
    antes = await conexion(c)
    await guardar(c, {"email": correo_de(datos.get("id_token", "")), "refresh_token": datos["refresh_token"],
                      "permisos": " ".join(sorted(permisos)), "fecha": int(time.time()),
                      "tipo": antes.get("tipo", "presencial"), "minutos": antes.get("minutos") or 30})
    log.info("@%s conectó su Google Calendar", await panel.usuario(c))
    return _volver("conectado")


def _volver(aviso: str) -> RedirectResponse:
    r = RedirectResponse(f"/panel/google?aviso={aviso}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/panel/google")
    return r


@router.post("/guardar")
async def guardar_opciones(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    f = await panel.form(request)
    con = await conexion(c)
    if con:
        minutos = int(f["minutos"]) if f.get("minutos", "").isdigit() and int(f["minutos"]) in MINUTOS else 30
        await guardar(c, {**con, "tipo": f.get("tipo") if f.get("tipo") in TIPOS else "presencial",
                          "minutos": minutos})
    return RedirectResponse("/panel/google?aviso=guardado", status_code=303)


@router.post("/desconectar")
async def desconectar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if token := (await conexion(c)).get("refresh_token"):
        await revocar(token)
    await guardar(c, {})
    return RedirectResponse("/panel/google?aviso=desconectado", status_code=303)
