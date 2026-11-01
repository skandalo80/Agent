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

Si alguien quita la app desde su Instagram, Meta llama a /conectar/desautorizar;
si además pide borrar sus datos, a /conectar/borrar-datos (las dos URL van en el
panel de Meta > Instagram > Configuración del inicio de sesión para empresas).
Se avisa al dueño, que borra a mano, y la persona consulta su solicitud en
/conectar/borrado?codigo=... (Meta le muestra ese enlace).
"""
from html import escape
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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

# En el iPhone, www.instagram.com abre la app de Instagram y la app se queda en "Loading";
# con api.instagram.com la app sí muestra la pantalla de permisos.
AUTHORIZE = "https://api.instagram.com/oauth/authorize"
TOKEN = "https://api.instagram.com/oauth/access_token"
SCOPES = ",".join([
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
])
STATE_COOKIE = "conectar_state"
STATE_TTL = 900
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


def firmar(texto: str) -> str:
    return hmac.new(config.IG_OAUTH_SECRET.encode(), texto.encode(), hashlib.sha256).hexdigest()[:32]


def nuevo_state() -> str:
    base = f"{int(time.time())}.{secrets.token_urlsafe(18)}"
    return f"{base}.{firmar(base)}"


_usados: dict[str, float] = {}  # states ya usados (cada uno sirve una vez)


def state_valido(state: str, cookie: str | None) -> bool:
    """El state vale si es el de la cookie o, si volvió en otro navegador (en el cel, Instagram
    regresa en su propio navegador, sin la cookie de Safari), si lo firmamos hace menos de 15 min."""
    ahora = time.time()
    for s, t in list(_usados.items()):
        if ahora - t > STATE_TTL:
            del _usados[s]
    if not state or state in _usados:
        return False
    ok = bool(cookie) and secrets.compare_digest(cookie, state)
    if not ok:
        partes = state.split(".")
        ok = (len(partes) == 3 and partes[0].isdigit() and 0 <= ahora - int(partes[0]) < STATE_TTL
              and secrets.compare_digest(partes[2], firmar(f"{partes[0]}.{partes[1]}")))
    if ok:
        _usados[state] = ahora
    return ok


def _sin_app(url: str) -> HTMLResponse:
    """En iPhone/iPad: www.instagram.com es un "enlace universal" y, si la navegación viene de un toque, iOS
    la abre en la app de Instagram, que no sabe qué hacer con /oauth/authorize ("Loading" y "Something went
    wrong"). iOS solo abre la app cuando la navegación nace de un toque; un meta refresh no es un toque, así
    que Safari se queda con la página de permisos."""
    destino = escape(url, quote=True)
    return HTMLResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="0;url={destino}"><title>Abriendo Instagram…</title>'
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#2B2C30;color:#F5F5F4;"
        "font:1rem/1.5 system-ui,sans-serif;text-align:center;padding:1.5rem}p{max-width:22rem;color:#A8A8AE}"
        "</style></head><body><div><h1 style=\"font-weight:600\">Abriendo Instagram…</h1>"
        "<p>Si se abre la app de Instagram y se queda cargando, regresa a Safari y usa Enlazar con Facebook.</p>"
        "</div></body></html>")


@router.get("/instagram")
def start(request: Request):
    """Manda al cliente a iniciar sesión en Instagram y autorizar la app."""
    if not enabled():
        return back("/conectar-error")
    state = nuevo_state()
    url = AUTHORIZE + "?" + urlencode({
        "client_id": config.IG_APP_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "enable_fb_login": "0",
        "force_authentication": "1",
    })
    ua = request.headers.get("user-agent", "")
    iphone = any(x in ua for x in ("iPhone", "iPad", "iPod"))
    response = _sin_app(url) if iphone else RedirectResponse(url, status_code=303)
    # Se compara al volver para que nadie conecte una cuenta con un enlace armado.
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, path="/conectar",
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


async def whoami(token: str) -> tuple[str, str, int, int]:
    """Id, usuario, seguidores y publicaciones de la cuenta del token."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{GRAPH}/{config.API_VERSION}/me", params={
            "fields": "user_id,username,followers_count,media_count", "access_token": token})
        check(r)
        data = r.json()
        return (str(data["user_id"]), data["username"],
                int(data.get("followers_count") or 0), int(data.get("media_count") or 0))


@router.get("/callback")
async def callback(request: Request):
    p = request.query_params
    expected = request.cookies.get(STATE_COOKIE)
    if p.get("error") or not p.get("code"):
        # Canceló, o su cuenta no es tester todavía.
        log.info("Conexión cancelada o rechazada: %s", p.get("error_reason") or p.get("error"))
        return back("/conectar-error")
    if not state_valido(p.get("state", ""), expected):
        log.warning("Callback con state inválido")
        return back("/conectar-error")
    try:
        token, expires_at = await exchange(p["code"].removesuffix("#_"))
        ig_id, usuario, seguidores, posts = await whoami(token)
    except Exception:
        log.exception("No se pudo cambiar el código por un token")
        return back("/conectar-error")
    nueva = await store.save_conexion(ig_id, usuario, token, expires_at)
    log.info("Cuenta conectada: @%s (%s), %d seguidores, %d publicaciones", usuario, ig_id, seguidores, posts)
    # Con mucho movimiento no se enciende sola: se revisa antes (costo y carga del servidor).
    grande = seguidores > config.CUENTA_GRANDE_SEGUIDORES or posts > config.CUENTA_GRANDE_POSTS
    try:
        c = await panel.alta(ig_id, usuario, token, grande) if panel.alta else None
    except Exception:
        log.exception("No se pudo dar de alta a @%s; se hace a mano con scripts/alta_cliente.py", usuario)
        c = None
    if nueva:
        await avisar(
            f"Instagram conectado: @{usuario}",
            f"@{usuario} conectó su Instagram ({seguidores:,} seguidores, {posts:,} publicaciones)"
            + (f". Es una cuenta grande: quedó en revisión y no puede encender su asistente. Para"
               f" habilitarla, quita EN_REVISION=1 de clientes/{c.slug}/.env y reinicia." if c and c.en_revision else
               ". Su asistente quedó en pausa hasta que lo encienda desde su panel." if c else
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


# --- la persona quita la app o pide borrar sus datos (callbacks de Meta) ---

def leer_signed_request(valor: str) -> dict | None:
    """El signed_request que manda Meta: firma.datos en base64url, firmado con el secreto de la app."""
    try:
        firma, datos = valor.split(".", 1)
        relleno = lambda t: t + "=" * (-len(t) % 4)  # noqa: E731
        recibida = base64.urlsafe_b64decode(relleno(firma))
        info = json.loads(base64.urlsafe_b64decode(relleno(datos)))
    except Exception:
        return None
    # Igual que el webhook: la app de Instagram puede tener su propio secreto.
    for secreto in {config.APP_SECRET, config.IG_OAUTH_SECRET}:
        esperada = hmac.new(secreto.encode(), datos.encode(), hashlib.sha256).digest()
        if hmac.compare_digest(recibida, esperada) and str(info.get("algorithm", "")).upper() == "HMAC-SHA256":
            return info
    return None


async def _de_meta(request: Request) -> tuple[str, str] | None:
    """(ig_id, "@usuario" o el id si no la conocemos) del signed_request válido, o None."""
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}
    info = leer_signed_request(form.get("signed_request", ""))
    if not info or not info.get("user_id"):
        log.warning("Callback de Meta con signed_request inválido")
        return None
    ig_id = str(info["user_id"])
    con = await store.conexion(ig_id)
    return ig_id, (f"@{con[0]}" + (f" (clientes/{con[1]})" if con[1] else "") if con else f"la cuenta {ig_id}")


@router.post("/desautorizar")
async def desautorizar(request: Request):
    quien = await _de_meta(request)
    if not quien:
        return JSONResponse({"error": "signed_request inválido"}, status_code=400)
    log.info("%s quitó la app desde Instagram", quien[1])
    await avisar(f"Quitaron la app: {quien[1]}",
                 f"{quien[1]} quitó a Skandar de su Instagram. Su token ya no sirve: apaga su asistente.")
    return {"ok": True}


@router.post("/borrar-datos")
async def borrar_datos(request: Request):
    quien = await _de_meta(request)
    if not quien:
        return JSONResponse({"error": "signed_request inválido"}, status_code=400)
    codigo = secrets.token_hex(8)
    await store.set(f"borrado:{codigo}", json.dumps({"ig_id": quien[0], "cuenta": quien[1], "fecha": time.time()}))
    log.info("%s pidió borrar sus datos (código %s)", quien[1], codigo)
    await avisar(f"Piden borrar datos: {quien[1]}",
                 f"{quien[1]} pidió desde Instagram que borremos sus datos (código {codigo}). "
                 "Bórralos en máximo 20 días hábiles, como dice el aviso de privacidad.")
    return {"url": f"{config.PUBLIC_URL}/conectar/borrado?codigo={codigo}", "confirmation_code": codigo}


BORRADO_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Solicitud de borrado · Skandar</title>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5">
<h1>Solicitud de borrado de datos</h1>
{cuerpo}
<p>Dudas: <a href="mailto:hola@skandar.online">hola@skandar.online</a></p>
</body></html>"""


@router.get("/borrado", response_class=HTMLResponse)
async def borrado(codigo: str = ""):
    guardado = await store.get(f"borrado:{codigo}") if re.fullmatch(r"[0-9a-f]{16}", codigo) else None
    if not guardado:
        cuerpo = "<p>No encontramos una solicitud con ese código.</p>"
    else:
        fecha = time.strftime("%d/%m/%Y", time.localtime(json.loads(guardado)["fecha"]))
        cuerpo = (f"<p>Recibimos tu solicitud el {fecha} (código <b>{codigo}</b>). Borramos tus datos de"
                  " Skandar en un plazo máximo de 20 días hábiles.</p>")
    return BORRADO_HTML.replace("{cuerpo}", cuerpo)
