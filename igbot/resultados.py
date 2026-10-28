"""Resultados: lo que hizo el asistente, en el panel del cliente.

console.skandar.online/panel/resultados, para todos los planes, con los últimos 7
o 30 días:
- Conversaciones: personas que escribieron (y cuántas nuevas), mensajes,
  cuántas resolvió el asistente sin pasar a una persona, su tiempo de
  respuesta (mediana) y cuántas veces contestó fuera del horario de atención.
- Gráficas: mensajes por día y a qué hora escriben (dentro y fuera de horario).
- Arriba, las tarjetas de Instagram (seguidores, alcance, vistas, interacciones y
  público) que guarda metricas.py una vez al día.
- Si el plan tiene contenido (Community Manager): comentarios, DMs que salieron
  de un comentario, palabras clave, spam ocultado y publicaciones.

Solo lee lo que ya está en la base de la cuenta: no llama a Instagram ni a
Claude. Los números se guardan CACHE_SEGUNDOS en memoria para que recargar la
página no repita las consultas. Las gráficas son SVG hechas aquí (el CSP del
bot no deja JavaScript; sus estilos están en panel.ESTILO); el detalle de cada barra sale al pasar el cursor.
"""
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import metricas, panel
from .comentarios import SPAM
from .cuenta import Cuenta

router = APIRouter(prefix="/panel/resultados")

RANGOS = {"7": "7 días", "30": "30 días", "90": "90 días"}
CACHE_SEGUNDOS = 600
_cache: dict[tuple, tuple[float, dict]] = {}

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


async def calcular(c: Cuenta, rango: str, fin: date | None = None) -> dict:
    """Los `rango` días que terminan en `fin` (hoy si no viene), contando ese día."""
    zona = ZoneInfo(c.timezone)
    dias = int(rango)
    ahora = datetime.now(zona)
    fin = min(fin or ahora.date(), ahora.date())
    inicio = datetime(fin.year, fin.month, fin.day, tzinfo=zona) - timedelta(days=dias - 1)
    since, until = inicio.timestamp(), min(ahora.timestamp(), (inicio + timedelta(days=dias)).timestamp())
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


async def numeros_de(c: Cuenta, rango: str, fin: date | None = None) -> dict:
    clave = (c.slug, rango, fin)
    guardado = _cache.get(clave)
    if guardado and guardado[0] > time.time():
        return guardado[1]
    datos = await calcular(c, rango, fin)
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
    else:  # una de cada cinco (de cada quince en 90 días), contando hacia atrás desde el último
        cada = 5 if d["dias"] <= 30 else 15
        etiquetas = [f"{f:%d/%m}" if (d["dias"] - 1 - i) % cada == 0 else "" for i, f in enumerate(fechas)]
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


def seccion_comentarios(d: dict) -> str:
    palabras = "".join(f'<span class="chip">{escape(w)} · {n}</span> ' for w, n in d["palabras"])
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
    )


MAX_POSTS = 24  # los más recientes del periodo con sus Insights (Instagram los guarda 1 h en informe)


async def posts_con_numeros(c: Cuenta, publicadas: list[dict]) -> list[dict]:
    """Los posts publicados del periodo con su foto y sus Insights, del de mejor interacción al peor
    (los que aún no tienen números, al final)."""
    import asyncio

    from . import contenido, informe
    posts = sorted(publicadas, key=lambda p: -(p["publish_at"] or p["created_at"]))[:MAX_POSTS]
    ids = await informe.ids_de_instagram(c, posts)
    turno = asyncio.Semaphore(4)

    async def leer(post):
        if post["id"] not in ids:
            return None
        async with turno:
            return await informe.numeros(c, ids[post["id"]])

    datos = await asyncio.gather(*(leer(p) for p in posts))
    salida = []
    for post, d in zip(posts, datos):
        media = await c.store.media(post["media_ids"][0]) if post["media_ids"] else None
        salida.append({"post": post, "datos": d, "tasa": informe.tasa(d),
                       "imagen": contenido.url_imagen(c, media[0]) if media else ""})
    salida.sort(key=lambda x: (x["tasa"] is None, -(x["tasa"] or 0)))
    return salida


def numero_post(etiqueta: str, valor) -> str:
    return f'<div><b>{"—" if valor is None else metricas.cifra(valor)}</b><span>{etiqueta}</span></div>'


def tarjeta_post(c: Cuenta, x: dict, mejor: bool = False, promedio: float | None = None) -> str:
    p, d = x["post"], x["datos"] or {}
    cuando = datetime.fromtimestamp(p["publish_at"] or p["created_at"], ZoneInfo(c.timezone))
    foto = (f'<img src="{escape(x["imagen"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            if x["imagen"] else '<div class="sin-foto">Sin imagen</div>')
    if p["permalink"].startswith("https://"):
        foto = f'<a href="{escape(p["permalink"])}" rel="noopener" title="Ver en Instagram">{foto}</a>'
    tasa = (f'<b class="tasa">{x["tasa"]:.1f}%</b><small>interacción</small>'.replace(".0%", "%")
            if x["tasa"] is not None else '<small>Instagram aún no da sus números</small>')
    tipo = "Historia" if p.get("tipo") == "historia" else "Post"
    if mejor and promedio and x["tasa"]:
        tasa += f'<small class="pp-vs">{x["tasa"] / promedio:.1f}× tu promedio</small>'.replace(".0×", "×")
    return (
        f'<article class="pp{" mejor" if mejor else ""}"><div class="pp-foto">{foto}'
        + ('<span class="pp-sello">Tu mejor post</span>' if mejor else "") + '</div>'
        f'<div class="pp-cuerpo"><p class="pp-fecha">{tipo} · {cuando:%d/%m} a las {cuando:%H:%M}</p>'
        f'<p class="pp-texto">{escape(p["caption"][:140] or "Publicación")}</p>'
        f'<div class="pp-tasa">{tasa}</div><div class="pp-nums">'
        + numero_post("alcance", d.get("reach")) + numero_post("vistas", d.get("views"))
        + numero_post("likes", d.get("likes")) + numero_post("comentarios", d.get("comments"))
        + numero_post("guardados", d.get("saved")) + numero_post("compartidos", d.get("shares"))
        + "</div></div></article>"
    )


async def seccion_publicaciones(c: Cuenta, d: dict) -> str:
    """Tus publicaciones del periodo: la mejor arriba, en grande, y las demás en tarjetas con sus números."""
    cabeza = (f'<div class="met-cab"><span>Tus publicaciones</span><b>{plural(len(d["publicadas"]), "publicada")}'
              f' <small>· {plural(d["programadas"], "programada")}</small></b></div>')
    if not d["publicadas"]:
        return (f'<section class="met-c ancho res-posts">{cabeza}<p class="ayuda">No hubo publicaciones en este periodo.</p>'
                '<div class="acciones"><a class="boton secundario" href="/panel/contenido">Ir a Calendario</a></div></section>')
    posts = await posts_con_numeros(c, d["publicadas"])
    con = [x for x in posts if x["tasa"] is not None]
    promedio = sum(x["tasa"] for x in con) / len(con) if con else None
    nota = (f'<p class="ayuda chica">Interacción promedio: {promedio:.1f}% (likes, comentarios, guardados y'
            ' compartidos entre el alcance). Ordenados de mejor a peor.</p>' if con else "")
    primero, resto = (posts[0], posts[1:]) if con else (None, posts)
    return (f'<section class="met-c ancho res-posts">{cabeza}{nota}'
            + (tarjeta_post(c, primero, mejor=True, promedio=promedio) if primero else "")
            + (f'<div class="pp-grid">{"".join(tarjeta_post(c, x) for x in resto)}</div>' if resto else "")
            + "</section>")


def titulo_periodo(ini: date, fin: date) -> str:
    from .calendario import MESES
    if ini.year != fin.year:
        return f"{ini.day} de {MESES[ini.month - 1]} de {ini.year} al {fin.day} de {MESES[fin.month - 1]} de {fin.year}"
    if ini.month == fin.month:
        return f"{ini.day} al {fin.day} de {MESES[fin.month - 1]} de {fin.year}"
    return f"{ini.day} de {MESES[ini.month - 1]} al {fin.day} de {MESES[fin.month - 1]} de {fin.year}"


VISTAS = {"instagram": "Instagram", "conversaciones": "Conversaciones"}


def enlace(rango: str, fin: date, hoy: date, vista: str = "instagram", base: str = "/panel/resultados") -> str:
    return (f"{base}?dias={rango}" + (f"&fin={fin.isoformat()}" if fin < hoy else "")
            + (f"&ver={vista}" if vista != "instagram" else ""))


async def barra(c: Cuenta, rango: str, fin: date, hoy: date, vista: str) -> str:
    """La barra de arriba, como la de Calendario: el periodo con ‹ › y Hoy, Instagram o Conversaciones,
    7/30/90 días, cuándo se actualizaron los números y la descarga en PDF de lo que se ve."""
    dias = int(rango)
    ini = fin - timedelta(days=dias - 1)
    antes = enlace(rango, fin - timedelta(days=dias), hoy, vista)
    siguiente = (f'<a href="{enlace(rango, min(fin + timedelta(days=dias), hoy), hoy, vista)}" aria-label="Siguiente">›</a>'
                 if fin < hoy else '<span class="off" aria-hidden="true">›</span>')
    vistas = "".join(f'<a href="{enlace(rango, fin, hoy, k)}"{panel.ACTIVA if k == vista else ""}>{nombre}</a>'
                     for k, nombre in VISTAS.items())
    rangos = "".join(f'<a href="{enlace(k, fin, hoy, vista)}"{panel.ACTIVA if k == rango else ""}>{nombre}</a>'
                     for k, nombre in RANGOS.items())
    guardado = await c.store.get(metricas.CLAVE_HORA)
    if guardado:
        cuando = datetime.fromtimestamp(float(guardado), ZoneInfo(c.timezone))
        dia = "hoy" if cuando.date() == hoy else "ayer" if cuando.date() == hoy - timedelta(days=1) else f"el {cuando:%d/%m}"
        instagram = f"Instagram: actualizado {dia} a las {cuando:%H:%M}"
    else:
        instagram = "Instagram: el primer guardado es mañana a las 6:00"
    return (
        '<div class="tarjeta cal res-barra"><div class="cal-cab">'
        f'<h2>{titulo_periodo(ini, fin)}</h2><div class="cal-nav">'
        f'<a href="{antes}" aria-label="Anterior">‹</a>{siguiente}'
        f'<a class="hoy-b" href="{enlace(rango, hoy, hoy, vista)}">Hoy</a></div>'
        f'<div class="cal-sw modo">{rangos}</div></div>'
        f'<div class="cal-filtros res-info"><div class="cal-sw res-vista">{vistas}</div>'
        + (f'<span><i class="punto"></i>{instagram}</span>'
           '<span class="res-comp">Seguidores vs. 7 y 30 días antes</span>'
           if vista == "instagram" else
           '<span><i class="punto"></i>Mensajes: se actualizan cada 10 minutos</span>')
        + f'<a class="boton secundario chico cal-bajar" href="{enlace(rango, fin, hoy, vista, "/panel/resultados/descargar")}"'
        ' title="Este periodo, en PDF">↓ Descargar</a></div></div>'
    )


def conversaciones_html(c: Cuenta, d: dict, publico: dict) -> str:
    if d["recibidos"]:
        pct = round(d["resueltas"] / d["escribieron"] * 100) if d["escribieron"] else 0
        cuerpo = (
            '<div class="hoy kpis res-kpis">'
            + kpi("Conversaciones", d["personas"], plural(d["nuevas"], "nueva"))
            + kpi("Mensajes recibidos", d["recibidos"], plural(d["respuestas_bot"], "respuesta"))
            + kpi("Resueltas solas", f"{pct}%", f'{d["resueltas"]} de {d["escribieron"]} sin ayuda')
            + kpi("Tiempo de respuesta", duracion(d["respuesta"]), "lo normal (mediana)")
            + kpi("Fuera de horario", d["fuera"], plural(d["fuera"], "respuesta"))
            + "</div>"
            '<div class="tarjeta"><span class="num">Por día</span><h2>Mensajes recibidos</h2>'
            f"{por_dia(d)}</div>"
            '<div class="res-dos"><div class="tarjeta"><span class="num">Por hora</span><h2>A qué hora te escriben</h2>'
            f"{por_hora(d, c)}"
            '<div class="leyenda"><span><i></i>Dentro de tu horario</span><span><i class="fuera"></i>Fuera de tu horario</span></div></div>'
            + metricas.ciudades(publico, "De dónde es tu público") + "</div>"
        )
    else:
        cuerpo = ('<div class="tarjeta"><h2>Aún no hay mensajes en este periodo</h2>'
                  '<p class="ayuda">Cuando tu asistente esté encendido y te escriban, aquí verás lo que hizo por ti.</p>'
                  '<div class="acciones"><a class="boton" href="/panel/asistente">Ir a tu asistente</a></div></div>')
    if c.imagenes_mes > 0:
        cuerpo += seccion_comentarios(d)
    return cuerpo


async def pagina(c: Cuenta, rango: str, fin: date | None = None, vista: str = "instagram") -> HTMLResponse:
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    fin = min(fin or hoy, hoy)
    if vista == "conversaciones":
        publico = json.loads(await c.store.get(metricas.CLAVE_PUBLICO) or "{}")
        cuerpo = conversaciones_html(c, await numeros_de(c, rango, fin), publico)
    else:
        cuerpo = metricas.seccion(await metricas.numeros(c, int(rango), fin))
        if c.imagenes_mes > 0:
            cuerpo += f'<div class="met">{await seccion_publicaciones(c, await numeros_de(c, rango, fin))}</div>'
    return panel.pagina(
        await panel.menu_de(c, "resultados")
        + f'<main><div class="contenedor res">{await barra(c, rango, fin, hoy, vista)}{cuerpo}</div></main>'
    )


def leer_fin(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


@router.get("")
async def ver(request: Request, dias: str = "7", fin: str = "", ver: str = "instagram"):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    return await pagina(c, dias if dias in RANGOS else "7", leer_fin(fin), ver if ver in VISTAS else "instagram")


@router.get("/descargar")
async def descargar(request: Request, dias: str = "7", fin: str = "", ver: str = "instagram"):
    """Lo que se ve en Resultados (Instagram o Conversaciones, en su periodo), en PDF."""
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    from . import informe
    rango = dias if dias in RANGOS else "7"
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    fin_ = min(leer_fin(fin) or hoy, hoy)
    ini = fin_ - timedelta(days=int(rango) - 1)
    d = await numeros_de(c, rango, fin_)
    m = await metricas.numeros(c, int(rango), fin_)
    pdf = await informe.resultados_pdf(c, titulo_periodo(ini, fin_), d, m, await panel.usuario(c),
                                       ver if ver in VISTAS else "instagram")
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="resultados-{ver if ver in VISTAS else 'instagram'}-{ini.isoformat()}-al-{fin_.isoformat()}.pdf"'})
