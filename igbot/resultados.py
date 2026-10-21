"""Resultados: lo que hizo el asistente, en el panel del cliente.

bot.skandar.online/panel/resultados, para todos los planes, con los últimos 7
o 30 días:
- Conversaciones: personas que escribieron (y cuántas nuevas), mensajes,
  cuántas resolvió el asistente sin pasar a una persona, su tiempo de
  respuesta (mediana) y cuántas veces contestó fuera del horario de atención.
- Gráficas: mensajes por día y a qué hora escriben (dentro y fuera de horario).
- Si el plan tiene contenido (Community y Pro): comentarios, DMs que salieron
  de un comentario, palabras clave, spam ocultado y publicaciones.

Solo lee lo que ya está en la base de la cuenta: no llama a Instagram ni a
Claude. Los números se guardan CACHE_SEGUNDOS en memoria para que recargar la
página no repita las consultas. Las gráficas son SVG hechas aquí (el CSP del
bot no deja JavaScript; sus estilos están en panel.ESTILO); el detalle de cada barra sale al pasar el cursor.
"""
import time
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import panel
from .comentarios import SPAM
from .cuenta import Cuenta

router = APIRouter(prefix="/panel/resultados")

RANGOS = {"7": "7 días", "30": "30 días"}
CACHE_SEGUNDOS = 600
_cache: dict[tuple[str, str], tuple[float, dict]] = {}

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# --- números ---

def horario(c: Cuenta) -> tuple[int, int]:
    """El horario de atención en minutos desde medianoche (BUSINESS_HOURS, p. ej. 09:00-18:00)."""
    inicio, fin = c.business_hours.split("-")
    minutos = [int(h) * 60 + int(m) for h, m in (t.strip().split(":") for t in (inicio, fin))]
    return minutos[0], minutos[1]


def en_horario(momento: datetime, c: Cuenta) -> bool:
    inicio, fin = horario(c)
    return momento.isoweekday() in c.business_days and inicio <= momento.hour * 60 + momento.minute < fin


def dias_de_horario(c: Cuenta) -> str:
    """"lun–vie 9:00–18:00", para que el cliente sepa con qué horario se cuenta."""
    dias = sorted(c.business_days)
    if dias and dias == list(range(dias[0], dias[-1] + 1)) and len(dias) > 2:
        nombres = f"{DIAS[dias[0] - 1]}–{DIAS[dias[-1] - 1]}"
    else:
        nombres = ", ".join(DIAS[d - 1] for d in dias)
    inicio, fin = horario(c)
    return f"{nombres} {inicio // 60}:{inicio % 60:02d}–{fin // 60}:{fin % 60:02d}"


def mediana(cuantas: Counter) -> float | None:
    """La mediana de unas duraciones contadas por segundo (así no se guarda cada una)."""
    n = sum(cuantas.values())
    if not n:
        return None
    medio, vistos = [], 0
    for d in sorted(cuantas):
        antes, vistos = vistos, vistos + cuantas[d]
        medio += [d] * max(0, min(vistos, n // 2 + 1) - max(antes, (n - 1) // 2))
        if vistos > n // 2:
            break
    return sum(medio) / len(medio)


async def conversaciones(filas, c: Cuenta, inicio: datetime, dias: int) -> dict:
    """Lo que sale de recorrer los mensajes en orden: por día, por hora, tiempos y quién atendió.
    `filas` llega por partes (Store.message_times), así que nunca están todos en memoria; solo se
    guardan contadores y quién está esperando respuesta."""
    zona = inicio.tzinfo
    por_dia = [0] * dias
    por_hora = [0] * 24
    esperando: dict[str, float] = {}  # el primer mensaje del cliente que sigue sin respuesta
    tiempos: Counter = Counter()  # segundos de espera -> cuántas respuestas
    fuera = contestados = 0
    escribieron: set[str] = set()
    atendidas_a_mano: set[str] = set()
    con_respuesta: set[str] = set()
    async for usuario, rol, t, a_mano in filas:
        if rol == "user":
            momento = datetime.fromtimestamp(t, zona)
            dia = (momento.date() - inicio.date()).days
            if 0 <= dia < dias:
                por_dia[dia] += 1
            por_hora[momento.hour] += 1
            esperando.setdefault(usuario, t)
            escribieron.add(usuario)
        elif a_mano:
            atendidas_a_mano.add(usuario)
            esperando.pop(usuario, None)
        else:
            contestados += 1
            con_respuesta.add(usuario)
            if usuario in esperando:
                desde = esperando.pop(usuario)
                tiempos[round(t - desde)] += 1
                if not en_horario(datetime.fromtimestamp(desde, zona), c):
                    fuera += 1
    return {
        "por_dia": por_dia,
        "por_hora": por_hora,
        "respuesta": mediana(tiempos),
        "fuera": fuera,
        "a_mano": atendidas_a_mano,
        "escribieron": escribieron,
        "contestados": contestados,
        "chats_contestados": len(con_respuesta),
    }


async def calcular(c: Cuenta, rango: str) -> dict:
    zona = ZoneInfo(c.timezone)
    dias = int(rango)
    ahora = datetime.now(zona)
    inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dias - 1)
    since, until = inicio.timestamp(), ahora.timestamp()
    store = c.store
    numeros = await store.week_stats(since, until)
    conv = await conversaciones(store.message_times(since, until), c, inicio, dias)
    escribieron = conv.pop("escribieron")
    con_persona = (conv.pop("a_mano") | {u for u, *_ in await store.events("humano", since, until)}) & escribieron
    datos = {
        **numeros, **conv,
        "inicio": inicio, "dias": dias,
        "resueltas": len(escribieron - con_persona), "escribieron": len(escribieron),
    }
    if c.imagenes_mes > 0:
        comentarios = await store.week_comments(since, until)
        normales = [x for x in comentarios if x[0] != SPAM]
        datos.update(
            comentarios=len(normales),
            spam=len(comentarios) - len(normales),
            dms=sum(d for _, d, _ in normales),
            publicas=sum(r for _, _, r in normales),
            palabras=Counter(w for m, _, _ in normales for w in m.split("; ") if w).most_common(6),
            publicadas=await store.week_published(since, until),
            programadas=await store.posts_count(("programada",), 0),
        )
    return datos


async def numeros_de(c: Cuenta, rango: str) -> dict:
    clave = (c.slug, rango)
    guardado = _cache.get(clave)
    if guardado and guardado[0] > time.time():
        return guardado[1]
    datos = await calcular(c, rango)
    _cache[clave] = (time.time() + CACHE_SEGUNDOS, datos)
    return datos


# --- dibujo ---

def duracion(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    if segundos < 90:
        return f"{max(round(segundos), 1)} s"
    if segundos < 90 * 60:
        return f"{round(segundos / 60)} min"
    return f"{segundos / 3600:.1f} h".replace(".0 h", " h")


def barras(valores: list[int], etiquetas: list[str], detalles: list[str], clases: list[str] | None = None,
           descripcion: str = "") -> str:
    """Gráfica de barras: SVG estirado al ancho y, abajo, las `etiquetas` en HTML (así se leen
    igual en el celular). `detalles` sale al pasar el cursor por cada barra."""
    ancho, alto = 100 * len(valores), 100
    tope = max(valores) or 1
    partes = [f'<line class="eje" x1="0" x2="{ancho}" y1="{alto}" y2="{alto}" vector-effect="non-scaling-stroke"/>']
    for i, (v, detalle) in enumerate(zip(valores, detalles)):
        h = max(round(v / tope * alto, 1), 1)
        clase = f"b {clases[i]}" if clases else "b"
        partes.append(f'<rect class="{clase}" x="{i * 100 + 15}" y="{alto - h}" width="70" height="{h}">'
                      f"<title>{escape(detalle)}</title></rect>")
    eje = "".join(f"<span>{escape(e)}</span>" for e in etiquetas)
    return (f'<svg class="grafica" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none" role="img"'
            f' aria-label="{escape(descripcion)}">{"".join(partes)}</svg><div class="ejex">{eje}</div>')


def plural(n: int, palabra: str) -> str:
    return f"{n} {palabra}" + ("" if n == 1 else "s")


def por_dia(d: dict) -> str:
    fechas = [d["inicio"] + timedelta(days=i) for i in range(d["dias"])]
    if d["dias"] <= 7:
        etiquetas = [DIAS[f.weekday()] for f in fechas]
    else:  # una de cada cinco, contando hacia atrás desde hoy
        etiquetas = [f"{f:%d/%m}" if (d["dias"] - 1 - i) % 5 == 0 else "" for i, f in enumerate(fechas)]
    detalles = [f"{DIAS[f.weekday()]} {f:%d/%m}: {plural(v, 'mensaje')}" for f, v in zip(fechas, d["por_dia"])]
    pico = max(range(d["dias"]), key=lambda i: d["por_dia"][i])
    resumen = (f'Promedio: {sum(d["por_dia"]) / d["dias"]:.0f} al día · el día con más fue el'
               f' {DIAS[fechas[pico].weekday()]} {fechas[pico]:%d/%m} ({d["por_dia"][pico]}).')
    return (barras(d["por_dia"], etiquetas, detalles, descripcion="Mensajes recibidos por día")
            + f'<p class="ayuda" style="margin-top:.75rem">{resumen}</p>')


def por_hora(d: dict, c: Cuenta) -> str:
    inicio, fin = horario(c)
    clases = ["" if inicio <= h * 60 < fin else "fuera" for h in range(24)]
    etiquetas = [f"{h}:00" if h % 3 == 0 else "" for h in range(24)]
    detalles = [f"{h}:00 a {h}:59: {plural(v, 'mensaje')}" for h, v in enumerate(d["por_hora"])]
    pico = max(range(24), key=lambda h: d["por_hora"][h])
    return (barras(d["por_hora"], etiquetas, detalles, clases, "Mensajes recibidos por hora del día")
            + f'<p class="ayuda" style="margin-top:.75rem">La hora con más mensajes: de {pico}:00 a {pico}:59.</p>')


def kpi(etiqueta: str, valor, nota: str = "") -> str:
    return f'<div class="kpi"><span>{etiqueta}</span><b>{valor}</b><small>{nota}</small></div>'


def seccion_contenido(d: dict) -> str:
    palabras = "".join(f'<span class="chip">{escape(w)} · {n}</span> ' for w, n in d["palabras"])
    publicadas = "".join(
        f'<div class="fila"><div class="datos"><b>{escape(p["caption"][:80] or "Publicación")}</b>'
        + (f'<span><a class="enlace" href="{escape(p["permalink"])}" rel="noopener">Ver en Instagram ›</a></span>'
           if p["permalink"].startswith("https://") else "")
        + "</div></div>"
        for p in d["publicadas"]
    )
    return (
        '<div class="tarjeta"><span class="num">Comentarios</span><h2>De comentario a mensaje</h2>'
        '<p class="ayuda">Cuando alguien comenta una palabra clave, tu asistente le escribe por mensaje directo.</p>'
        '<div class="kpis">'
        + kpi("Comentarios", d["comentarios"])
        + kpi("Mensajes directos", d["dms"], "enviados por un comentario")
        + kpi("Respuestas públicas", d["publicas"])
        + (kpi("Spam ocultado", d["spam"]) if d["spam"] else "")
        + "</div>"
        + (f'<p class="ayuda" style="margin-top:1.25rem">Palabras clave más usadas</p><div>{palabras}</div>' if palabras else "")
        + "</div>"
        '<div class="tarjeta"><span class="num">Publicaciones</span>'
        f'<h2>{plural(len(d["publicadas"]), "publicada")} · {plural(d["programadas"], "programada")}</h2>'
        + (publicadas or '<p class="ayuda">No hubo publicaciones en este periodo.</p>')
        + '<div class="acciones"><a class="boton secundario" href="/panel/contenido">Ir a Contenido</a></div></div>'
    )


async def pagina(c: Cuenta, rango: str) -> HTMLResponse:
    d = await numeros_de(c, rango)
    pestanas = "".join(f'<a href="/panel/resultados?dias={k}"{panel.ACTIVA if k == rango else ""}>{nombre}</a>'
                       for k, nombre in RANGOS.items())
    if d["recibidos"]:
        pct = round(d["resueltas"] / d["escribieron"] * 100) if d["escribieron"] else 0
        numeros = (
            '<div class="hoy kpis">'
            + kpi("Conversaciones", d["personas"], plural(d["nuevas"], "nueva"))
            + kpi("Mensajes recibidos", d["recibidos"], plural(d["respuestas_bot"], "respuesta"))
            + kpi("Resueltas solas", f"{pct}%", f'{d["resueltas"]} de {d["escribieron"]} sin ayuda')
            + kpi("Tiempo de respuesta", duracion(d["respuesta"]), "lo normal (mediana)")
            + kpi("Fuera de horario", d["fuera"], plural(d["fuera"], "respuesta"))
            + "</div>")
        cuerpo = (
            '<div class="tarjeta"><span class="num">Por día</span><h2>Mensajes recibidos</h2>'
            f"{por_dia(d)}</div>"
            '<div class="tarjeta"><span class="num">Por hora</span><h2>A qué hora te escriben</h2>'
            f"{por_hora(d, c)}"
            '<div class="leyenda"><span><i></i>Dentro de tu horario</span><span><i class="fuera"></i>Fuera de tu horario</span></div></div>'
        )
    else:
        numeros = ""
        cuerpo = ('<div class="tarjeta"><h2>Aún no hay mensajes en este periodo</h2>'
                  '<p class="ayuda">Cuando tu asistente esté encendido y te escriban, aquí verás lo que hizo por ti.</p>'
                  '<div class="acciones"><a class="boton" href="/panel/asistente">Ir a tu asistente</a></div></div>')
    if c.imagenes_mes > 0:
        cuerpo += seccion_contenido(d)
    return panel.pagina(
        panel.cabecera(salir=True, menu="resultados", cuenta=c, perfil=await panel.usuario(c), foto=await panel.foto_perfil(c))
        + '<section class="oscuro portada compacta"><div class="contenedor">'
        '<p class="antetitulo">Resultados</p>'
        f'<h1>@{escape(await panel.usuario(c))}<span class="punto">.</span></h1>'
        f'<p class="bajada" style="margin-left:0">Lo que hizo tu asistente en los últimos {RANGOS[rango]}.</p>'
        f'<nav class="pestanas">{pestanas}</nav>{numeros}'
        "</div></section>"
        f'<main><div class="contenedor">{cuerpo}'
        '<p class="nota">Los números se actualizan cada 10 minutos.</p></div></main>'
    )


@router.get("")
async def ver(request: Request, dias: str = "7"):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    return await pagina(c, dias if dias in RANGOS else "7")
