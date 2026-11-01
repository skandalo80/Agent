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
Claude). Los chats se guardan en la base de la cuenta principal (tabla
skandi_chats, los de la web también). Arriba en la ventana, el ícono de historial
(<details>, sin JavaScript) lista sus últimos MAX_HISTORIAL chats para abrir uno o
empezar otro. Una copia de esa lista vive en memoria (_chats, se llena al
arrancar con cargar()) para pintar el botón sin esperar a la base.

Enlaces a secciones: si preguntan dónde está algo, Skandi escribe [[clave]] y aquí se
cambia por el nombre de la sección en lima, con enlace a esa parte del panel. Solo
valen las claves de SECCIONES (direcciones reales); una clave que no existe no sale.

La cuenta principal ve todos los chats (panel y web) en /panel/skandi, con los que
pidieron soporte marcados.
"""
import logging
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.routing import Match
from zoneinfo import ZoneInfo

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
MAX_HISTORIAL = 30  # chats que ve cada cuenta en su historial
MAX_GUARDADOS = 200  # mensajes que se guardan por chat (a Claude solo van los últimos MAX_TURNOS)
SALUDO = "Hola, soy Skandi ¿Cómo te puedo ayudar?"

# Las secciones del panel a las que Skandi puede mandar con un enlace: (clave, nombre que se ve,
# dirección, qué hay ahí, solo con contenido en el plan). Solo estas: nada de direcciones inventadas.
SECCIONES = [
    ("inicio", "Inicio", "/panel/inicio", "lo de hoy: pasos que faltan, números, chats que esperan, próximas publicaciones, uso del plan", False),
    ("resultados", "Resultados", "/panel/resultados", "números de conversaciones e Instagram, PDF", False),
    ("anuncios", "Resultados de tus anuncios", "/panel/resultados?ver=anuncios", "chats, citas y ventas por anuncio", False),
    ("comentarios", "Resultados de tus comentarios", "/panel/resultados?ver=comentarios",
     "cuántos comentarios llegaron y cuántos contestó el asistente, de qué tipo, posts y palabras clave", True),
    ("conversaciones", "Conversaciones", "/panel/asistente", "los chats en vivo, tomar un chat, Marcar venta", False),
    ("probar", "Probar tu asistente", "/panel/probar#ultimo", "chat de prueba con su asistente", False),
    ("campanas", "Campañas", "/panel/contenido/campanas", "sus campañas", True),
    ("nueva_campana", "Crear campaña", "/panel/contenido/campanas/nueva", "empezar una campaña nueva", True),
    ("calendario", "Calendario", "/panel/contenido", "publicaciones programadas, moverlas, PDF", True),
    ("empresa", "Tu empresa", "/panel/empresa", "todo lo que el asistente sabe del negocio", False),
    ("empresa_general", "Información general", "/panel/empresa?paso=1",
     "nombre, sitio web, a qué se dedica, ciudad, llenar con su sitio web", False),
    ("catalogo", "Tu catálogo", "/panel/empresa?paso=3",
     "sus productos y servicios, precios, fotos, traer productos de Shopify", False),
    ("como_compran", "Cómo te compran", "/panel/empresa?paso=4", "horarios, dónde atiende, formas de pago, políticas", False),
    ("preguntas", "Preguntas frecuentes", "/panel/empresa?paso=5", "preguntas que le hacen y sus respuestas", False),
    ("clientes", "Tus clientes", "/panel/empresa?paso=2", "a quién le vende y qué lo hace distinto", False),
    ("asistente", "Tu asistente", "/panel/asistente/configura", "configurar su asistente paso por paso", False),
    ("personalidad", "Personalidad", "/panel/asistente/configura?paso=personalidad", "cómo se siente al hablar", False),
    ("escribe", "Cómo escribe", "/panel/asistente/configura?paso=escribe", "trato, largo de mensajes y emojis", False),
    ("reglas", "Reglas y límites", "/panel/asistente/configura?paso=reglas", "qué hace y qué evita su asistente", False),
    ("equipo", "Tu equipo", "/panel/asistente/configura?paso=equipo",
     "cuándo pasar la conversación a su equipo y el aviso por correo", False),
    ("encender", "Resumen de tu asistente", "/panel/asistente/configura?paso=resumen",
     "encender o pausar su asistente", False),
    ("marca", "Tu marca", "/panel/contenido/marca", "cómo se ven sus imágenes", True),
    ("estilo", "Estilo visual", "/panel/contenido/marca?paso=1", "estilos de sus diseños", True),
    ("colores", "Colores", "/panel/contenido/marca?paso=2", "colores de su marca", True),
    ("tipografia", "Tipografía", "/panel/contenido/marca?paso=3", "letra de títulos y textos", True),
    ("logotipo", "Logotipo", "/panel/contenido/marca?paso=4", "su logo en las imágenes", True),
    ("interacciones", "Tus interacciones", "/panel/interacciones",
     "qué contestar a comentarios, menciones y respuestas a historias, palabras clave y ocultar spam", True),
    ("palabras_clave", "Palabras clave", "/panel/interacciones?paso=claves",
     "palabras clave generales que mandan información por DM a quien las comenta", True),
    ("conexion", "Conexión", "/panel/conexion",
     "ver si todo funciona; conectar Facebook, Google Calendar, Mercado Pago, Stripe, Shopify y anuncios", False),
]
ENLACES = {k: (nombre, href, solo_contenido) for k, nombre, href, _, solo_contenido in SECCIONES}
FICHA = re.compile(r"\[\[\s*([a-z_]+)\s*\]\]")

# Por cuenta (clave): sus chats del más reciente al más viejo (como los da Store.chats_skandi),
# el que tiene abierto (None = uno nuevo; sin entrada = el más reciente) y un aviso que sale una vez.
_chats: dict[str, list[dict]] = {}
_actual: dict[str, int | None] = {}
_avisos: dict[str, str] = {}

ICONO_MIN = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2"'
             ' stroke-linecap="round" aria-hidden="true"><path d="M6 12h12"/></svg>')
ICONO_HISTORIAL = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"'
                   ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/>'
                   '<path d="M3 3v5h5M12 7v5l3 2"/></svg>')

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
.sk-ultimo{display:none;color:#A8A8AE;font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sk-nombre{min-width:0;flex:1}
.skandi.minimizada{height:auto;width:min(20rem,calc(100vw - 2rem));cursor:pointer}
.skandi.minimizada .sk-mensajes,.skandi.minimizada .sk-escribir,.skandi.minimizada .sk-hist,.skandi.minimizada .sk-min,.skandi.minimizada .sk-cabeza small{display:none}
.skandi.minimizada .sk-ultimo{display:block}
.skandi.minimizada .sk-cabeza{padding:.75rem .9rem}
.sk-cerrar,.sk-min{color:#A8A8AE;font-size:1.5rem;line-height:1;text-decoration:none;padding:.2rem .45rem;border-radius:.6rem;display:grid;place-items:center}
.sk-min{padding:.35rem}
.sk-cerrar:hover,.sk-min:hover{color:#F5F5F4;background:rgba(255,255,255,.06)}
.sk-hist{margin-left:auto}
.sk-hist>summary{list-style:none;cursor:pointer;color:#A8A8AE;padding:.35rem;border-radius:.6rem;display:grid;place-items:center}
.sk-hist>summary::-webkit-details-marker{display:none}
.sk-hist>summary:hover,.sk-hist[open]>summary{color:#F5F5F4;background:rgba(255,255,255,.06)}
.sk-lista{position:absolute;left:0;right:0;top:4.4rem;bottom:0;z-index:2;background:#1F2024;overflow-y:auto;padding:.9rem;display:flex;flex-direction:column;gap:.45rem}
.sk-lista h3{margin:.1rem .2rem .3rem;font-size:.85rem;color:#A8A8AE;font-weight:600}
.sk-lista form{margin:0}
.sk-lista button{width:100%;text-align:left;font:inherit;color:#F5F5F4;background:#2E3035;border:1px solid transparent;border-radius:.9rem;padding:.6rem .8rem;cursor:pointer;display:block}
.sk-lista button:hover{border-color:rgba(255,255,255,.12)}
.sk-lista button.on{border-color:var(--acento);background:rgba(255,138,61,.1)}
.sk-lista button b{display:block;font-weight:600;font-size:.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sk-lista button small{color:#A8A8AE;font-size:.78rem}
.sk-lista .sk-nueva{background:var(--acento);color:var(--tinta);font-weight:700;text-align:center}
.sk-lista .vacio{color:#A8A8AE;font-size:.88rem;margin:.4rem .2rem}
.sk-mensajes{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.5rem}
.sk-msj{max-width:85%;padding:.55rem .85rem;border-radius:1.1rem;font-size:.92rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
.sk-msj.tu{align-self:flex-end;background:var(--acento);color:var(--tinta);border-bottom-right-radius:.3rem}
.sk-msj.skandi-dice{align-self:flex-start;background:#2E3035;border-bottom-left-radius:.3rem}
.sk-enlace{color:#C6FA2C;font-weight:700;text-decoration:underline;text-decoration-color:rgba(198,250,44,.35);text-underline-offset:.18em}
.sk-enlace:hover{text-decoration-color:#C6FA2C}
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


def con_enlaces(texto: str, contenido: bool = True) -> str:
    """El texto escapado, con cada [[clave]] de SECCIONES como enlace en lima a esa sección.
    Sin contenido en el plan, las secciones de contenido salen como nombre, sin enlace."""
    def cambiar(m: re.Match) -> str:
        if m.group(1) not in ENLACES:
            return ""
        nombre, href, solo_contenido = ENLACES[m.group(1)]
        if solo_contenido and not contenido:
            return nombre
        return f'<a class="sk-enlace" href="{escape(href)}" data-enlace-skandi>{nombre}</a>'
    return re.sub(r" +([,.;:])", r"\1", FICHA.sub(cambiar, escape(texto)))


def mensaje(rol: str, texto: str, contenido: bool = True) -> str:
    cuerpo = escape(texto) if rol == "user" else con_enlaces(texto, contenido)
    return f'<div class="sk-msj {"tu" if rol == "user" else "skandi-dice"}">{cuerpo}</div>'


def sin_fichas(texto: str) -> str:
    """Para el correo a soporte: [[clave]] como el nombre de la sección."""
    return FICHA.sub(lambda m: ENLACES.get(m.group(1), ("",))[0], texto)


def enlaces_para(c: Cuenta) -> str:
    """La lista de secciones a las que puede mandar Skandi (según el plan de la cuenta)."""
    filas = "\n".join(f"[[{k}]] = {nombre}: {que}" for k, nombre, _, que, solo in SECCIONES
                      if not solo or c.imagenes_mes > 0)
    return f"<enlaces>\n{filas}\n</enlaces>"


def abierto(k: str) -> dict | None:
    """El chat que tiene abierto la cuenta `k` (None si empieza uno nuevo)."""
    lista = _chats.get(k, [])
    if k in _actual:
        return next((x for x in lista if x["id"] == _actual[k]), None)
    return lista[0] if lista else None


def titulo(chat: dict) -> str:
    """Su primera pregunta, para la lista."""
    primera = next((t for r, t in chat["mensajes"] if r == "user"), "Conversación")
    return primera if len(primera) <= 70 else primera[:69] + "…"


def cuando(t: float, zona: str) -> str:
    momento = datetime.fromtimestamp(t, ZoneInfo(zona))
    meses = "ene feb mar abr may jun jul ago sep oct nov dic".split()
    return f"{momento.day} {meses[momento.month - 1]}, {momento:%H:%M}"


def boton_chat(accion: str, contenido: str, chat_id: int | None = None, clase: str = "") -> str:
    """Un botón del historial: un formulario a /panel/ayuda (abrir un chat o empezar otro)."""
    return ('<form method="post" action="/panel/ayuda" data-skandi>'
            '<input type="hidden" name="volver" value="">'
            f'<input type="hidden" name="accion" value="{accion}">'
            + (f'<input type="hidden" name="chat" value="{chat_id}">' if chat_id else "")
            + (f'<button class="{clase}">' if clase else "<button>") + f"{contenido}</button></form>")


def historial(c: Cuenta, actual: dict | None) -> str:
    """El ícono de historial y su lista (se abre y cierra sin JavaScript)."""
    lista = _chats.get(clave(c), [])
    filas = "".join(
        boton_chat("abrir", f"<b>{escape(titulo(x))}</b><small>{cuando(x['actualizado'], c.timezone)}</small>",
                   x["id"], "on" if actual and x["id"] == actual["id"] else "")
        for x in lista) or '<p class="vacio">Aquí vas a ver tus conversaciones anteriores.</p>'
    return ('<details class="sk-hist"><summary aria-label="Conversaciones anteriores" title="Conversaciones anteriores">'
            f'{ICONO_HISTORIAL}</summary><div class="sk-lista">'
            + boton_chat("nueva", "Nueva conversación", clase="sk-nueva")
            + f'<h3>Conversaciones anteriores</h3>{filas}</div></details>')


def widget(c: Cuenta) -> str:
    """El botón de ayuda y la ventanita (cerrada hasta que la abren con #skandi)."""
    actual = abierto(clave(c))
    charla = actual["mensajes"] if actual else []
    aviso = _avisos.pop(clave(c), "")
    contenido = c.imagenes_mes > 0
    mensajes = mensaje("assistant", SALUDO) + "".join(mensaje(r, t, contenido) for r, t in charla)
    if aviso:
        mensajes += f'<div class="sk-aviso">{escape(aviso)}</div>'
    # El botón va después de la ventana: así el CSS lo esconde mientras está abierta.
    return (
        '<section class="skandi" id="skandi" aria-label="Skandi, tu ayuda">'
        '<div class="sk-cabeza"><span class="sk-avatar" aria-hidden="true">S</span>'
        # Minimizada, la cabeza queda como barrita con el último mensaje (.sk-ultimo, lo llena panel.js).
        '<div class="sk-nombre"><b>Skandi</b><small>Tu ayuda en Skandar</small><span class="sk-ultimo"></span></div>'
        + historial(c, actual) +
        # Minimizar: queda una barrita abajo a la derecha y la conversación sigue igual. Cerrar (×): con JavaScript, además la
        # siguiente pregunta empieza una conversación nueva (la anterior queda en el historial).
        f'<a class="sk-min" href="#minimizar-ayuda" data-minimizar-skandi aria-label="Minimizar" title="Minimizar">{ICONO_MIN}</a>'
        '<a class="sk-cerrar" href="#cerrar-ayuda" data-cerrar-skandi aria-label="Cerrar chat" title="Cerrar">×</a></div>'
        f'<div class="sk-mensajes" id="sk-mensajes">{mensajes}<span id="sk-fin"></span></div>'
        '<form class="sk-escribir" method="post" action="/panel/ayuda" data-skandi>'
        '<input type="hidden" name="volver" value=""><input type="hidden" name="nueva" value="">'
        f'<input type="text" name="mensaje" maxlength="{MAX_MENSAJE}" placeholder="Escribe tu pregunta"'
        ' aria-label="Escribe tu pregunta para Skandi" autocomplete="off" required>'
        '<button>Enviar</button></form></section>'
        f'<a class="sk-boton" href="#skandi" aria-label="Ayuda: pregúntale a Skandi" title="Ayuda">{ICONO}</a>'
    )


async def cargar() -> None:
    """Al arrancar: los últimos chats de cada cuenta, de la base a la memoria."""
    if (p := principal()) is None:
        return
    _chats.clear()
    chats, _ = await p.store.chats_skandi("panel", cuantos=100_000)
    for x in chats:
        lista = _chats.setdefault(x["quien"], [])
        if len(lista) < MAX_HISTORIAL:
            lista.append(x)


async def guardar(c: Cuenta, actual: dict | None, mensajes: list, soporte: bool) -> None:
    """En la base (de la cuenta principal) y en la memoria; queda como su chat abierto."""
    from . import panel
    k, usuario = clave(c), await panel.usuario(c)
    chat_id = await principal().store.guardar_skandi(actual["id"] if actual else None, "panel", k, usuario,
                                                     mensajes[-MAX_GUARDADOS:], soporte)
    chat = await principal().store.chat_skandi(chat_id)
    _chats[k] = [chat] + [x for x in _chats.get(k, []) if x["id"] != chat_id][:MAX_HISTORIAL - 1]
    _actual[k] = chat_id


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
    return "\n".join(f"{quien if r == 'user' else 'Skandi'}: {sin_fichas(t)}" for r, t in charla)


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
    k = clave(c)
    if f.get("accion") == "nueva":
        _actual[k] = None
        return RedirectResponse(destino, status_code=303)
    if f.get("accion") == "abrir":
        if f.get("chat", "").isdigit() and any(x["id"] == int(f["chat"]) for x in _chats.get(k, [])):
            _actual[k] = int(f["chat"])
        return RedirectResponse(destino, status_code=303)
    texto = " ".join(f.get("mensaje", "").split())[:MAX_MENSAJE]
    if not texto:
        return RedirectResponse(destino, status_code=303)
    if f.get("nueva") == "1":  # cerró el chat: esta pregunta empieza una conversación nueva
        _actual[k] = None
    limite, usadas = await preguntas_hoy(c)
    if usadas >= MAX_PREGUNTAS_DIA:
        _avisos[k] = ("Llegaste al límite de preguntas de hoy. Mañana sigo contigo; si es urgente,"
                             " escríbenos por Instagram a @skandar.online.")
        return RedirectResponse(destino, status_code=303)
    await c.store.set(limite, str(usadas + 1))
    actual = abierto(k)
    todos = actual["mensajes"] if actual else []
    previos = todos[-MAX_TURNOS:]
    try:
        extra = (f"<cuenta_de_quien_pregunta>\n{await datos_cuenta(c)}\n</cuenta_de_quien_pregunta>\n\n"
                 + enlaces_para(c))
        respuesta = await responder(c, previos, texto, await sistema(PROMPT, extra))
    except Exception:
        log.exception("Skandi no pudo contestar a %s", clave(c))
        _avisos[clave(c)] = "No pude contestar esta vez. Intenta de nuevo en un momento."
        return RedirectResponse(destino, status_code=303)
    soporte = MARCA_SOPORTE in respuesta
    respuesta = respuesta.replace(MARCA_SOPORTE, "").strip() or "El equipo de soporte se comunicará contigo pronto."
    charla = todos + [["user", texto], ["assistant", respuesta]]
    await guardar(c, actual, charla, soporte)
    if soporte:
        await avisar_soporte(c, charla[-MAX_TURNOS:])
    return RedirectResponse(destino, status_code=303)


# --- Skandi en la web pública (skandar.online) ---
#
# La web es estática: sitio.js pone el botón redondo y una ventanita con un <iframe>
# a skandar.online/ayuda, que Caddy pasa a este proceso (mismo origen, así el CSP de
# la web lo deja y aquí no hace falta fetch). Cada visitante tiene una cookie al azar;
# su conversación vive en memoria y se guarda en skandi_chats. Contra abuso:
# MAX_WEB_VISITANTE preguntas por visitante al día y MAX_WEB_DIA entre todos;
# correos a soporte, MAX_WEB_CORREOS al día.

web_router = APIRouter(prefix="/ayuda")
PROMPT_WEB = Path("prompts/skandi-web.md")
COOKIE_WEB = "skandi"
MAX_WEB_VISITANTE = 15
MAX_WEB_DIA = 300
MAX_WEB_CORREOS = 20
MAX_VISITANTES = 500  # conversaciones en memoria; se olvidan las más viejas
SALUDO_WEB = SALUDO
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
async def ver_web(request: Request, nueva: str = ""):
    token, v = visitante(request)
    if nueva == "1":  # cerró el chat (sitio.js): empieza otra conversación; la anterior queda guardada
        v["charla"] = []
        v.pop("chat_id", None)
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
    todos = v["charla"]
    previos = todos[-MAX_TURNOS:]
    try:
        respuesta = await responder(p, previos, texto, await sistema(PROMPT_WEB))
    except Exception:
        log.exception("Skandi (web) no pudo contestar")
        v["aviso"] = "No pude contestar esta vez. Intenta de nuevo en un momento."
        return volver
    soporte = MARCA_SOPORTE in respuesta
    respuesta = respuesta.replace(MARCA_SOPORTE, "").strip() or "El equipo se comunicará contigo pronto."
    v["charla"] = (todos + [["user", texto], ["assistant", respuesta]])[-MAX_GUARDADOS:]
    v["chat_id"] = await p.store.guardar_skandi(v.get("chat_id"), "web", token, "", v["charla"], soporte)
    if soporte:
        kc, enviados = await preguntas_hoy(p, "skandi_web_correos")
        if enviados < MAX_WEB_CORREOS and await correo_a_soporte(
                "Web: un visitante quiere que lo contacten",
                "Un visitante de skandar.online habló con Skandi y pidió que lo contacte el equipo.\n\n"
                "Conversación:\n" + texto_charla(v["charla"][-MAX_TURNOS:], "Visitante")):
            await p.store.set(kc, str(enviados + 1))
    return volver


# --- todos los chats con Skandi (solo la cuenta principal) ---

FILTROS = {"todos": "Todos", "soporte": "Pidieron soporte", "panel": "Clientes", "web": "Visitantes de la web"}
POR_PAGINA = 20

CSS_CHATS = """
.skc-filtros{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1rem}
.skc-filtros a{padding:.45rem .95rem;border-radius:999px;color:#A8A8AE;text-decoration:none;font-size:.9rem;font-weight:600;background:#1F2024}
.skc-filtros a.on{background:var(--acento);color:var(--tinta)}
.skc{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:1.25rem;align-items:start}
.skc-lista,.skc-chat{background:#1F2024;border-radius:1.5rem;padding:1rem}
.skc-fila{display:block;padding:.75rem .9rem;border-radius:1rem;text-decoration:none;color:#F5F5F4;border:1px solid transparent}
.skc-fila:hover{background:#26272C}.skc-fila.on{border-color:var(--acento);background:rgba(255,138,61,.08)}
.skc-fila b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.skc-fila small{color:#A8A8AE;font-size:.8rem;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.skc-marca{background:rgba(255,138,61,.16);color:#FFAB70;border-radius:999px;padding:0 .5rem;font-weight:600}
.skc-chat h2{font-size:1.05rem;margin:.2rem .3rem .9rem}
.skc-chat .sk-msj{margin-bottom:.5rem}
.skc-chat .sk-mensajes{padding:0}
@media(max-width:900px){.skc{grid-template-columns:1fr}}
"""


def quien_es(x: dict) -> str:
    return f"@{x['usuario']}" if x["origen"] == "panel" and x["usuario"] else (
        "Cliente" if x["origen"] == "panel" else "Visitante de la web")


@router.get("/skandi")
async def ver_chats(request: Request, ver: str = "todos", p: int = 1, chat: int = 0):
    from . import panel
    c = panel.cuenta_de_sesion(request)
    if not c:
        return RedirectResponse("/panel", status_code=303)
    if c.slug or (pr := principal()) is None:
        return await panel.no_encontrada(request)
    ver = ver if ver in FILTROS else "todos"
    origen = ver if ver in ("panel", "web") else ""
    p = max(p, 1)
    chats, total = await pr.store.chats_skandi(origen, soporte=ver == "soporte",
                                                desde=(p - 1) * POR_PAGINA, cuantos=POR_PAGINA)
    paginas = max(1, -(-total // POR_PAGINA))

    def enlace(ver_: str = ver, pagina: int = p, chat_: int = 0) -> str:
        return f"/panel/skandi?ver={ver_}" + (f"&p={pagina}" if pagina > 1 else "") + (f"&chat={chat_}" if chat_ else "")

    filtros = "".join(f'<a href="{enlace(k, 1)}"{panel.ACTIVA if k == ver else ""}>{n}</a>'
                      for k, n in FILTROS.items())
    filas = "".join(
        f'<a class="skc-fila{" on" if x["id"] == chat else ""}" href="{escape(enlace(chat_=x["id"]))}">'
        f'<b>{escape(titulo(x))}</b><small>{escape(quien_es(x))} · {cuando(x["actualizado"], c.timezone)}'
        f' · {len(x["mensajes"]) // 2} preguntas'
        + (' <span class="skc-marca">Soporte</span>' if x["soporte"] else "") + "</small></a>"
        for x in chats) or '<p class="vacio">Todavía no hay conversaciones aquí.</p>'
    abierto_ = await pr.store.chat_skandi(chat) if chat else None
    detalle = (
        f'<div class="skc-chat"><h2>{escape(quien_es(abierto_))} · {cuando(abierto_["creado"], c.timezone)}'
        + (' <span class="skc-marca">Soporte</span>' if abierto_["soporte"] else "") + '</h2>'
        '<div class="sk-mensajes">' + "".join(mensaje(r, t) for r, t in abierto_["mensajes"]) + "</div></div>"
        if abierto_ else '<div class="skc-chat"><p class="vacio">Elige una conversación para verla.</p></div>')
    return panel.pagina(
        f"<style>{CSS_CHATS}</style>"
        + await panel.menu_de(c, "cuenta", sub="skandi")
        + '<main><div class="contenedor">'
        f'<nav class="skc-filtros" aria-label="Filtros">{filtros}</nav>'
        f'<div class="skc"><div class="skc-lista">{filas}'
        + panel.paginas(p, paginas, lambda n: enlace(pagina=n)) + f"</div>{detalle}</div>"
        "</div></main>")
