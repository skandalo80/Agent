"""Tu asistente > Conversaciones: la bandeja de DMs del panel.

A la izquierda, los chats de 10 en 10 con buscador y tres pestañas (Sin contestar,
Pidieron una persona, Todas); a la derecha, el chat abierto con el interruptor
Asistente / Tú y la caja para contestar desde aquí. Solo DMs: los comentarios no salen.
Todo es HTML que arma el servidor; el buscador se manda solo mientras escribes con
panel.js (sin fetch: recarga la página), y sin JS se busca con Enter.
"""
import time
from datetime import datetime
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .cuenta import Cuenta, usar
from .store import MANUAL

POR_HOJA = 10
# Mensajes del chat abierto que se cargan de una vez ("Ver mensajes anteriores" suma otros tantos).
POR_TANDA = 40
MAX_MENSAJES = 400
MAX_RESPUESTA = 1000
# Instagram solo deja contestar dentro de las 24 h desde el último mensaje del cliente.
VENTANA = 24 * 3600
# "todas" es explícita: sin `ver`, se abre Sin contestar si hay alguno y si no, Todas.
FILTROS = {"sin": "Sin contestar", "persona": "Pidieron una persona", "todas": "Todas"}
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                "octubre", "noviembre", "diciembre"]

CSS = """
.inbox{display:grid;grid-template-columns:24rem minmax(0,1fr);background:var(--blanco);border-radius:1.5rem;overflow:hidden;min-height:38rem;margin-bottom:1.25rem;scroll-margin-top:1rem}
.inbox .lista{border-right:1px solid var(--linea-clara);display:flex;flex-direction:column;min-width:0}
.inbox .buscar{padding:1rem 1rem .5rem;margin:0}
.inbox input[type=search]{width:100%;box-sizing:border-box;font:inherit;font-size:.95rem;padding:.65rem 1rem .65rem 2.4rem;border:1px solid var(--linea-clara);border-radius:999px;background:var(--claro) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%23C9B8D1' stroke-width='2' viewBox='0 0 24 24'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat .9rem center}
.inbox .buscar button{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.js .inbox .buscar button{display:none}
.filtros-chat{display:flex;gap:.1rem;padding:0 .75rem;overflow-x:auto;border-bottom:1px solid var(--linea-clara);font-size:.82rem}
.filtros-chat a{padding:.7rem .45rem;color:var(--gris);text-decoration:none;font-weight:500;border-bottom:2px solid transparent;white-space:nowrap}
.filtros-chat a.on{color:var(--negro);border-color:var(--acento);font-weight:600}
.filtros-chat b{display:inline-block;min-width:1.2rem;padding:0 .35rem;margin-left:.25rem;border-radius:999px;background:var(--acento);color:var(--tinta);font-size:.7rem;text-align:center;line-height:1.2rem}
.filtros-chat b.gris{background:var(--claro);color:var(--gris)}
.inbox .cuantos{font-size:.75rem;color:var(--gris);padding:.5rem 1rem 0;margin:0}
.conv{display:flex;gap:.75rem;padding:.85rem 1rem;text-decoration:none;color:inherit;border-bottom:1px solid var(--linea-clara);position:relative}
.conv:hover{background:rgba(255,255,255,.04)}.conv.on{background:var(--claro)}
.inbox .avatar{flex:none;width:2.6rem;height:2.6rem;border-radius:50%;background:#4A4756;color:var(--negro);display:grid;place-items:center;font-weight:600;font-size:.95rem}
.conv .txt{min-width:0;flex:1}
.conv .arriba{display:flex;justify-content:space-between;gap:.5rem;font-size:.9rem}
.conv .arriba b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv .arriba small{color:var(--gris);font-size:.75rem;white-space:nowrap}
.conv .ult{display:block;font-size:.85rem;color:var(--texto2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:.1rem 0 .25rem}
.conv.sin .ult{color:var(--negro);font-weight:600}
.conv .lo-tiene{display:block;font-size:.7rem;color:var(--gris)}
.conv .lo-tiene.rojo{color:var(--naranja-t);font-weight:600}
.punto-sin{position:absolute;left:.35rem;top:50%;width:.4rem;height:.4rem;border-radius:50%;background:var(--neon)}
.inbox .vacio{padding:1.5rem 1rem;font-size:.9rem;color:var(--gris)}
.inbox .lista .paginas{margin:auto 0 0;padding:.75rem;justify-content:center}
.hilo{display:flex;flex-direction:column;min-width:0}
.hilo .cab{display:flex;align-items:center;gap:.85rem;padding:1rem 1.5rem;border-bottom:1px solid var(--linea-clara)}
.hilo .cab .nom{flex:1;min-width:0}.hilo .cab .nom small{display:block;color:var(--gris);font-size:.8rem}
.quien-contesta{display:flex;align-items:center;border:1px solid var(--linea-clara);border-radius:999px;padding:.2rem;background:var(--claro);margin:0}
.quien-contesta button{font:inherit;font-size:.85rem;font-weight:600;border:0;background:transparent;padding:.4rem .9rem;border-radius:999px;cursor:pointer;color:var(--gris)}
.quien-contesta button[aria-pressed=true]{background:var(--acento);color:var(--tinta);cursor:default}
.hilo .alerta{margin:1rem 1.5rem 0;padding:.75rem 1rem;border-radius:1rem;background:rgba(243,154,54,.12);border:1px solid rgba(243,154,54,.35);color:#E6E3EC;font-size:.9rem}
.hilo .alerta b,.hilo .alerta strong{color:var(--naranja-t)}
.hilo .alerta.tuyo{background:var(--claro);border-color:var(--linea-clara);color:#E6E3EC}
.hilo .alerta form{display:inline;margin:0}
.hilo .alerta button{font:inherit;font-weight:600;background:none;border:0;padding:0;color:inherit;text-decoration:underline;cursor:pointer}
/* column-reverse: el chat abre ya abajo, en lo más nuevo, sin JS. */
.mensajes{flex:1;padding:1.25rem 1.5rem;background:var(--suave);overflow:auto;max-height:32rem;display:flex;flex-direction:column-reverse}
.mensajes>div{display:flex;flex-direction:column}
.mensajes .dia{text-align:center;font-size:.75rem;color:var(--gris);margin:.5rem 0 1rem}
.mensajes .anteriores{text-align:center;font-size:.85rem;margin-bottom:1rem}
.mensajes .burbuja.de-cliente{background:var(--claro);border:1px solid var(--linea-clara);border-bottom-left-radius:.35rem}
.mensajes .burbuja.de-bot{background:#4A4756;color:var(--negro);margin-left:auto;border-bottom-right-radius:.35rem}
.mensajes .burbuja.de-ti{background:var(--acento);color:var(--tinta);margin-left:auto;border-bottom-right-radius:.35rem}
.mensajes .hora-msg{font-size:.7rem;color:var(--gris);margin:-.3rem 0 .8rem;text-align:right}
.mensajes .hora-msg.izq{text-align:left}
.responder{border-top:1px solid var(--linea-clara);padding:1rem 1.5rem;margin:0}
.responder label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.responder textarea{width:100%;box-sizing:border-box;font:inherit;font-size:.95rem;border:1px solid var(--linea-clara);border-radius:1rem;padding:.75rem 1rem;resize:vertical;min-height:4.5rem}
.responder .pie{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:.6rem}
.responder .pie span{font-size:.8rem;color:var(--gris)}
.responder.cerrado{font-size:.9rem;color:var(--gris)}
.hilo .elige{flex:1;display:grid;place-items:center;text-align:center;color:var(--gris);padding:2rem}
.volver{display:none}
@media(max-width:860px){.inbox{grid-template-columns:minmax(0,1fr)}.inbox .lista{border-right:0}.inbox.abierto .lista{display:none}.inbox:not(.abierto) .hilo{display:none}
.volver{display:block;padding:.75rem 1.25rem 0;font-size:.9rem}.hilo .cab{flex-wrap:wrap;padding:.75rem 1.25rem}.hilo .cab .nom{flex-basis:calc(100% - 3.5rem)}
.quien-contesta{flex-basis:100%;justify-content:center}.quien-contesta button{flex:1}.mensajes,.responder{padding-left:1.25rem;padding-right:1.25rem}.hilo .alerta{margin:.75rem 1.25rem 0}}
"""


def enlace(ver: str, buscar: str = "", hoja: int = 1, chat: str = "", n: int = 0) -> str:
    """Dirección de la bandeja con su filtro, búsqueda, página y chat abierto."""
    q = urlencode({k: v for k, v in (("ver", ver), ("q", buscar), ("p", hoja if hoja > 1 else ""),
                                     ("chat", chat), ("n", n or "")) if v})
    return "/panel/asistente" + ("?" + q if q else "") + "#chats"


def _hora(t: float, zona: ZoneInfo, ahora: datetime) -> str:
    """Hora corta de la lista: 13:02, ayer, lun o 19 oct."""
    d = datetime.fromtimestamp(t, zona)
    dias = (ahora.date() - d.date()).days
    if dias <= 0:
        return d.strftime("%H:%M")
    if dias == 1:
        return "ayer"
    if dias < 7:
        return DIAS[d.weekday()]
    return f"{d.day} {MESES[d.month - 1]}" + (f" {d.year}" if d.year != ahora.year else "")


def _dia(d: datetime, ahora: datetime) -> str:
    dias = (ahora.date() - d.date()).days
    if dias <= 0:
        return "Hoy"
    if dias == 1:
        return "Ayer"
    return f"{d.day} de {MESES_LARGOS[d.month - 1]}" + (f" de {d.year}" if d.year != ahora.year else "")


def _ocultos(ver: str, buscar: str, hoja: int, chat: str = "") -> str:
    """Campos para volver a la misma bandeja después de un botón."""
    return "".join(f'<input type="hidden" name="{k}" value="{escape(str(v))}">'
                   for k, v in (("ver", ver), ("q", buscar), ("p", hoja if hoja > 1 else ""), ("chat", chat)) if v)


async def bandeja(c: Cuenta, ver: str = "", buscar: str = "", hoja: int = 1, chat: str = "", n: int = 0) -> str:
    import asyncio

    from . import panel
    ahora = time.time()
    zona = ZoneInfo(c.timezone)
    hoy = datetime.fromtimestamp(ahora, zona)
    buscar = buscar.strip()[:60]
    # Quién pidió una persona: el asistente lo pasó contigo (pasar_a_humano) durante su pausa actual.
    humano_desde = ahora - c.human_pause_hours * 3600 - 60

    async def pagina(filtro: str, n_: int):
        return await c.store.chats_page(ahora, ahora - VENTANA, humano_desde, "" if filtro == "todas" else filtro,
                                        buscar.lstrip("@"), POR_HOJA, (n_ - 1) * POR_HOJA)
    if ver not in FILTROS:
        # Sin filtro (o un enlace viejo): Sin contestar si hay alguno; si no, Todas.
        _, cuantos, _ = await c.store.chats_page(ahora, ahora - VENTANA, humano_desde, "sin", "", 1, 0)
        ver = "sin" if cuantos["sin"] else "todas"
    filas, cuantos, encontrados = await pagina(ver, max(1, hoja))
    total = max(1, -(-encontrados // POR_HOJA))
    if hoja > total or hoja < 1:
        hoja = min(max(1, hoja), total)
        filas, cuantos, encontrados = await pagina(ver, hoja)
    with usar(c):
        nombres = await asyncio.gather(*(c.instagram.username(f[0]) for f in filas))

    lo_tiene = {"persona": ("Pidió hablar con una persona", " rojo"), "sin": ("Sin contestar · Asistente", ""),
                "tu": ("Contestaste tú · Asistente", ""), "asistente": ("Asistente", "")}
    conv = []
    for (u, t, rol, texto, _, hasta, est), nombre in zip(filas, nombres):
        nombre = nombre if nombre.startswith("@") else "Cliente de Instagram"
        if rol == "assistant":
            manual = texto.startswith(MANUAL)
            texto = ("Tú: " + texto[len(MANUAL):].strip()) if manual else "Asistente: " + texto
        ult = escape(texto[:140]) if texto else "Sin mensajes todavía"
        if est == "pausa":
            quien, color = f"Lo atiendes tú · {max(1, round((hasta - ahora) / 3600))} h", ""
        else:
            quien, color = lo_tiene[est]
        conv.append(
            f'<a class="conv{" sin" if est == "sin" else ""}{" on" if u == chat else ""}"'
            f' href="{escape(enlace(ver, buscar, hoja, u))}"' + (' aria-current="true"' if u == chat else "") + ">"
            + ('<span class="punto-sin"></span>' if est == "sin" else "")
            + f'<span class="avatar">{escape(nombre[1:2].upper() or "?")}</span><span class="txt">'
            f'<span class="arriba"><b>{escape(nombre)}</b><small>{_hora(t, zona, hoy) if t else ""}</small></span>'
            f'<span class="ult">{ult}</span><span class="lo-tiene{color}">{quien}</span></span></a>')
    pestanas = "".join(
        f'<a href="{escape(enlace(k, buscar))}"' + (' class="on" aria-current="page"' if k == ver else "") + ">"
        f'{nombre}<b class="{"gris" if k == "todas" or not cuantos[k if k != "todas" else ""] else ""}">'
        f'{cuantos[k if k != "todas" else ""]}</b></a>' for k, nombre in FILTROS.items())
    vacio = {"todas": "Aún no te escribe nadie. Aquí vas a ver tus chats en cuanto lleguen.",
             "persona": "Nadie: ningún chat está esperando a que le contestes tú.",
             "sin": "Ninguno: todos tus chats tienen respuesta."}[ver]
    if buscar:
        vacio = f"No encontramos chats con «{escape(buscar)}»."
    busqueda = ('<form method="get" action="/panel/asistente#chats" class="buscar" role="search" data-vivo>'
                + _ocultos(ver, "", 1, chat)
                + f'<input type="search" name="q" value="{escape(buscar)}" placeholder="Buscar un cliente o un mensaje"'
                ' aria-label="Buscar un cliente o un mensaje" maxlength="60" autocomplete="off">'
                '<button>Buscar</button></form>')
    cuantos_txt = (f'<p class="cuantos">{encontrados} {"chat" if encontrados == 1 else "chats"}'
                   + (f" · página {hoja} de {total}" if total > 1 else "") + "</p>") if buscar and encontrados else ""
    lista = (f'<div class="lista">{busqueda}<nav class="filtros-chat" aria-label="Filtros">{pestanas}</nav>{cuantos_txt}'
             + ("".join(conv) or f'<p class="vacio">{vacio}</p>')
             + panel.paginas(hoja, total, lambda x: enlace(ver, buscar, x, chat)) + "</div>")
    hilo = await _hilo(c, chat, ver, buscar, hoja, n, ahora, zona, hoy, humano_desde) if chat else ""
    if not hilo:
        chat = ""
        hilo = '<div class="hilo"><p class="elige">Elige una conversación de la lista para verla y contestar.</p></div>'
    return (f'<div class="inbox{" abierto" if chat else ""}" id="chats">{lista}{hilo}</div>')


async def _hilo(c: Cuenta, u: str, ver: str, buscar: str, hoja: int, n: int, ahora: float, zona: ZoneInfo,
                hoy: datetime, humano_desde: float) -> str:
    """El chat abierto. Vacío si ese chat no es de esta cuenta."""
    hasta = await c.store.paused_until(u)
    en_pausa = hasta > ahora
    if u.startswith("admin:") or not (en_pausa or await c.store.has_chat(u)):
        return ""
    with usar(c):
        nombre = await c.instagram.username(u)
    nombre = nombre if nombre.startswith("@") else "Cliente de Instagram"
    n = min(max(POR_TANDA, n), MAX_MENSAJES)
    mensajes, hay_mas = await c.store.chat_messages(u, n)
    ultimo_cliente = await c.store.last_message_from(u)
    motivo = ""
    if en_pausa:
        pidio = [d for x, d, _ in await c.store.events("humano", humano_desde, ahora + 1) if x == u]
        motivo = pidio[-1].get("motivo", "") if pidio else None
    horas = max(1, round((hasta - ahora) / 3600))
    ocultos = _ocultos(ver, buscar, hoja, u) + f'<input type="hidden" name="usuario" value="{escape(u)}">'

    from . import panel
    sub = (f"Último mensaje {panel.hace(ultimo_cliente)}" if ultimo_cliente else "Aún no te escribe")
    if nombre.startswith("@"):
        sub += (f' · <a class="enlace" href="https://ig.me/m/{escape(nombre[1:])}" target="_blank" rel="noopener">'
                "Abrir en Instagram</a>")
    interruptor = (
        '<form method="post" action="/panel/pausas" class="quien-contesta" aria-label="Quién contesta este chat">'
        + ocultos
        + f'<button name="accion" value="reactivar" aria-pressed="{"false" if en_pausa else "true"}"'
        + ("" if en_pausa else " disabled") + ">Asistente</button>"
        + f'<button name="accion" value="tomar" aria-pressed="{"true" if en_pausa else "false"}"'
        + (" disabled" if en_pausa else "") + ">Tú</button></form>")
    extender = (f'<form method="post" action="/panel/pausas">{ocultos}'
                f'<button name="accion" value="extender">Darte {c.human_pause_hours:g} h más</button></form>')
    if en_pausa and motivo is not None:
        alerta = ('<div class="alerta"><b>Pidió hablar con una persona.</b> '
                  + (f"Motivo: {escape(motivo[:200])}. " if motivo else "")
                  + f"Tu asistente no le contesta en las próximas {horas} h. {extender}</div>")
    elif en_pausa:
        alerta = (f'<div class="alerta tuyo">Lo atiendes tú: tu asistente no le contesta en las próximas {horas} h.'
                  f" {extender}</div>")
    else:
        alerta = ""

    burbujas, dia_antes = [], None
    if hay_mas and n < MAX_MENSAJES:
        burbujas.append(f'<p class="anteriores"><a class="enlace" href="{escape(enlace(ver, buscar, hoja, u, n + POR_TANDA))}">'
                        "Ver mensajes anteriores</a></p>")
    for rol, texto, t in mensajes:
        d = datetime.fromtimestamp(t, zona)
        if d.date() != dia_antes:
            burbujas.append(f'<p class="dia">{_dia(d, hoy)}</p>')
            dia_antes = d.date()
        hora = d.strftime("%H:%M")
        if rol == "user":
            burbujas.append(f'<div class="burbuja de-cliente">{escape(texto)}</div><p class="hora-msg izq">{hora}</p>')
        elif texto.startswith(MANUAL):
            burbujas.append(f'<div class="burbuja de-ti">{escape(texto[len(MANUAL):].strip())}</div>'
                            f'<p class="hora-msg">Tú · {hora}</p>')
        else:
            burbujas.append(f'<div class="burbuja de-bot">{escape(texto)}</div><p class="hora-msg">Asistente · {hora}</p>')
    if not mensajes:
        burbujas.append('<p class="dia">Aún no hay mensajes guardados en este chat.</p>')

    if ultimo_cliente and ahora - ultimo_cliente < VENTANA:
        responder = (
            f'<form method="post" action="/panel/responder" class="responder">{ocultos}'
            f'<label for="respuesta">Tu respuesta</label>'
            f'<textarea id="respuesta" name="texto" maxlength="{MAX_RESPUESTA}" required'
            f' placeholder="Escribe tu respuesta a {escape(nombre)}"></textarea>'
            '<div class="pie"><span>Le llega por Instagram como mensaje tuyo. Al enviarlo, tu asistente deja de'
            f" contestar en este chat {c.human_pause_hours:g} h.</span>"
            '<button class="boton">Enviar</button></div></form>')
    else:
        responder = ('<p class="responder cerrado">Instagram solo deja contestar dentro de las 24 horas desde el último'
                     " mensaje del cliente. Cuando te vuelva a escribir, puedes contestarle desde aquí.</p>")
    return (f'<div class="hilo"><a class="volver enlace" href="{escape(enlace(ver, buscar, hoja))}">‹ Tus chats</a>'
            f'<div class="cab"><span class="avatar">{escape(nombre[1:2].upper() or "?")}</span>'
            f'<div class="nom"><b>{escape(nombre)}</b><small>{sub}</small></div>{interruptor}</div>'
            f'{alerta}<div class="mensajes"><div>{"".join(burbujas)}</div></div>{responder}</div>')

