"""Skandi: el asistente de ayuda, con un botón redondo de chat abajo a la derecha.

Dos lugares: el panel (clientes con sesión, abajo) y la web pública skandar.online
(visitantes; ver "Skandi en la web pública" al final).

En el panel:

El botón abre una ventanita de chat (#skandi con :target, sin JavaScript). La
pregunta va por un formulario a /panel/ayuda, que contesta con Claude (Haiku,
una vuelta, sin herramientas) y regresa a la misma página con la ventana
abierta. panel.js solo agrega "escribiendo…", baja al último mensaje y cierra
sin brincar.

Lo que sabe Skandi:
- prompts/skandi.md: quién es, qué es Skandar, el mapa del panel y cuándo pasar
  a soporte. Es el contexto base, fácil de editar.
- La información de empresa de la cuenta principal (@skandar.online, su
  Perfil de empresa): la misma para todos los clientes.
- Unos datos de la cuenta de quien pregunta (usuario, plan, si está encendido).

Si piden una persona o no sabe, Skandi dice que soporte se comunicará y termina
con [SOPORTE]: esa línea no se muestra y manda un correo a SOPORTE_EMAIL con la
cuenta y la conversación (como mucho MAX_CORREOS_DIA por cuenta al día).

Cada cuenta tiene MAX_PREGUNTAS_DIA preguntas al día (cada una es una llamada a
Claude). La conversación vive en memoria (las últimas MAX_TURNOS) y se borra
al reiniciar el proceso: es ayuda rápida, no un historial.
"""
import logging
import os
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.routing import Match

from . import config
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.skandi")

router = APIRouter(prefix="/panel")
# La pone app.py al arrancar: la cuenta principal, de donde sale la información de empresa.
principal = lambda: None  # noqa: E731

PROMPT = Path("prompts/skandi.md")
SOPORTE_EMAIL = os.getenv("SOPORTE_EMAIL", "skandar.carmach@gmail.com")
MARCA_SOPORTE = "[SOPORTE]"
MODELO = "claude-haiku-5-5"
MAX_PREGUNTAS_DIA = 30
MAX_CORREOS_DIA = 3
MAX_TURNOS = 16
MAX_MENSAJE = 500
SALUDO = ("Hola, soy Skandi. Pregúntame cómo usar el panel, tu asistente o tus campañas."
          " Si prefieres hablar con alguien del equipo, dímelo.")

# Por cuenta (su slug; "" la principal): [[rol, texto], ...] y un aviso que se muestra una vez.
_charlas: dict[str, list[list[str]]] = {}
_avisos: dict[str, str] = {}

# El ícono de chat de siempre (burbuja con tres puntos), en el botón redondo.
ICONO = ('<svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" aria-hidden="true">'
         '<path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.4 1.2 4.6 3.1 6.1L4.3 21l4-1.9c1.2.4 2.4.6 3.7.6 5.5 0 10-3.8 10-8.6S17.5 3 12 3z"/>'
         '<circle cx="8" cy="11.5" r="1.3" fill="var(--tinta,#141518)"/><circle cx="12" cy="11.5" r="1.3" fill="var(--tinta,#141518)"/>'
         '<circle cx="16" cy="11.5" r="1.3" fill="var(--tinta,#141518)"/></svg>')

CSS = """
.sk-boton{position:fixed;right:calc(1.5rem + env(safe-area-inset-right,0px));bottom:calc(1.5rem + env(safe-area-inset-bottom,0px));z-index:15;width:3.75rem;height:3.75rem;display:grid;place-items:center;background:var(--acento);color:#F5F5F4;border-radius:50%;text-decoration:none;box-shadow:0 10px 24px rgba(12,11,16,.45),inset 2px 2px 0 rgba(255,255,255,.25),inset -3px -4px 8px rgba(0,0,0,.12);transition:transform .15s}
.sk-boton:hover{transform:scale(1.06)}
.skandi{display:none;position:fixed;right:1.25rem;bottom:1.25rem;z-index:50;width:min(23rem,calc(100vw - 2rem));height:min(34rem,calc(100dvh - 2.5rem));flex-direction:column;background:#1F2024;color:#F5F5F4;border-radius:1.6rem;box-shadow:10px 14px 28px rgba(12,11,16,.55),inset 2px 2px 0 rgba(255,255,255,.07);overflow:hidden}
html:not(.js) .skandi:target,.skandi.abierta{display:flex}
html:not(.js) .skandi:target~.sk-boton,.skandi.abierta~.sk-boton{display:none}
.sk-cabeza{display:flex;align-items:center;gap:.7rem;padding:1rem 1.1rem;background:#18191C}
.sk-avatar{width:2.2rem;height:2.2rem;border-radius:50%;background:var(--acento);color:var(--tinta);display:grid;place-items:center;font-weight:800}
.sk-cabeza b{display:block;font-size:1rem}.sk-cabeza small{display:flex;align-items:center;gap:.35rem;color:#A8A8AE;font-size:.8rem}
.sk-cabeza small::before{content:"";width:.45rem;height:.45rem;border-radius:50%;background:#C6FA2C}
.sk-cerrar{margin-left:auto;color:#A8A8AE;font-size:1.5rem;line-height:1;text-decoration:none;padding:.2rem .45rem;border-radius:.6rem}
.sk-cerrar:hover{color:#F5F5F4;background:rgba(255,255,255,.06)}
.sk-mensajes{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.5rem}
.sk-msj{max-width:85%;padding:.55rem .85rem;border-radius:1.1rem;font-size:.92rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
.sk-msj.tu{align-self:flex-end;background:var(--acento);color:var(--tinta);border-bottom-right-radius:.3rem}
.sk-msj.skandi-dice{align-self:flex-start;background:#2E3035;border-bottom-left-radius:.3rem}
.sk-msj.escribiendo{color:#A8A8AE;font-style:italic}
.sk-aviso{font-size:.85rem;color:#FFAB70;background:rgba(255,138,61,.12);border-radius:.8rem;padding:.55rem .8rem}
.sk-escribir{display:flex;gap:.5rem;padding:.8rem;border-top:1px solid rgba(255,255,255,.08)}
.sk-escribir input[type=text]{flex:1;min-width:0;font:inherit;font-size:.95rem;color:#F5F5F4;background:#18191C;border:1px solid rgba(255,255,255,.1);border-radius:.9rem;padding:.6rem .8rem}
.sk-escribir input[type=text]:focus{outline:2px solid var(--acento);outline-offset:1px;border-color:transparent}
.sk-escribir button{font:inherit;font-weight:700;background:var(--acento);color:var(--tinta);border:0;border-radius:.9rem;padding:.6rem .95rem;cursor:pointer}
.sk-escribir button:disabled{opacity:.6;cursor:wait}
@media(max-width:600px){.sk-boton{right:calc(1rem + env(safe-area-inset-right,0px));bottom:calc(1rem + env(safe-area-inset-bottom,0px));width:3.4rem;height:3.4rem}
.skandi{right:.5rem;bottom:calc(.5rem + env(safe-area-inset-bottom,0px));width:calc(100vw - 1rem);height:calc(100dvh - 1rem - env(safe-area-inset-bottom,0px))}}
@media print{.sk-boton,.skandi{display:none!important}}
"""


def clave(c: Cuenta) -> str:
    return c.slug or "principal"


def plan(c: Cuenta) -> str:
    return "Community Manager" if c.imagenes_mes > 0 else "Asistente virtual"


def mensaje(rol: str, texto: str) -> str:
    return f'<div class="sk-msj {"tu" if rol == "user" else "skandi-dice"}">{escape(texto)}</div>'


def widget(c: Cuenta) -> str:
    """El botón de ayuda y la ventanita (cerrada hasta que la abren con #skandi)."""
    charla = _charlas.get(clave(c), [])
    aviso = _avisos.pop(clave(c), "")
    mensajes = mensaje("assistant", SALUDO) + "".join(mensaje(r, t) for r, t in charla)
    if aviso:
        mensajes += f'<div class="sk-aviso">{escape(aviso)}</div>'
    # El botón va después de la ventana: así el CSS lo esconde mientras está abierta.
    return (
        '<section class="skandi" id="skandi" aria-label="Skandi, tu ayuda">'
        '<div class="sk-cabeza"><span class="sk-avatar" aria-hidden="true">S</span>'
        '<div><b>Skandi</b><small>Tu ayuda en Skandar</small></div>'
        '<a class="sk-cerrar" href="#cerrar-ayuda" data-cerrar-skandi aria-label="Cerrar ayuda">×</a></div>'
        f'<div class="sk-mensajes" id="sk-mensajes">{mensajes}<span id="sk-fin"></span></div>'
        '<form class="sk-escribir" method="post" action="/panel/ayuda" data-skandi>'
        '<input type="hidden" name="volver" value="">'
        f'<input type="text" name="mensaje" maxlength="{MAX_MENSAJE}" placeholder="Escribe tu pregunta"'
        ' aria-label="Escribe tu pregunta para Skandi" autocomplete="off" required>'
        '<button>Enviar</button></form></section>'
        f'<a class="sk-boton" href="#skandi" aria-label="Ayuda: pregúntale a Skandi" title="Ayuda">{ICONO}</a>'
    )


# --- contestar ---

async def preguntas_hoy(c: Cuenta, que: str = "skandi") -> tuple[str, int]:
    from .limites import today_start
    with usar(c):  # el día empieza a medianoche en la zona horaria de la cuenta
        k = f"{que}:{int(today_start())}"
    return k, int(await c.store.get(k) or 0)


async def datos_cuenta(c: Cuenta) -> str:
    from . import panel
    estado = ("en revisión" if c.en_revision else
              "encendido" if await panel.activo(c) else "en pausa")
    return (f"Usuario de Instagram: @{escape(await panel.usuario(c))}\n"
            f"Plan: {plan(c)}\nSu asistente está: {estado}")


async def sistema(prompt: Path, extra: str = "") -> str:
    """El contexto base (prompt), la información de empresa de la cuenta principal y `extra`."""
    from . import empresa
    partes = [prompt.read_text(encoding="utf-8").strip()]
    if (p := principal()) is not None and (info := await empresa.texto_para_prompt(p)):
        partes.append(f"<informacion_de_skandar>\n{info}\n</informacion_de_skandar>")
    if extra:
        partes.append(extra)
    return "\n\n".join(partes)


def conversacion(previos: list[list[str]]) -> str:
    return "\n".join(f"{'Cliente' if r == 'user' else 'Skandi'}: {escape(t)}" for r, t in previos)


async def responder(c: Cuenta, previos: list[list[str]], texto: str, system: str) -> str:
    """Una vuelta de Claude sin herramientas; el costo cuenta para `c`."""
    from . import agent  # import tardío: agent importa el SDK
    partes = []
    if previos:
        partes.append(f"<conversacion>\n{conversacion(previos)}\n</conversacion>")
    partes.append(f"<pregunta>\n{escape(texto)}\n</pregunta>\n\nEscribe solo tu respuesta.")
    with usar(c):
        return await agent.run_claude("\n\n".join(partes), system, max_turns=1, model=MODELO)


async def correo_a_soporte(asunto: str, texto: str) -> bool:
    from . import conectar
    if not conectar.google or not SOPORTE_EMAIL:
        log.warning("Skandi: no hay correo configurado para avisar a soporte (%s)", asunto)
        return False
    try:
        await conectar.google.send_email(config.MAIL_FROM, "Skandi", SOPORTE_EMAIL, "", asunto, texto)
    except Exception:
        log.exception("Skandi: no se pudo mandar el correo a soporte")
        return False
    return True


async def avisar_soporte(c: Cuenta, charla: list[list[str]]) -> None:
    """Correo a soporte con la cuenta y la conversación (pocos al día por cuenta)."""
    from . import panel
    k, enviados = await preguntas_hoy(c, "skandi_correos")
    if enviados >= MAX_CORREOS_DIA:
        log.info("Skandi: %s ya mandó %d correos a soporte hoy", clave(c), enviados)
        return
    usuario = await panel.usuario(c)
    texto = (f"@{usuario} pidió ayuda en el panel y Skandi lo pasa a soporte.\n\n"
             f"Cuenta: @{usuario} ({clave(c)}, id {c.ig_account_id})\nPlan: {plan(c)}\n"
             + (f"Correo de la cuenta: {c.owner_email}\n" if c.owner_email and c.owner_email != config.MAIL_FROM else "")
             + f"Instagram: https://www.instagram.com/{usuario}/\n\nConversación:\n" + texto_charla(charla))
    if await correo_a_soporte(f"Soporte: @{usuario} necesita ayuda", texto):
        await c.store.set(k, str(enviados + 1))


def texto_charla(charla: list[list[str]], quien: str = "Cliente") -> str:
    return "\n".join(f"{quien if r == 'user' else 'Skandi'}: {t}" for r, t in charla)


def volver_a(request: Request, pedido: str) -> str:
    """La página desde donde preguntaron (si es del panel y se puede abrir con GET)."""
    ruta = pedido or urlparse(request.headers.get("referer", "")).path
    if not ruta.startswith("/panel") or "//" in ruta or any(ch in ruta for ch in "\r\n\\#"):
        return "/panel"
    camino = ruta.split("?", 1)[0]
    scope = {"type": "http", "path": camino, "method": "GET", "root_path": ""}
    if not any(r.matches(scope)[0] == Match.FULL for r in request.app.router.routes):
        return "/panel"
    return ruta


@router.post("/ayuda")
async def preguntar(request: Request):
    from . import panel
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    f = await panel.form(request)
    destino = volver_a(request, f.get("volver", "")) + "#skandi"
    texto = " ".join(f.get("mensaje", "").split())[:MAX_MENSAJE]
    if not texto:
        return RedirectResponse(destino, status_code=303)
    k, usadas = await preguntas_hoy(c)
    if usadas >= MAX_PREGUNTAS_DIA:
        _avisos[clave(c)] = ("Llegaste al límite de preguntas de hoy. Mañana sigo contigo; si es urgente,"
                             " escríbenos por Instagram a @skandar.online.")
        return RedirectResponse(destino, status_code=303)
    await c.store.set(k, str(usadas + 1))
    previos = _charlas.get(clave(c), [])[-MAX_TURNOS:]
    try:
        extra = f"<cuenta_de_quien_pregunta>\n{await datos_cuenta(c)}\n</cuenta_de_quien_pregunta>"
        respuesta = await responder(c, previos, texto, await sistema(PROMPT, extra))
    except Exception:
        log.exception("Skandi no pudo contestar a %s", clave(c))
        _avisos[clave(c)] = "No pude contestar esta vez. Intenta de nuevo en un momento."
        return RedirectResponse(destino, status_code=303)
    soporte = MARCA_SOPORTE in respuesta
    respuesta = respuesta.replace(MARCA_SOPORTE, "").strip() or "El equipo de soporte se comunicará contigo pronto."
    charla = (previos + [["user", texto], ["assistant", respuesta]])[-MAX_TURNOS:]
    _charlas[clave(c)] = charla
    if soporte:
        await avisar_soporte(c, charla)
    return RedirectResponse(destino, status_code=303)


# --- Skandi en la web pública (skandar.online) ---
#
# La web es estática: sitio.js pone el botón redondo y una ventanita con un <iframe>
# a skandar.online/ayuda, que Caddy pasa a este proceso (mismo origen, así el CSP de
# la web lo deja y aquí no hace falta fetch). Cada visitante tiene una cookie al azar;
# su conversación vive en memoria. Contra abuso: MAX_WEB_VISITANTE preguntas por
# visitante al día y MAX_WEB_DIA entre todos; correos a soporte, MAX_WEB_CORREOS al día.

web_router = APIRouter(prefix="/ayuda")
PROMPT_WEB = Path("prompts/skandi-web.md")
COOKIE_WEB = "skandi"
MAX_WEB_VISITANTE = 15
MAX_WEB_DIA = 300
MAX_WEB_CORREOS = 20
MAX_VISITANTES = 500  # conversaciones en memoria; se olvidan las más viejas
SALUDO_WEB = ("Hola, soy Skandi. Te ayudo con dudas de Skandar: qué hace, cuánto cuesta, cómo empezar"
              " o cómo ver una demo.")
CSP_WEB = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline' https://fonts.googleapis.com;"
           " font-src https://fonts.gstatic.com; form-action 'self'; base-uri 'none'; frame-ancestors 'self'")

# token del visitante -> {"dia": clave del día, "n": preguntas hoy, "charla": [[rol, texto], ...]}
_visitantes: dict[str, dict] = {}

ESTILO_WEB = """
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{font:15px/1.5 "Nunito",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#F5F5F4;background:#2B2C30;display:flex;flex-direction:column}
.mensajes{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.msj{max-width:85%;padding:9px 13px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere}
.msj.tu{align-self:flex-end;background:#FF8A3D;color:#18191C;border-bottom-right-radius:5px;font-weight:600}
.msj.skandi{align-self:flex-start;background:#1F2024;border:1px solid #34363B;border-bottom-left-radius:5px}
.msj.escribiendo{color:#A8A8AE;font-style:italic}
.aviso{font-size:13px;color:#FFAB70;background:rgba(255,138,61,.12);border-radius:12px;padding:8px 12px}
form{display:flex;gap:8px;padding:12px;border-top:1px solid #34363B;background:#242529}
input[type=text]{flex:1;min-width:0;font:inherit;color:#F5F5F4;background:#18191C;border:1px solid #34363B;border-radius:12px;padding:10px 12px}
input[type=text]:focus{outline:2px solid #FF8A3D;outline-offset:1px;border-color:transparent}
button{font:inherit;font-weight:700;background:#FF8A3D;color:#18191C;border:0;border-radius:12px;padding:10px 15px;cursor:pointer;box-shadow:0 3px 0 #C9621F}
button:disabled{opacity:.6;cursor:wait}
"""


def mensaje_web(rol: str, texto: str) -> str:
    return f'<div class="msj {"tu" if rol == "user" else "skandi"}">{escape(texto)}</div>'


def visitante(request: Request) -> tuple[str, dict]:
    import secrets
    token = request.cookies.get(COOKIE_WEB, "")
    if not (len(token) == 32 and token.isalnum()):
        token = secrets.token_hex(16)
    if token not in _visitantes:
        while len(_visitantes) >= MAX_VISITANTES:
            _visitantes.pop(next(iter(_visitantes)))
        _visitantes[token] = {"dia": "", "n": 0, "charla": []}
    return token, _visitantes[token]


def pagina_web(v: dict, aviso: str = "") -> HTMLResponse:
    from . import panel
    mensajes = mensaje_web("assistant", SALUDO_WEB) + "".join(mensaje_web(r, t) for r, t in v["charla"])
    if aviso:
        mensajes += f'<div class="aviso">{escape(aviso)}</div>'
    html = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
        '<title>Skandi · Skandar</title>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap">'
        f'<style>{ESTILO_WEB}</style>'
        f'<script src="/ayuda/ayuda.js?v={panel.VERSION_JS}" defer></script></head><body>'
        f'<div class="mensajes" id="mensajes">{mensajes}<span id="fin"></span></div>'
        '<form method="post" action="/ayuda#fin">'
        f'<input type="text" name="mensaje" maxlength="{MAX_MENSAJE}" placeholder="Escribe tu pregunta"'
        ' aria-label="Escribe tu pregunta para Skandi" autocomplete="off" required>'
        '<button>Enviar</button></form></body></html>'
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP_WEB, "X-Frame-Options": "SAMEORIGIN",
                                       "Cache-Control": "no-store"})


def con_cookie(response, token: str):
    response.set_cookie(COOKIE_WEB, token, max_age=30 * 86400, path="/ayuda", httponly=True,
                        secure=True, samesite="lax")
    return response


@web_router.get("")
async def ver_web(request: Request):
    token, v = visitante(request)
    aviso = v.pop("aviso", "")
    return con_cookie(pagina_web(v, aviso), token)


@web_router.get("/ayuda.js")
def script_web():
    from fastapi.responses import FileResponse
    from . import panel
    return FileResponse(panel.STATIC / "ayuda.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


def de_la_web(request: Request) -> None:
    """Contra CSRF: el formulario tiene que venir de la web o del mismo sitio."""
    h = request.headers
    if (h.get("origin") not in (config.SITE_URL, config.PUBLIC_URL.rstrip("/"))
            and h.get("sec-fetch-site") != "same-origin"):
        raise HTTPException(status_code=403)


@web_router.post("")
async def preguntar_web(request: Request):
    from . import panel
    de_la_web(request)
    token, v = visitante(request)
    volver = con_cookie(RedirectResponse("/ayuda#fin", status_code=303), token)
    texto = " ".join((await panel.form(request)).get("mensaje", "").split())[:MAX_MENSAJE]
    if not texto or (p := principal()) is None:
        return volver
    k, total = await preguntas_hoy(p, "skandi_web")
    if v["dia"] != k:
        v["dia"], v["n"] = k, 0
    if v["n"] >= MAX_WEB_VISITANTE or total >= MAX_WEB_DIA:
        v["aviso"] = ("Por hoy ya no puedo contestar más preguntas. Escríbenos por Instagram a"
                      " @skandar.online y te contestamos ahí.")
        return volver
    v["n"] += 1
    await p.store.set(k, str(total + 1))
    previos = v["charla"][-MAX_TURNOS:]
    try:
        respuesta = await responder(p, previos, texto, await sistema(PROMPT_WEB))
    except Exception:
        log.exception("Skandi (web) no pudo contestar")
        v["aviso"] = "No pude contestar esta vez. Intenta de nuevo en un momento."
        return volver
    soporte = MARCA_SOPORTE in respuesta
    respuesta = respuesta.replace(MARCA_SOPORTE, "").strip() or "El equipo se comunicará contigo pronto."
    v["charla"] = (previos + [["user", texto], ["assistant", respuesta]])[-MAX_TURNOS:]
    if soporte:
        kc, enviados = await preguntas_hoy(p, "skandi_web_correos")
        if enviados < MAX_WEB_CORREOS and await correo_a_soporte(
                "Web: un visitante quiere que lo contacten",
                "Un visitante de skandar.online habló con Skandi y pidió que lo contacte el equipo.\n\n"
                "Conversación:\n" + texto_charla(v["charla"], "Visitante")):
            await p.store.set(kc, str(enviados + 1))
    return volver
