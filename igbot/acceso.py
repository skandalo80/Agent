"""Entrar al panel con correo (enlace de un solo uso) o con Google.

La pantalla de entrar ya no ofrece Instagram: en un iPhone con la app de
Instagram, los enlaces de instagram.com los abre la app y la pantalla de
permisos se queda en "Loading". Quedan correo, Google y Facebook.

- Correo de una cuenta (el de los avisos de Tu equipo o el que dejó al
  conectarse): le llega un enlace que dura 15 minutos y entra a su panel.
- Correo nuevo, o Google con un correo que no es de ninguna cuenta: tras
  comprobarlo, el paso "Enlaza tu Instagram" (con Instagram o Facebook). Al
  volver al panel, ese correo queda como el de su cuenta y la próxima vez entra
  directo. Instagram no se puede enlazar solo con un correo: la persona tiene
  que dar permiso una vez.
- Apple: igual que Google (solo si están las llaves APPLE_* en .env).
- Facebook (facebook.py) ya trae el Instagram enlazado a su página.

El enlace abre una página con un botón: algunos correos abren los enlaces solos
para revisarlos, y así no lo gastan antes que la persona.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import urlencode

from . import config, panel
from .cuenta import Cuenta

log = logging.getLogger("igbot.acceso")
router = APIRouter(prefix="/panel/correo")

# Lo pone app.py al arrancar: todas las cuentas cargadas, y la base principal (enlaces ya usados).
todas: Callable[[], list[Cuenta]] = lambda: []
store = None

VIGENCIA = 900
CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.I)
MAX_POR_CORREO = 3   # enlaces por correo por hora
MAX_POR_HORA = 30    # en total, contra abusos
ALTA_VIGENCIA = 3600  # una hora para enlazar su Instagram después de comprobar su correo
COOKIE_ALTA = "correo_alta"
COOKIE_ULTIMO = "ultimo_metodo"

_usados: dict[str, float] = {}       # enlaces ya usados -> cuándo vencen
_enviados: list[tuple[float, str]] = []  # (cuándo, correo)


def _firma(texto: str) -> str:
    clave = hmac.new(config.SESSION_SECRET.encode(), b"panel-correo", hashlib.sha256).digest()
    return hmac.new(clave, texto.encode(), hashlib.sha256).hexdigest()[:32]


def _b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


def _de_b64(texto: str) -> str:
    try:
        return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return ""


def nuevo_enlace(dato: str, ahora: float | None = None) -> str:
    """Enlace firmado de un solo uso. `dato` = "i:<id de Instagram>" (entrar a una cuenta) o
    "c:<correo>" (correo nuevo: después enlaza su Instagram)."""
    base = f"{_b64(dato)}.{int((time.time() if ahora is None else ahora) + VIGENCIA)}.{secrets.token_urlsafe(9)}"
    return f"{base}.{_firma(base)}"


def de_enlace(t: str) -> str | None:
    """Lo que dice el enlace ("i:..." o "c:..."), si es nuestro, no ha vencido y no se ha usado."""
    partes = t.split(".")
    if len(partes) != 4 or not partes[1].isdigit() or t in _usados:
        return None
    if int(partes[1]) < time.time():
        return None
    if not hmac.compare_digest(partes[3], _firma(".".join(partes[:3]))):
        return None
    return _de_b64(partes[0]) or None


async def usar_enlace(t: str) -> bool:
    """Gasta el enlace. False si ya se había gastado (también si fue antes de un reinicio: queda en la base)."""
    from . import unavez
    ahora = time.time()
    for k, vence in list(_usados.items()):
        if vence < ahora:
            del _usados[k]
    if t in _usados:
        return False
    _usados[t] = ahora + VIGENCIA
    return await unavez.gastar(store, "enlace:" + t.rsplit(".", 1)[-1], VIGENCIA)


# --- correo comprobado sin cuenta todavía: se guarda en una cookie firmada hasta que enlaza su Instagram ---

def _sello(correo: str) -> str:
    base = f"{_b64(correo)}.{int(time.time()) + ALTA_VIGENCIA}"
    return f"{base}.{_firma('alta.' + base)}"


def correo_alta(request: Request) -> str:
    """El correo comprobado (por enlace o por Google) de quien todavía no enlaza su Instagram, o ""."""
    partes = request.cookies.get(COOKIE_ALTA, "").split(".")
    if len(partes) != 3 or not partes[1].isdigit() or int(partes[1]) < time.time():
        return ""
    if not hmac.compare_digest(partes[2], _firma("alta." + ".".join(partes[:2]))):
        return ""
    return _de_b64(partes[0])


async def guardar_correo(c: Cuenta, correo: str) -> None:
    """Al enlazar su Instagram: el correo con el que se registró queda como el de su cuenta (si no tenía uno
    propio), para que la próxima vez entre con él."""
    from . import ajustes
    if not correo or (c.owner_email and c.owner_email.lower() != config.MAIL_FROM.lower()):
        return
    datos = json.loads(await c.store.get("ajustes") or "{}")
    datos["owner_email"] = correo
    await c.store.set("ajustes", json.dumps(datos, ensure_ascii=False))
    ajustes.aplicar(c, datos)
    log.info("@%s quedó con el correo con el que se registró", await panel.usuario(c))


def entrar_a(c: Cuenta, metodo: str) -> RedirectResponse:
    r = RedirectResponse("/panel", status_code=303)
    panel.iniciar_sesion(r, c.ig_account_id)
    ultimo(r, metodo)
    return r


def ultimo(r, metodo: str) -> None:
    """Recuerda con qué entró (la etiqueta "Último usado" de la pantalla de entrar)."""
    r.set_cookie(COOKIE_ULTIMO, metodo, max_age=365 * 86400, path="/panel", secure=True, httponly=True,
                 samesite="lax")


def a_enlazar(correo: str) -> RedirectResponse:
    r = RedirectResponse("/panel/correo/conecta", status_code=303)
    r.set_cookie(COOKIE_ALTA, _sello(correo), max_age=ALTA_VIGENCIA, path="/panel", secure=True, httponly=True,
                 samesite="lax")
    return r


def cuenta_de_correo(correo: str) -> Cuenta | None:
    """La cuenta con ese correo. Una cuenta de cliente sin correo propio tiene el tuyo por omisión;
    esa no cuenta (si no, tu correo entraría a la cuenta del cliente)."""
    correo = correo.strip().lower()
    for c in todas():
        propio = c.owner_email.strip().lower()
        if c.ig_account_id and propio == correo and (not c.slug or propio != config.MAIL_FROM.lower()):
            return c
    return None


def _puede_enviar(correo: str) -> bool:
    ahora = time.time()
    _enviados[:] = [(t, c) for t, c in _enviados if ahora - t < 3600]
    if len(_enviados) >= MAX_POR_HORA or sum(c == correo for _, c in _enviados) >= MAX_POR_CORREO:
        return False
    _enviados.append((ahora, correo))
    return True


def pagina(titulo: str, bajada: str, cuerpo: str = "") -> HTMLResponse:
    return panel.pagina(
        panel.cabecera()
        + '<section class="oscuro entrar"><div class="contenedor centro">'
        f'<h1>{titulo}<span class="punto">.</span></h1>'
        f'<p class="bajada">{bajada}</p>{cuerpo}'
        "</div></section>")


@router.get("")
def ver():
    return RedirectResponse("/panel", status_code=303)


@router.post("")
async def pedir(request: Request):
    panel.de_aqui(request)
    correo = str((await request.form()).get("correo", "")).strip().lower()
    if not CORREO.match(correo):
        return panel.entrar(request, "Revisa el correo: parece que le falta algo.")
    c = cuenta_de_correo(correo)
    from . import conectar  # conectar usa panel; aquí ya está cargado
    if conectar.google and _puede_enviar(correo):
        dato = f"i:{c.ig_account_id}" if c else f"c:{correo}"
        enlace = f"{config.PUBLIC_URL}/panel/correo/entrar?t={nuevo_enlace(dato)}"
        texto = ("Este es tu enlace para entrar a tu panel" if c else
                 "Este es tu enlace para crear tu cuenta. Después solo enlazas el Instagram de tu negocio")
        try:
            await conectar.google.send_email(
                config.MAIL_FROM, "Skandar", correo, "",
                "Tu enlace para entrar a Skandar" if c else "Tu enlace para crear tu cuenta en Skandar",
                f"Hola,\n\n{texto}:\n\n{enlace}\n\n"
                "Sirve una sola vez y dura 15 minutos. Si no lo pediste, ignora este correo.\n",
            )
            log.info("Enlace por correo enviado (%s)", "entrar" if c else "cuenta nueva")
        except Exception:
            log.exception("No se pudo mandar el enlace por correo")
    # Siempre lo mismo: no se dice qué correos están registrados.
    return pagina("Revisa tu correo",
                  f"Te mandamos un enlace a {escape(correo)}. Ábrelo en este mismo celular o computadora;"
                  " dura 15 minutos.",
                  '<p class="nota">¿No llegó? Revisa en spam o <a class="enlace" href="/panel">'
                  "pide otro ›</a></p>")


def vencido() -> HTMLResponse:
    return pagina("Este enlace ya no sirve",
                  "Ya se usó o pasaron más de 15 minutos. Pide otro y entra con el nuevo.",
                  '<div class="acciones"><a class="boton" href="/panel">Pedir otro enlace</a></div>')


@router.get("/entrar")
def confirmar(t: str = ""):
    # Abrir el enlace no lo gasta: algunos correos abren los enlaces solos para revisarlos.
    if not (dato := de_enlace(t)):
        return vencido()
    nueva = dato.startswith("c:")
    return pagina("Crea tu cuenta" if nueva else "Entra a tu panel",
                  "Toca el botón para seguir. El enlace sirve una sola vez.",
                  '<form method="post" action="/panel/correo/entrar">'
                  f'<input type="hidden" name="t" value="{escape(t)}">'
                  f'<div class="acciones"><button class="boton" type="submit">{"Seguir" if nueva else "Entrar"}'
                  "</button></div></form>")


@router.post("/entrar")
async def entrar(request: Request):
    panel.de_aqui(request)
    t = str((await request.form()).get("t", ""))
    dato = de_enlace(t) or ""
    tipo, _, valor = dato.partition(":")
    if tipo == "i" and (c := panel.buscar(valor)):
        if not await usar_enlace(t):
            return vencido()
        return entrar_a(c, "correo")
    if tipo == "c" and CORREO.match(valor):
        if not await usar_enlace(t):
            return vencido()
        if c := cuenta_de_correo(valor):  # ya enlazó su Instagram en otra ventana
            return entrar_a(c, "correo")
        r = a_enlazar(valor)
        ultimo(r, "correo")
        return r
    return vencido()


@router.get("/conecta")
def conecta(request: Request):
    """Correo ya comprobado: falta enlazar su Instagram (con Instagram o con la página de Facebook)."""
    if not (correo := correo_alta(request)):
        return RedirectResponse("/panel", status_code=303)
    from . import facebook
    # Como Manychat ("Connect via Meta"): el login de Meta para empresas se queda en Safari y deja entrar con la
    # cuenta de Instagram o la de Facebook. La pantalla de Instagram (www.instagram.com/oauth) en el iPhone la abre
    # la app de Instagram y se queda en "Loading" (enlace universal), así que ahí no se ofrece.
    iphone = any(x in request.headers.get("user-agent", "") for x in ("iPhone", "iPad", "iPod"))
    meta = ('<a class="boton" href="/panel/facebook/entrar?solo=ig">Conectar con Meta</a>' if facebook.enabled()
            else "")
    solo_ig = ("" if iphone else
               f'<a class="boton{" secundario" if meta else ""}" href="/conectar/instagram">Solo con Instagram</a>')
    return pagina(
        "Conecta tu Instagram",
        f"Ya comprobamos {escape(correo)}. Último paso: conecta el Instagram de tu negocio para que tu asistente"
        " pueda contestar por ti.",
        f'<div class="acciones">{meta}{solo_ig}</div>'
        '<p class="nota">Con Meta entras con tu Instagram o tu Facebook, eliges tu cuenta de Instagram y das los'
        " permisos. No necesitas página de Facebook.</p>")


# --- entrar con Google: solo el correo (openid email), con el mismo cliente web de Google Calendar ---

GOOGLE_COOKIE = "google_entrar"


@router.get("/google")
def con_google():
    from . import agenda_google as g
    if not g.enabled():
        return RedirectResponse("/panel", status_code=303)
    base = f"entrar.{int(time.time())}.{secrets.token_urlsafe(12)}"
    state = f"{base}.{_firma(base)}"
    url = g.AUTORIZAR + "?" + urlencode({
        "client_id": config.GOOGLE_WEB_CLIENT_ID, "redirect_uri": g.redirect_uri(), "response_type": "code",
        "scope": "openid email", "prompt": "select_account", "state": state})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(GOOGLE_COOKIE, state, max_age=600, path="/panel/google", secure=True, httponly=True,
                 samesite="lax")
    return r


def es_de_entrar(state: str) -> bool:
    return state.startswith("entrar.")


async def vuelta_google(request: Request):
    """La vuelta de Google (/panel/google/callback) cuando el state es de entrar, no de conectar el calendario."""
    from . import agenda_google as g
    p = dict(request.query_params)
    state, cookie = p.get("state", ""), request.cookies.get(GOOGLE_COOKIE, "")
    partes = state.split(".")
    if not p.get("code"):
        return RedirectResponse("/panel", status_code=303)
    if (len(partes) != 4 or not cookie or not hmac.compare_digest(cookie, state) or not partes[1].isdigit()
            or not 0 <= time.time() - int(partes[1]) < 600
            or not hmac.compare_digest(partes[3], _firma(".".join(partes[:3])))):
        log.warning("Vuelta de Google para entrar con state inválido")
        return panel.entrar(request, "No pudimos entrar con Google. Intenta de nuevo.")
    try:
        correo = g.correo_de((await g.cambiar_codigo(p["code"])).get("id_token", "")).strip().lower()
    except Exception:
        log.exception("No se pudo entrar con Google")
        correo = ""
    if not CORREO.match(correo):
        return panel.entrar(request, "No pudimos entrar con Google. Intenta de nuevo.")
    r = entrar_a(c, "google") if (c := cuenta_de_correo(correo)) else a_enlazar(correo)
    ultimo(r, "google")
    r.delete_cookie(GOOGLE_COOKIE, path="/panel/google")
    return r


# --- entrar con Apple (Sign in with Apple, solo el correo). Apple vuelve con un POST desde appleid.apple.com,
# así que la cookie del state va con SameSite=None. ---

APPLE = "https://appleid.apple.com"
APPLE_COOKIE = "apple_entrar"


def apple_activo() -> bool:
    return bool(config.APPLE_SERVICES_ID and config.APPLE_TEAM_ID and config.APPLE_KEY_ID and config.APPLE_KEY_PATH
                and config.PUBLIC_URL)


def _apple_vuelta() -> str:
    return f"{config.PUBLIC_URL.rstrip('/')}/panel/correo/apple/vuelta"


def apple_secreto(ahora: float | None = None) -> str:
    """El client_secret de Apple: un JWT firmado (ES256) con la llave .p8, válido 5 minutos."""
    import jwt
    from pathlib import Path
    ahora = int(time.time() if ahora is None else ahora)
    return jwt.encode({"iss": config.APPLE_TEAM_ID, "iat": ahora, "exp": ahora + 300, "aud": APPLE,
                       "sub": config.APPLE_SERVICES_ID},
                      Path(config.APPLE_KEY_PATH).read_text(), algorithm="ES256",
                      headers={"kid": config.APPLE_KEY_ID})


def _correo_del_token(id_token: str) -> str:
    """Llega directo de Apple por HTTPS al cambiar el código: no hace falta revisar su firma."""
    try:
        cuerpo = id_token.split(".")[1]
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (IndexError, ValueError):
        return ""
    ok = str(datos.get("email_verified", "true")).lower() == "true"
    return str(datos.get("email", ""))[:200].strip().lower() if ok else ""


@router.get("/apple")
def con_apple():
    if not apple_activo():
        return RedirectResponse("/panel", status_code=303)
    base = f"apple.{int(time.time())}.{secrets.token_urlsafe(12)}"
    state = f"{base}.{_firma(base)}"
    url = f"{APPLE}/auth/authorize?" + urlencode({
        "client_id": config.APPLE_SERVICES_ID, "redirect_uri": _apple_vuelta(), "response_type": "code",
        "response_mode": "form_post", "scope": "email", "state": state})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(APPLE_COOKIE, state, max_age=600, path="/panel/correo/apple", secure=True, httponly=True,
                 samesite="none")
    return r


@router.post("/apple/vuelta")
async def vuelta_apple(request: Request):
    f = await request.form()
    code, state = str(f.get("code", "")), str(f.get("state", ""))
    cookie = request.cookies.get(APPLE_COOKIE, "")
    partes = state.split(".")
    if not code:  # canceló
        return RedirectResponse("/panel", status_code=303)
    if (not apple_activo() or len(partes) != 4 or not cookie or not hmac.compare_digest(cookie, state)
            or not partes[1].isdigit() or not 0 <= time.time() - int(partes[1]) < 600
            or not hmac.compare_digest(partes[3], _firma(".".join(partes[:3])))):
        log.warning("Vuelta de Apple con state inválido")
        return panel.entrar(request, "No pudimos entrar con Apple. Intenta de nuevo.")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(f"{APPLE}/auth/token", data={
                "client_id": config.APPLE_SERVICES_ID, "client_secret": apple_secreto(), "code": code,
                "grant_type": "authorization_code", "redirect_uri": _apple_vuelta()})
        if r.is_error:
            log.error("Apple respondió %s al cambiar el código: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        correo = _correo_del_token(r.json().get("id_token", ""))
    except Exception:
        log.exception("No se pudo entrar con Apple")
        correo = ""
    if not CORREO.match(correo):
        return panel.entrar(request, "No pudimos entrar con Apple. Intenta de nuevo.")
    resp = entrar_a(c, "apple") if (c := cuenta_de_correo(correo)) else a_enlazar(correo)
    ultimo(resp, "apple")
    resp.delete_cookie(APPLE_COOKIE, path="/panel/correo/apple", secure=True, httponly=True, samesite="none")
    return resp
