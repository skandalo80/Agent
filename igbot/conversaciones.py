"""Tu asistente > Conversaciones: la bandeja de DMs del panel.

A la izquierda, los chats de 10 en 10 con buscador, arriba quién los atiende (Tú: Nuevos, Sin
contestar y Todas, con la razón por la que te los pasó el asistente o por la que no pudo contestar;
Tu asistente: En curso y Todas, con la etiqueta "Pregunta sin dato" cuando le faltó un dato y te avisó
sin soltar el chat); a la derecha, el chat abierto con el interruptor
Asistente / Tú y la caja para contestar desde aquí. Solo DMs: los comentarios no salen.
Todo es HTML que arma el servidor; el buscador se manda solo mientras escribes con
panel.js (sin fetch: recarga la página), y sin JS se busca con Enter.
En vivo: panel.js escucha /panel/vivo (EventSource) y, cuando llega o sale un mensaje, cambia la
lista y el chat abierto con el HTML que manda el servidor, sin recargar.
"""
import time
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import config
from .cuenta import Cuenta, usar
from .store import MANUAL

POR_HOJA = 10
# Mensajes del chat abierto que se cargan de una vez ("Ver mensajes anteriores" suma otros tantos).
POR_TANDA = 40
MAX_MENSAJES = 400
MAX_RESPUESTA = 1000
# Instagram solo deja contestar dentro de las 24 h desde el último mensaje del cliente.
VENTANA = 24 * config.HORA
VENTANA_HUMANO = 7 * 24 * config.HORA  # con la etiqueta Human Agent (config.HUMAN_AGENT_TAG) una persona puede contestar 7 días


def ventana_panel() -> float:
    """Hasta cuándo puede contestar el dueño desde el panel, desde el último mensaje del cliente."""
    return VENTANA_HUMANO if config.HUMAN_AGENT_TAG else VENTANA
# Arriba, quién atiende: "Tú" (el chat está en pausa) o "Tu asistente"; debajo, sus pestañas.
# Sin `ver` se abre "Tú" si algo espera tu respuesta; si no, "Tu asistente".
FILTROS = {"nuevos": "Nuevos", "sin": "Sin contestar", "tuyas": "Todas", "curso": "En curso", "bot": "Todas"}
LADO = {"nuevos": "tu", "sin": "tu", "tuyas": "tu", "curso": "bot", "bot": "bot"}
AVISAN = {"nuevos", "sin"}  # las pestañas cuyo número sale en naranja
# Un mensaje del cliente sin respuesta del asistente después de esto pasa a "Tú", en Sin contestar.
SIN_RESPUESTA = 120


def primero(cuantos: dict, lado: str) -> str:
    """La pestaña con la que abre un lado: la primera que pide atención y tiene algo; si no, En curso / Todas."""
    if lado == "tu":
        return "nuevos" if cuantos["nuevos"] else "sin" if cuantos["sin"] else "tuyas"
    return "curso" if cuantos["curso"] else "bot"
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                "octubre", "noviembre", "diciembre"]

CSS = (Path(__file__).resolve().parent / "static" / "css" / "conversaciones.css").read_text(encoding="utf-8")


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
    """La bandeja completa. Con JS, panel.js abre /panel/vivo (data-vivo) y cambia la lista y el
    chat abierto cuando llega algo, sin recargar."""
    p = await partes(c, ver, buscar, hoja, chat, n)
    vivo = enlace(p["ver"], p["buscar"], p["hoja"], p["chat"], n).replace("/panel/asistente", "/panel/vivo", 1)
    vivo = vivo.split("#")[0] + ("&" if "?" in vivo else "?") + f"v={c.store.cambios}"
    hilo = p["hilo"] or ('<div class="hilo"><p class="elige">Elige una conversación de la lista para verla y'
                         " contestar.</p></div>")
    return (f'<div class="inbox{" abierto" if p["hilo"] else ""}" id="chats" data-vivo="{escape(vivo)}">'
            f'<div class="lista">{p["busqueda"]}<div class="lista-vivo">{p["lista"]}</div></div>{hilo}</div>')


async def partes(c: Cuenta, ver: str = "", buscar: str = "", hoja: int = 1, chat: str = "", n: int = 0) -> dict:
    """Las piezas de la bandeja: el buscador, la lista (pestañas, chats y páginas) y el chat abierto
    ("" si no hay o no es de esta cuenta). También el filtro, la página y el chat que quedaron."""
    import asyncio

    from . import panel
    ahora = time.time()
    zona = ZoneInfo(c.timezone)
    hoy = datetime.fromtimestamp(ahora, zona)
    buscar = buscar.strip()[:60]
    # Quién pidió una persona: el asistente lo pasó contigo (pasar_a_humano) durante su pausa actual.
    humano_desde = ahora - c.human_pause_hours * config.HORA - 60

    from .limites import LIMIT_NOTICE
    from .tools import RAZONES

    async def pagina(filtro: str, n_: int, buscar_: str = buscar, limite: int = POR_HOJA):
        return await c.store.chats_page(ahora, ahora - VENTANA, humano_desde, ahora - SIN_RESPUESTA, LIMIT_NOTICE,
                                        filtro, buscar_.lstrip("@"), limite, (n_ - 1) * limite)
    if ver not in FILTROS:
        # Sin filtro (o un enlace viejo): lo que espera tu respuesta; si no hay nada, lo del asistente.
        _, cuantos, _ = await pagina("nuevos", 1, "", 1)
        ver = primero(cuantos, "tu" if cuantos["nuevos"] or cuantos["sin"] else "bot")
    filas, cuantos, encontrados = await pagina(ver, max(1, hoja))
    total = max(1, -(-encontrados // POR_HOJA))
    if hoja > total or hoja < 1:
        hoja = min(max(1, hoja), total)
        filas, cuantos, encontrados = await pagina(ver, hoja)
    with usar(c):
        nombres = await asyncio.gather(*(c.instagram.username(f[0]) for f in filas))
    # Por qué te lo pasó el asistente (la etiqueta de cada chat del lado "Tú").
    pasados = {f[0] for f in filas if f[6]}
    razones = {u: d for u, d, _ in await c.store.events("humano", humano_desde, ahora + 1) if u in pasados} if pasados else {}
    # Los que llegaron por un anuncio de "Enviar mensaje".
    anuncios = await c.store.anuncios_de([f[0] for f in filas])
    # Lo que el asistente no supo contestar y te avisó (pregunta_sin_dato): él sigue con el chat.
    sin_dato = await c.store.sin_dato(ahora - VENTANA)

    conv = []
    for (u, t, rol, texto, hasta, est, paso), nombre in zip(filas, nombres):
        nombre = nombre if nombre.startswith("@") or u.startswith("fb:") else "Cliente de Instagram"
        limite = texto == LIMIT_NOTICE
        if rol == "assistant":
            manual = texto.startswith(MANUAL)
            texto = ("Tú: " + texto[len(MANUAL):].strip()) if manual else "Asistente: " + texto
        ult = escape(texto[:140]) if texto else "Sin mensajes todavía"
        if est in ("nuevo", "tu_sin", "tu"):
            razon = razones.get(u, {}).get("razon") if paso else None
            etiqueta = (f'<span class="razon{" molesto" if razon == "molesto" else ""}">'
                        f'{RAZONES.get(razon, "Te lo pasó el asistente") if paso else "Lo tomaste tú"}</span>')
            quien = {"nuevo": "Nuevo", "tu_sin": "Sin contestar"}.get(est, "Lo atiendes tú")
            quien += f" · {max(1, round((hasta - ahora) / 3600))} h"
        elif est == "revisar":
            etiqueta = f'<span class="razon">{"Límite del plan" if limite else "El asistente no respondió"}</span>'
            quien = "Sin contestar"
        elif u in sin_dato:
            pregunta = sin_dato[u][-1][0]
            etiqueta = f'<span class="razon dato" title="{escape(pregunta)}">Pregunta sin dato</span>'
            quien = "Asistente"
        else:
            etiqueta, quien = "", "Asistente"
        if u in anuncios:
            etiqueta = '<span class="razon anuncio">Anuncio</span>' + etiqueta
        aviso = est in ("nuevo", "tu_sin", "revisar") or (est == "asistente" and u in sin_dato)
        conv.append(
            f'<a class="conv{" sin" if aviso else ""}{" on" if u == chat else ""}"'
            f' href="{escape(enlace(ver, buscar, hoja, u))}"' + (' aria-current="true"' if u == chat else "") + ">"
            + ('<span class="punto-sin"></span>' if aviso else "")
            + f'<span class="avatar">{escape(nombre[1:2].upper() or "?")}</span><span class="txt">'
            f'<span class="arriba"><b>{escape(nombre)}</b><small>{_hora(t, zona, hoy) if t else ""}</small></span>'
            f'<span class="ult">{ult}</span><span class="lo-tiene">{etiqueta}{quien}</span></span></a>')
    lado = LADO[ver]
    pendientes = cuantos["nuevos"] + cuantos["sin"]
    # Del lado del asistente, cuántos chats tienen una pregunta sin dato abierta (y siguen con él).
    activos = {u for u, *_ in await c.store.paused_list(ahora)}
    sin_contestar = len(set(sin_dato) - activos)
    numero = {"tu": pendientes, "bot": sin_contestar}
    lados = "".join(
        f'<a href="{escape(enlace(primero(cuantos, k), buscar))}"' + (' class="on" aria-current="true"' if k == lado else "")
        + f">{nombre}" + (f"<b>{numero[k]}</b>" if numero[k] else "") + "</a>"
        for k, nombre in (("tu", "Tú"), ("bot", "Tu asistente")))
    pestanas = "".join(
        f'<a href="{escape(enlace(k, buscar))}"' + (' class="on" aria-current="page"' if k == ver else "") + ">"
        f'{nombre}<b class="{"" if cuantos[k] and k in AVISAN else "gris"}">{cuantos[k]}</b></a>'
        for k, nombre in FILTROS.items() if LADO[k] == lado)
    vacio = {"nuevos": "Ninguno: el asistente no te ha pasado chats nuevos.",
             "sin": "Ninguno: ya contestaste todos los chats que atiendes tú.",
             "tuyas": "Ninguno: tu asistente está atendiendo todos tus chats.",
             "curso": "Nadie ha escrito en las últimas 24 horas.",
             "bot": "Aún no te escribe nadie. Aquí vas a ver tus chats en cuanto lleguen."}[ver]
    if buscar:
        vacio = f"No encontramos chats con «{escape(buscar)}»."
    busqueda = ('<form method="get" action="/panel/asistente#chats" class="buscar" role="search" data-vivo>'
                + _ocultos(ver, "", 1, chat)
                + f'<input type="search" name="q" value="{escape(buscar)}" placeholder="Buscar un cliente o un mensaje"'
                ' aria-label="Buscar un cliente o un mensaje" maxlength="60" autocomplete="off">'
                '<button>Buscar</button></form>')
    cuantos_txt = (f'<p class="cuantos">{encontrados} {"chat" if encontrados == 1 else "chats"}'
                   + (f" · página {hoja} de {total}" if total > 1 else "") + "</p>") if buscar and encontrados else ""
    lista = (f'<nav class="lados" aria-label="Quién atiende">{lados}</nav>'
             f'<nav class="filtros-chat" aria-label="Filtros">{pestanas}</nav>{cuantos_txt}'
             + ("".join(conv) or f'<p class="vacio">{vacio}</p>')
             + panel.paginas(hoja, total, lambda x: enlace(ver, buscar, x, chat)))
    hilo = await _hilo(c, chat, ver, buscar, hoja, n, ahora, zona, hoy, humano_desde, sin_dato.get(chat, [])) if chat else ""
    return {"busqueda": busqueda, "lista": lista, "hilo": hilo, "ver": ver, "buscar": buscar, "hoja": hoja,
            "chat": chat if hilo else "", "pendientes": pendientes}


async def _vendido(c: Cuenta, ocultos: str, ventas: list[dict]) -> str:
    """"Marcar venta": para lo que se cobra fuera de Skandar (en persona, por transferencia). Cuenta en
    Resultados > Anuncios como venta del anuncio que trajo este chat; las marcadas aquí se pueden quitar."""
    from .empresa import MONEDAS, productos
    from .tienda_shopify import monto
    moneda = next((p["moneda"] for p in await productos(c) if p.get("moneda") in MONEDAS), MONEDAS[0])
    opciones = "".join(f'<option{" selected" if m == moneda else ""}>{m}</option>' for m in MONEDAS)
    marcadas = "".join(
        f'<form method="post" action="/panel/venta#chats" class="marcada">{ocultos}'
        f'<input type="hidden" name="externo" value="{escape(v["externo"])}">'
        f'<span>Vendido {monto(v["monto"], v["moneda"])}</span>'
        '<button name="accion" value="quitar" class="enlace">Quitar</button></form>'
        for v in ventas if v["origen"] == "manual")
    return ('<details class="venta-chat"><summary>Marcar venta</summary><div class="caja">' + marcadas
            + f'<form method="post" action="/panel/venta#chats">{ocultos}'
            '<label for="venta-monto">¿Por cuánto le vendiste?</label><div class="fila">'
            '<input id="venta-monto" name="monto" type="number" min="1" step="0.01" required inputmode="decimal">'
            f'<select name="moneda" aria-label="Moneda">{opciones}</select></div>'
            '<button class="boton chico" name="accion" value="marcar">Guardar venta</button>'
            "<small>Se suma al anuncio que trajo este chat, en Resultados.</small></form></div></details>")


async def _hilo(c: Cuenta, u: str, ver: str, buscar: str, hoja: int, n: int, ahora: float, zona: ZoneInfo,
                hoy: datetime, humano_desde: float, preguntas: list[tuple[str, float]] = ()) -> str:
    """El chat abierto. Vacío si ese chat no es de esta cuenta."""
    hasta = await c.store.paused_until(u)
    en_pausa = hasta > ahora
    if u.startswith("admin:") or not (en_pausa or await c.store.has_chat(u)):
        return ""
    with usar(c):
        nombre = await c.instagram.username(u)
    nombre = nombre if nombre.startswith("@") or u.startswith("fb:") else "Cliente de Instagram"
    n = min(max(POR_TANDA, n), MAX_MENSAJES)
    mensajes, hay_mas = await c.store.chat_messages(u, n)
    ultimo_cliente = await c.store.last_message_from(u)
    pidio = None  # lo que dijo el asistente al pasártelo (pasar_a_humano), si fue así
    if en_pausa:
        pidio = ([d for x, d, _ in await c.store.events("humano", humano_desde, ahora + 1) if x == u] or [None])[-1]
    horas = max(1, round((hasta - ahora) / config.HORA))
    ocultos = _ocultos(ver, buscar, hoja, u) + f'<input type="hidden" name="usuario" value="{escape(u)}">'

    from . import panel
    sub = (f"Último mensaje {panel.hace(ultimo_cliente)}" if ultimo_cliente else "Aún no te escribe")
    if anuncio := (await c.store.anuncios_de([u])).get(u):
        sub += " · Llegó por " + (f"el anuncio «{escape(anuncio['titulo'][:80])}»" if anuncio["titulo"] else "un anuncio")
    if ventas := await c.store.ventas_de(u):
        from .tienda_shopify import monto
        donde = " en tu tienda" if all(v["origen"] == "shopify" for v in ventas) else ""
        sub += f" · Compró {monto(sum(v['monto'] for v in ventas), ventas[-1]['moneda'])}{donde}"
    if nombre.startswith("@"):
        sub += (f' · <a class="enlace" href="https://ig.me/m/{escape(nombre[1:])}" target="_blank" rel="noopener">'
                "Abrir en Instagram</a>")
    interruptor = (
        '<form method="post" action="/panel/pausas#chats" class="quien-contesta" aria-label="Quién contesta este chat">'
        + ocultos
        + f'<button name="accion" value="reactivar" aria-pressed="{"false" if en_pausa else "true"}"'
        + ("" if en_pausa else " disabled") + ">Asistente</button>"
        + f'<button name="accion" value="tomar" aria-pressed="{"true" if en_pausa else "false"}"'
        + (" disabled" if en_pausa else "") + ">Tú</button></form>")
    vendido = await _vendido(c, ocultos, ventas)
    extender = (f'<form method="post" action="/panel/pausas#chats">{ocultos}'
                f'<button name="accion" value="extender">Darte {c.human_pause_hours:g} h más</button></form>')
    if pidio is not None:
        from .tools import RAZONES
        motivo = pidio.get("motivo", "")
        alerta = (f'<div class="alerta"><b>Te lo pasó el asistente: {RAZONES.get(pidio.get("razon"), "pidió una persona")}.</b> '
                  + (f"Motivo: {escape(motivo[:200])}. " if motivo else "")
                  + f"Tu asistente no le contesta en las próximas {horas} h. {extender}</div>")
    elif not en_pausa and (fallo := await c.store.ultimo_evento(u, "sin_respuesta")) and mensajes \
            and mensajes[-1][0] == "user" and fallo[1] >= mensajes[-1][2] - 1:
        porque = ("la cuenta llegó a su gasto máximo de hoy" if fallo[0].get("motivo") == "presupuesto"
                  else "falló varias veces al generar la respuesta")
        alerta = ('<div class="alerta"><b>El asistente no pudo contestar:</b> ' + porque + ". Se le avisó a la persona"
                  " que se le contesta en un momento; contéstale tú aquí abajo o espera a que el asistente pueda.</div>")
    elif preguntas and not en_pausa:
        cuales = " ".join(f"«{escape(p[:200])}»" for p, _ in preguntas[-3:])
        alerta = (f'<div class="alerta"><b>Pregunta sin dato:</b> {cuales}. Tu asistente le dijo que lo consulta y'
                  " sigue atendiendo el chat. Si quieres, contéstale tú aquí abajo."
                  f' <form method="post" action="/panel/pausas#chats">{ocultos}'
                  '<button name="accion" value="dato_listo">Marcar como resuelta</button></form></div>')
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

    if ultimo_cliente and ahora - ultimo_cliente < ventana_panel():
        if preguntas and not en_pausa:
            # Está aclarando un dato que el asistente no tenía: por omisión el asistente sigue con el chat y la
            # respuesta se guarda como pregunta frecuente, para que la próxima vez ya la sepa.
            pregunta = preguntas[-1][0][:300]
            opciones = (
                f'<input type="hidden" name="pregunta" value="{escape(pregunta)}">'
                '<label class="opcion"><input type="checkbox" name="seguir" value="1" checked> Que tu asistente siga'
                " contestando este chat (si lo quitas, lo atiendes tú las próximas"
                f" {c.human_pause_hours:g} h)</label>"
                '<label class="opcion"><input type="checkbox" name="guardar_faq" value="1" checked> Guardar como'
                f" pregunta frecuente: tu asistente responderá así cuando le pregunten «{escape(pregunta[:120])}»</label>")
            pie = "Le llega por Instagram como mensaje tuyo."
        else:
            opciones = ""
            pie = ("Le llega por Instagram como mensaje tuyo. Al enviarlo, tu asistente deja de contestar en este"
                   f" chat {c.human_pause_hours:g} h.")
        responder = (
            f'<form method="post" action="/panel/responder#chats" class="responder">{ocultos}'
            f'<label for="respuesta">Tu respuesta</label>'
            f'<textarea id="respuesta" name="texto" maxlength="{MAX_RESPUESTA}" required'
            f' placeholder="Escribe tu respuesta a {escape(nombre)}"></textarea>{opciones}'
            f'<div class="pie"><span>{pie}</span>'
            '<button class="boton">Enviar</button></div></form>')
    else:
        plazo = "los 7 días" if config.HUMAN_AGENT_TAG else "las 24 horas"
        responder = (f'<p class="responder cerrado">Instagram solo deja contestar dentro de {plazo} desde el último'
                     " mensaje del cliente. Cuando te vuelva a escribir, puedes contestarle desde aquí.</p>")
    return (f'<div class="hilo"><a class="volver enlace" href="{escape(enlace(ver, buscar, hoja))}">‹ Tus chats</a>'
            f'<div class="cab"><span class="avatar">{escape(nombre[1:2].upper() or "?")}</span>'
            f'<div class="nom"><b>{escape(nombre)}</b><small>{sub}</small></div>{vendido}{interruptor}</div>'
            f'{alerta}<div class="mensajes"><div>{"".join(burbujas)}</div></div>{responder}</div>')

