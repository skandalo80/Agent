"""Skandi: la ayuda del panel, en un botón abajo a la derecha de cada página.

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

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
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

ICONO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"'
         ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9.5"/>'
         '<path d="M9.3 9.2a2.8 2.8 0 0 1 5.4 1c0 1.9-2.7 2.5-2.7 4"/><path d="M12 17.6h0"/></svg>')

CSS = """
.sk-boton{position:fixed;right:1.25rem;bottom:1.25rem;z-index:15;display:flex;align-items:center;gap:.45rem;background:var(--acento);color:var(--tinta);font-weight:700;padding:.7rem 1.05rem;border-radius:999px;text-decoration:none;box-shadow:0 10px 24px rgba(12,11,16,.45),inset 2px 2px 0 rgba(255,255,255,.25)}
.sk-boton:hover{filter:brightness(1.05)}
.skandi{display:none;position:fixed;right:1.25rem;bottom:1.25rem;z-index:50;width:min(23rem,calc(100vw - 2rem));height:min(34rem,calc(100dvh - 2.5rem));flex-direction:column;background:#302D3A;color:#F7F5FA;border-radius:1.6rem;box-shadow:10px 14px 28px rgba(12,11,16,.55),inset 2px 2px 0 rgba(255,255,255,.07);overflow:hidden}
html:not(.js) .skandi:target,.skandi.abierta{display:flex}
html:not(.js) .skandi:target~.sk-boton,.skandi.abierta~.sk-boton{display:none}
.sk-cabeza{display:flex;align-items:center;gap:.7rem;padding:1rem 1.1rem;background:#25232F}
.sk-avatar{width:2.2rem;height:2.2rem;border-radius:50%;background:var(--acento);color:var(--tinta);display:grid;place-items:center;font-weight:800}
.sk-cabeza b{display:block;font-size:1rem}.sk-cabeza small{display:flex;align-items:center;gap:.35rem;color:#B9B5C5;font-size:.8rem}
.sk-cabeza small::before{content:"";width:.45rem;height:.45rem;border-radius:50%;background:#C6FA2C}
.sk-cerrar{margin-left:auto;color:#B9B5C5;font-size:1.5rem;line-height:1;text-decoration:none;padding:.2rem .45rem;border-radius:.6rem}
.sk-cerrar:hover{color:#F7F5FA;background:rgba(255,255,255,.06)}
.sk-mensajes{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.5rem}
.sk-msj{max-width:85%;padding:.55rem .85rem;border-radius:1.1rem;font-size:.92rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
.sk-msj.tu{align-self:flex-end;background:var(--acento);color:var(--tinta);border-bottom-right-radius:.3rem}
.sk-msj.skandi-dice{align-self:flex-start;background:#3B3846;border-bottom-left-radius:.3rem}
.sk-msj.escribiendo{color:#B9B5C5;font-style:italic}
.sk-aviso{font-size:.85rem;color:#F6B262;background:rgba(243,154,54,.12);border-radius:.8rem;padding:.55rem .8rem}
.sk-escribir{display:flex;gap:.5rem;padding:.8rem;border-top:1px solid rgba(255,255,255,.08)}
.sk-escribir input[type=text]{flex:1;min-width:0;font:inherit;font-size:.95rem;color:#F7F5FA;background:#25232F;border:1px solid rgba(255,255,255,.1);border-radius:.9rem;padding:.6rem .8rem}
.sk-escribir input[type=text]:focus{outline:2px solid var(--acento);outline-offset:1px;border-color:transparent}
.sk-escribir button{font:inherit;font-weight:700;background:var(--acento);color:var(--tinta);border:0;border-radius:.9rem;padding:.6rem .95rem;cursor:pointer}
.sk-escribir button:disabled{opacity:.6;cursor:wait}
@media(max-width:600px){.sk-boton{right:.9rem;bottom:.9rem;padding:.75rem}.sk-boton .txt{display:none}
.skandi{right:.5rem;bottom:.5rem;width:calc(100vw - 1rem);height:calc(100dvh - 1rem)}}
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
        f'<a class="sk-boton" href="#skandi" aria-label="Ayuda: pregúntale a Skandi">{ICONO}<span class="txt">Ayuda</span></a>'
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


async def sistema(c: Cuenta) -> str:
    from . import empresa
    partes = [PROMPT.read_text(encoding="utf-8").strip()]
    if (p := principal()) is not None and (info := await empresa.texto_para_prompt(p)):
        partes.append(f"<informacion_de_skandar>\n{info}\n</informacion_de_skandar>")
    partes.append(f"<cuenta_de_quien_pregunta>\n{await datos_cuenta(c)}\n</cuenta_de_quien_pregunta>")
    return "\n\n".join(partes)


def conversacion(previos: list[list[str]]) -> str:
    return "\n".join(f"{'Cliente' if r == 'user' else 'Skandi'}: {escape(t)}" for r, t in previos)


async def responder(c: Cuenta, previos: list[list[str]], texto: str) -> str:
    from . import agent  # import tardío: agent importa el SDK
    partes = []
    if previos:
        partes.append(f"<conversacion>\n{conversacion(previos)}\n</conversacion>")
    partes.append(f"<pregunta>\n{escape(texto)}\n</pregunta>\n\nEscribe solo tu respuesta.")
    with usar(c):
        return await agent.run_claude("\n\n".join(partes), await sistema(c), max_turns=1, model=MODELO)


async def avisar_soporte(c: Cuenta, charla: list[list[str]]) -> None:
    """Correo a soporte con la cuenta y la conversación (pocos al día por cuenta)."""
    from . import conectar, panel
    k, enviados = await preguntas_hoy(c, "skandi_correos")
    if enviados >= MAX_CORREOS_DIA:
        log.info("Skandi: %s ya mandó %d correos a soporte hoy", clave(c), enviados)
        return
    google = conectar.google
    if not google or not SOPORTE_EMAIL:
        log.warning("Skandi: no hay correo configurado para avisar a soporte (%s)", clave(c))
        return
    usuario = await panel.usuario(c)
    texto = (f"@{usuario} pidió ayuda en el panel y Skandi lo pasa a soporte.\n\n"
             f"Cuenta: @{usuario} ({clave(c)}, id {c.ig_account_id})\nPlan: {plan(c)}\n"
             + (f"Correo de la cuenta: {c.owner_email}\n" if c.owner_email and c.owner_email != config.MAIL_FROM else "")
             + f"Instagram: https://www.instagram.com/{usuario}/\n\nConversación:\n"
             + "\n".join(f"{'Cliente' if r == 'user' else 'Skandi'}: {t}" for r, t in charla))
    try:
        await google.send_email(config.MAIL_FROM, "Skandi", SOPORTE_EMAIL, "",
                                f"Soporte: @{usuario} necesita ayuda", texto)
    except Exception:
        log.exception("Skandi: no se pudo mandar el correo a soporte")
        return
    await c.store.set(k, str(enviados + 1))


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
        respuesta = await responder(c, previos, texto)
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
