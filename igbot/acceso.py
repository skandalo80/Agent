"""Entrar al panel con un enlace por correo.

En un iPhone con la app de Instagram, los enlaces de instagram.com los abre la
app y la pantalla de permisos se queda en "Loading": desde Safari no se puede
entrar con Instagram. Aquí el cliente escribe su correo (el de "Horario y
avisos" o el que dejó al conectarse) y le llega un enlace de un solo uso que
dura 15 minutos. El enlace abre una página con un botón: algunos correos abren
los enlaces solos para revisarlos, y así no gastan el enlace antes que él.
"""
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections.abc import Callable
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config, panel
from .cuenta import Cuenta

log = logging.getLogger("igbot.acceso")
router = APIRouter(prefix="/panel/correo")

# Lo pone app.py al arrancar: todas las cuentas cargadas.
todas: Callable[[], list[Cuenta]] = lambda: []

VIGENCIA = 900
CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.I)
MAX_POR_CORREO = 3   # enlaces por correo por hora
MAX_POR_HORA = 30    # en total, contra abusos

_usados: dict[str, float] = {}       # enlaces ya usados -> cuándo vencen
_enviados: list[tuple[float, str]] = []  # (cuándo, correo)


def _firma(texto: str) -> str:
    clave = hmac.new(config.APP_SECRET.encode(), b"panel-correo", hashlib.sha256).digest()
    return hmac.new(clave, texto.encode(), hashlib.sha256).hexdigest()[:32]


def nuevo_enlace(ig_id: str, ahora: float | None = None) -> str:
    base = f"{ig_id}.{int((time.time() if ahora is None else ahora) + VIGENCIA)}.{secrets.token_urlsafe(9)}"
    return f"{base}.{_firma(base)}"


def de_enlace(t: str) -> str | None:
    """El id de Instagram del enlace, si es nuestro, no ha vencido y no se ha usado."""
    partes = t.split(".")
    if len(partes) != 4 or not partes[1].isdigit() or t in _usados:
        return None
    if int(partes[1]) < time.time():
        return None
    if not hmac.compare_digest(partes[3], _firma(".".join(partes[:3]))):
        return None
    return partes[0]


def usar_enlace(t: str) -> None:
    ahora = time.time()
    for k, vence in list(_usados.items()):
        if vence < ahora:
            del _usados[k]
    _usados[t] = ahora + VIGENCIA


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
        '<p class="antetitulo">Panel de clientes</p>'
        f'<h1>{titulo}<span class="punto">.</span></h1>'
        f'<p class="bajada">{bajada}</p>{cuerpo}'
        "</div></section>")


def formulario(aviso: str = "") -> HTMLResponse:
    return pagina(
        "Entra con tu correo",
        "Te mandamos un enlace para entrar a tu panel. Usa el correo que tienes en Horario y avisos"
        " o el que dejaste al conectarte.",
        (f'<p class="aviso-entrar">{aviso}</p>' if aviso else "")
        + '<form class="entrar-correo" method="post" action="/panel/correo">'
        '<input type="email" name="correo" required maxlength="254" autocomplete="email"'
        ' placeholder="tu@correo.com" aria-label="Tu correo">'
        '<button class="boton" type="submit">Mandarme el enlace</button></form>'
        '<p class="nota"><a class="enlace" href="/panel">Volver a entrar con Instagram ›</a></p>')


@router.get("")
def ver():
    return formulario()


@router.post("")
async def pedir(request: Request):
    panel.de_aqui(request)
    correo = str((await request.form()).get("correo", "")).strip().lower()
    if not CORREO.match(correo):
        return formulario("Revisa el correo: parece que le falta algo.")
    c = cuenta_de_correo(correo)
    from . import conectar  # conectar usa panel; aquí ya está cargado
    if c and conectar.google and _puede_enviar(correo):
        enlace = f"{config.PUBLIC_URL}/panel/correo/entrar?t={nuevo_enlace(c.ig_account_id)}"
        try:
            await conectar.google.send_email(
                config.MAIL_FROM, "Agentes IA by Skandar", correo, "", "Tu enlace para entrar al panel",
                f"Hola,\n\nEste es tu enlace para entrar a tu panel:\n\n{enlace}\n\n"
                "Sirve una sola vez y dura 15 minutos. Si no lo pediste, ignora este correo.\n",
            )
            log.info("Enlace de acceso enviado a la cuenta @%s", await panel.usuario(c))
        except Exception:
            log.exception("No se pudo mandar el enlace de acceso")
    elif not c:
        log.info("Pidieron enlace de acceso con un correo que no es de ninguna cuenta")
    # Siempre lo mismo: no se dice qué correos están registrados.
    return pagina("Revisa tu correo",
                  f"Si {escape(correo)} es el correo de una cuenta, te acaba de llegar un enlace para entrar."
                  " Dura 15 minutos.",
                  '<p class="nota">¿No llegó? Revisa en spam o <a class="enlace" href="/panel/correo">'
                  "pide otro ›</a></p>")


def vencido() -> HTMLResponse:
    return pagina("Este enlace ya no sirve",
                  "Ya se usó o pasaron más de 15 minutos. Pide otro y entra con el nuevo.",
                  '<div class="acciones"><a class="boton" href="/panel/correo">Pedir otro enlace</a></div>')


@router.get("/entrar")
def confirmar(t: str = ""):
    if not de_enlace(t):
        return vencido()
    return pagina("Entra a tu panel", "Toca el botón para entrar. El enlace sirve una sola vez.",
                  '<form method="post" action="/panel/correo/entrar">'
                  f'<input type="hidden" name="t" value="{escape(t)}">'
                  '<div class="acciones"><button class="boton" type="submit">Entrar</button></div></form>')


@router.post("/entrar")
async def entrar(request: Request):
    panel.de_aqui(request)
    t = str((await request.form()).get("t", ""))
    ig_id = de_enlace(t)
    if not ig_id or not panel.buscar(ig_id):
        return vencido()
    usar_enlace(t)
    response = RedirectResponse("/panel", status_code=303)
    panel.iniciar_sesion(response, ig_id)
    return response
