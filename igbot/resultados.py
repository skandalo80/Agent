"""Resultados: lo que hizo el asistente, en el panel del cliente.

console.skandar.online/panel/resultados, para todos los planes, con los últimos 7
o 30 días:
- Conversaciones: personas que escribieron (y cuántas nuevas), mensajes,
  cuántas resolvió el asistente sin pasar a una persona, su tiempo de
  respuesta (mediana) y cuántas veces contestó fuera del horario de atención.
- Gráficas: mensajes por día y a qué hora escriben (dentro y fuera de horario).
- Arriba, las tarjetas de Instagram (seguidores, alcance, vistas, interacciones y
  público) que guarda metricas.py una vez al día.
- Si el plan tiene contenido (Community Manager): tus publicaciones (en Instagram) y la
  pestaña Comentarios: cuántos llegaron, cuántos contestó el asistente (en público o por DM),
  spam ocultado, menciones, de qué tipo son (los de Configura tus interacciones), por día,
  los posts con más comentarios y las palabras clave más usadas. Para la foto y el texto de
  esos posts sí se llama a Instagram (guardado POST_INFO_SEGUNDOS).

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

from . import anuncios_meta, config, metricas, panel
from .comentarios import SPAM
from .cuenta import Cuenta

router = APIRouter(prefix="/panel/resultados")

RANGOS = {"7": "7 días", "30": "30 días", "90": "90 días"}
MAX_DIAS = 366  # el rango personalizado (?desde=AAAA-MM-DD&fin=AAAA-MM-DD) llega hasta un año
VISTAS_PDF = ("instagram", "conversaciones")
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
        "embudo": await store.embudo(since, until),
        "temas": temas_de(json.loads(await store.get(metricas.CLAVE_TEMAS) or "{}"), dias),
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
    igual en el celular). Al pasar el cursor sale el número en una etiqueta sobre la barra;
    `detalles` ya no se muestra."""
    ancho, alto = 100 * len(valores), 100
    tope = max(valores) or 1
    partes = [f'<line class="eje" x1="0" x2="{ancho}" y1="{alto}" y2="{alto}" vector-effect="non-scaling-stroke"/>']
    columnas = []
    for i, (v, detalle) in enumerate(zip(valores, detalles)):
        h = max(round(v / tope * alto, 1), 1)
        clase = f"b {clases[i]}" if clases else "b"
        partes.append(f'<rect class="{clase}" x="{i * 100 + 15}" y="{alto - h}" width="70" height="{h}"/>')
        columnas.append((alto - h, f"<b>{v:,}</b>", ""))  # al pasar el cursor, solo el número
    eje = "".join(f"<span>{escape(e)}</span>" for e in etiquetas)
    return (f'<div class="met-graf barras"><svg class="grafica" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none"'
            f' role="img" aria-label="{escape(descripcion)}">{"".join(partes)}</svg>'
            f'{metricas.puntos_hover(columnas)}</div><div class="ejex">{eje}</div>')


def plural(n: int, palabra: str) -> str:
    return f"{n} {palabra}" + ("" if n == 1 else "s")


def por_dia(d: dict, cada: str = "dia") -> str:
    """Mensajes recibidos por día, o sumados por semana o por mes (bloques completos desde el final)."""
    fechas = [d["inicio"].date() + timedelta(days=i) for i in range(d["dias"])]
    bloque = metricas.CADA[cada][1]
    if bloque > 1:
        n = d["dias"] // bloque
        sobran = d["dias"] - n * bloque
        valores = [sum(d["por_dia"][sobran + i * bloque: sobran + (i + 1) * bloque]) for i in range(n)]
        fechas = [fechas[sobran + i * bloque] for i in range(n)]
        paso = 1 if n <= 8 else 2 if n <= 16 else 3
        etiquetas = [f"{f:%d/%m}" if (n - 1 - i) % paso == 0 else "" for i, f in enumerate(fechas)]
        nombre = "semana" if cada == "semana" else "mes"
        detalles = [f"{nombre} del {f:%d/%m}: {plural(v, 'mensaje')}" for f, v in zip(fechas, valores)]
        pico = max(range(n), key=lambda i: valores[i])
        resumen = (f'Promedio: {sum(valores) / n:.0f} por {nombre} · {"la" if cada == "semana" else "el"} {nombre}'
                   f' con más fue {"la" if cada == "semana" else "el"} del {fechas[pico]:%d/%m} ({valores[pico]}).')
        return (barras(valores, etiquetas, detalles, descripcion=f"Mensajes recibidos por {nombre}")
                + f'<p class="ayuda" style="margin-top:.75rem">{resumen}</p>')
    if d["dias"] <= 7:
        etiquetas = [DIAS[f.weekday()] for f in fechas]
    else:  # una de cada cinco (de cada quince en 90 días), contando hacia atrás desde el último
        paso = 5 if d["dias"] <= 30 else 15
        etiquetas = [f"{f:%d/%m}" if (d["dias"] - 1 - i) % paso == 0 else "" for i, f in enumerate(fechas)]
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


MAX_POSTS = 24  # los más recientes del periodo con sus Insights (Instagram los guarda 1 h en informe)


async def posts_con_numeros(c: Cuenta, publicadas: list[dict], inicio: datetime | None = None,
                            dias: int = 0) -> list[dict]:
    """Lo publicado en el periodo con su foto y sus Insights, de mejor interacción a peor (sin números, al final):
    los posts del panel y, con `inicio` y `dias`, también lo subido a mano a Instagram."""
    import asyncio

    from . import contenido, informe
    todos = list(publicadas)
    if inicio is not None and dias:
        todos = await informe.publicados_entre(c, inicio.timestamp(), (inicio + timedelta(days=dias)).timestamp(),
                                              publicadas)
    posts = sorted(todos, key=lambda p: -(p["publish_at"] or p["created_at"]))[:MAX_POSTS]
    ids = await informe.ids_de_instagram(c, [p for p in posts if p.get("id") is not None])
    for p in posts:
        if p.get("ig_id") and p.get("id") is not None:
            ids.setdefault(p["id"], p["ig_id"])
    turno = asyncio.Semaphore(4)

    async def leer(post):
        media_id = post["ig_id"] if post.get("externo") else ids.get(post["id"])
        if not media_id:
            return None
        async with turno:
            return await informe.numeros(c, media_id, post["publish_at"], post.get("tipo") or "post")

    datos = await asyncio.gather(*(leer(p) for p in posts))
    salida = []
    for post, d in zip(posts, datos):
        if post.get("externo"):
            imagen = post.get("imagen", "")
        else:
            media = await c.store.media(post["media_ids"][0]) if post["media_ids"] else None
            imagen = contenido.url_imagen(c, media[0]) if media else ""
        salida.append({"post": post, "datos": d, "tasa": informe.tasa(d), "pocas": informe.pocas(d), "imagen": imagen})
    # De mejor a peor; los que llegaron a muy pocas cuentas (su tasa dice poco) después, y sin números al final.
    salida.sort(key=lambda x: (x["tasa"] is None, x["pocas"], -(x["tasa"] or 0)))
    return salida


TIPOS_POST = {"post": "Post", "reel": "Reel", "historia": "Historia"}
PLURAL_TIPO = {"post": "posts", "reel": "reels", "historia": "historias"}


def por_formato_html(posts: list[dict]) -> str:
    """Posts, reels e historias del periodo comparados: cuántos, vistas y alcance promedio y su interacción.
    Solo cuando hay más de un formato con números."""
    from . import informe
    grupos: dict[str, list[dict]] = {}
    for x in posts:
        if x["datos"]:
            grupos.setdefault(x["post"].get("tipo") or "post", []).append(x)
    if len(grupos) < 2:
        return ""
    filas = []
    for tipo in ("post", "reel", "historia"):
        if not (xs := grupos.get(tipo)):
            continue
        n = len(xs)
        vistas = sum(x["datos"].get("views", 0) for x in xs) / n
        alcance = sum(x["datos"].get("reach", 0) for x in xs) / n
        inter = sum(informe.interacciones(x["datos"]) for x in xs) / n
        conjunta = informe.tasa_conjunta([x["datos"] for x in xs])
        tasa = f"{conjunta:.1f}%".replace(".0%", "%") if conjunta is not None else "—"
        filas.append(f"<tr><td>{TIPOS_POST[tipo]}<small> · {n}</small></td><td>{metricas.cifra(vistas)}</td>"
                     f"<td>{metricas.cifra(alcance)}</td><td>{metricas.cifra(inter)}</td><td><b>{tasa}</b></td></tr>")
    return ('<div class="res-formatos"><span class="num">Por formato</span>'
            '<table><tr><th>Formato</th><th>Vistas</th><th>Alcance</th><th>Interacciones</th><th>Interacción</th></tr>'
            + "".join(filas) + "</table><p class=\"ayuda chica\">Promedios por publicación del periodo; la interacción es "
            "la suma de interacciones entre la suma de alcance. En historias, las interacciones son respuestas y compartidos.</p></div>")


def numero_post(etiqueta: str, valor) -> str:
    return f'<div><b>{"—" if valor is None else metricas.cifra(valor)}</b><span>{etiqueta}</span></div>'


def tarjeta_post(c: Cuenta, x: dict, mejor: bool = False, promedio: float | None = None) -> str:
    from . import informe
    p, d = x["post"], x["datos"] or {}
    cuando = datetime.fromtimestamp(p["publish_at"] or p["created_at"], ZoneInfo(c.timezone))
    foto = (f'<img src="{escape(x["imagen"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            if x["imagen"] else '<div class="sin-foto">Sin imagen</div>')
    if p["permalink"].startswith("https://"):
        foto = f'<a href="{escape(p["permalink"])}" rel="noopener" title="Ver en Instagram">{foto}</a>'
    tasa = (f'<b class="tasa">{x["tasa"]:.1f}%</b><small>interacción</small>'.replace(".0%", "%")
            if x["tasa"] is not None else '<small>Instagram aún no da sus números</small>')
    tipo = TIPOS_POST.get(p.get("tipo") or "post", "Post") + (" · desde Instagram" if p.get("externo") else "")
    if mejor and promedio and x["tasa"]:
        tasa += f'<small class="pp-vs">{x["tasa"] / promedio:.1f}× tu promedio</small>'.replace(".0×", "×")
    elif x.get("pocas"):
        tasa += f'<small class="pp-vs">llegó a menos de {informe.MIN_ALCANCE} cuentas</small>'
    if p.get("tipo") == "historia":
        nums = (numero_post("alcance", d.get("reach")) + numero_post("vistas", d.get("views"))
                + numero_post("respuestas", d.get("replies")) + numero_post("compartidos", d.get("shares")))
    else:
        nums = (numero_post("alcance", d.get("reach")) + numero_post("vistas", d.get("views"))
                + numero_post("likes", d.get("likes")) + numero_post("comentarios", d.get("comments"))
                + numero_post("guardados", d.get("saved")) + numero_post("compartidos", d.get("shares")))
    if d.get("profile_visits") is not None or d.get("follows") is not None:
        nums += numero_post("visitas al perfil", d.get("profile_visits")) + numero_post("seguidores", d.get("follows"))
    return (
        f'<article class="pp{" mejor" if mejor else ""}"><div class="pp-foto">{foto}'
        + ('<span class="pp-sello">Tu mejor post</span>' if mejor else "") + '</div>'
        f'<div class="pp-cuerpo"><p class="pp-fecha">{tipo} · {cuando:%d/%m} a las {cuando:%H:%M}</p>'
        f'<p class="pp-texto">{escape(p["caption"][:140] or "Publicación")}</p>'
        f'<div class="pp-tasa">{tasa}</div><div class="pp-nums">{nums}</div></div></article>'
    )


async def seccion_publicaciones(c: Cuenta, d: dict) -> str:
    """Tus publicaciones del periodo (del panel y subidas a mano a Instagram): la mejor arriba, en grande, las
    demás en tarjetas con sus números, y la comparación por formato."""
    posts = await posts_con_numeros(c, d["publicadas"], d["inicio"], d["dias"])
    externos = sum(1 for x in posts if x["post"].get("externo"))
    cabeza = (f'<div class="met-cab"><span>Tus publicaciones</span><b>{plural(len(posts), "publicada")}'
              f' <small>· {plural(d["programadas"], "programada")}'
              + (f" · {externos} desde Instagram" if externos else "") + '</small></b></div>')
    if not posts:
        return (f'<section class="met-c ancho res-posts">{cabeza}<p class="ayuda">No hubo publicaciones en este periodo.</p>'
                '<div class="acciones"><a class="boton secundario" href="/panel/contenido">Ir a Calendario</a></div></section>')
    from . import informe
    con = [x for x in posts if x["tasa"] is not None]
    # El promedio es el del conjunto (suma de interacciones entre suma de alcance) de posts y reels; las historias
    # se miden aparte, en Por formato.
    promedio = informe.tasa_conjunta([x["datos"] for x in con if x["post"].get("tipo") != "historia"])
    nota = (f'<p class="ayuda chica">Interacción promedio: {promedio:.1f}% (likes, comentarios, guardados y'
            ' compartidos entre el alcance de tus posts y reels). Ordenados de mejor a peor.</p>'.replace(".0%", "%")
            if promedio is not None else "")
    primero, resto = (posts[0], posts[1:]) if con and not posts[0]["pocas"] else (None, posts)
    return (f'<section class="met-c ancho res-posts">{cabeza}{nota}{por_formato_html(posts)}'
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


VISTAS = {"instagram": "Instagram", "conversaciones": "Conversaciones", "comentarios": "Comentarios",
          "anuncios": "Anuncios"}


def vistas_de(c: Cuenta) -> dict[str, str]:
    """Comentarios, solo con contenido en el plan (Community Manager): Básico no los atiende."""
    return {k: v for k, v in VISTAS.items() if k != "comentarios" or c.imagenes_mes > 0}


def enlace(rango: str, fin: date, hoy: date, vista: str = "instagram", base: str = "/panel/resultados",
           formato: str = "", cada: str = "", **extra) -> str:
    """`cada` (día, semana o mes) solo va en el enlace cuando no es la que tocaría por defecto para ese rango."""
    if cada and cada not in metricas.opciones_cada(int(rango)):  # al cambiar de rango, si ya no cabe, vuelve a la de defecto
        cada = ""
    return (f"{base}?dias={rango}" + (f"&fin={fin.isoformat()}" if fin < hoy else "")
            + (f"&ver={vista}" if vista != "instagram" else "") + (f"&formato={formato}" if formato else "")
            + (f"&cada={cada}" if cada and cada != metricas.cada_por_defecto(int(rango)) else "")
            + "".join(f"&{k}={v}" for k, v in extra.items() if v))


def personalizado(rango: str, fin: date, hoy: date, vista: str, cada: str = "") -> str:
    """La pastilla "Personalizado" a la izquierda de 7 días: un formulario plegado (sin JS) con desde y hasta.
    Con un rango que no es 7, 30 ni 90 queda marcada y dice las fechas."""
    dias = int(rango)
    ini = fin - timedelta(days=dias - 1)
    activo = rango not in RANGOS
    nombre = f"{ini:%d/%m} – {fin:%d/%m}" if activo else "Personalizado"
    return (
        f'<details class="res-pers{" on" if activo else ""}"><summary>{nombre}</summary>'
        '<form method="get" action="/panel/resultados" class="res-pers-f">'
        f'<label for="res-desde">Desde</label><input id="res-desde" type="date" name="desde" value="{ini.isoformat()}" max="{hoy.isoformat()}" required>'
        f'<label for="res-fin">Hasta</label><input id="res-fin" type="date" name="fin" value="{fin.isoformat()}" max="{hoy.isoformat()}" required>'
        + (f'<input type="hidden" name="ver" value="{vista}">' if vista != "instagram" else "")
        + (f'<input type="hidden" name="cada" value="{cada}">' if cada else "")
        + '<button class="boton chico" type="submit">Ver</button></form></details>'
    )


def descargas(rango: str, fin: date, hoy: date, vista: str, cada: str = "") -> str:
    """El menú de Descargar (plegado, sin JS): PDF y CSV de la cuenta y, en Instagram, por post. El PDF sigue
    la granularidad que se ve; el CSV siempre va día por día."""
    base = "/panel/resultados/descargar"
    opciones = [("PDF del periodo", enlace(rango, fin, hoy, vista, base, cada=cada)),
                ("CSV día por día", enlace(rango, fin, hoy, vista, base, archivo="csv"))]
    if vista == "instagram":
        opciones += [("PDF por publicación", enlace(rango, fin, hoy, vista, base, que="posts")),
                     ("CSV por publicación", enlace(rango, fin, hoy, vista, base, que="posts", archivo="csv"))]
    return ('<details class="res-bajar"><summary class="boton secundario chico cal-bajar">↓ Descargar</summary><div>'
            + "".join(f'<a href="{href}">{nombre}</a>' for nombre, href in opciones) + "</div></details>")


def nota_comparacion(dias: int, cada: str) -> str:
    """Con qué se compara el periodo: los mismos días anteriores, o semanas o meses enteros atrás para que
    coincidan los días de la semana."""
    if cada == "dia":
        return f"Comparado con los {dias} días anteriores"
    n = metricas.salto(dias, cada) // metricas.CADA[cada][1]
    if cada == "semana":
        return f"Comparado con {plural(n, 'semana')} atrás, mismos días de la semana"
    return f"Comparado con {n} {'mes' if n == 1 else 'meses'} atrás"


async def barra(c: Cuenta, rango: str, fin: date, hoy: date, vista: str, cada: str = "dia") -> str:
    """La barra de arriba, como la de Calendario: el periodo con ‹ › y Hoy, Instagram o Conversaciones,
    7/30/90 días, por día / semana / mes (una vez, para todas las gráficas), cuándo se actualizaron los
    números y la descarga en PDF de lo que se ve."""
    dias = int(rango)
    ini = fin - timedelta(days=dias - 1)
    antes = enlace(rango, fin - timedelta(days=dias), hoy, vista, cada=cada)
    siguiente = (f'<a href="{enlace(rango, min(fin + timedelta(days=dias), hoy), hoy, vista, cada=cada)}" aria-label="Siguiente">›</a>'
                 if fin < hoy else '<span class="off" aria-hidden="true">›</span>')
    vistas = "".join(f'<a href="{enlace(rango, fin, hoy, k, cada=cada)}"{panel.ACTIVA if k == vista else ""}>{nombre}</a>'
                     for k, nombre in vistas_de(c).items())
    rangos = personalizado(rango, fin, hoy, vista, cada) + "".join(
        f'<a href="{enlace(k, fin, hoy, vista, cada=cada)}"{panel.ACTIVA if k == rango else ""}>{nombre}</a>'
        for k, nombre in RANGOS.items())
    opciones = metricas.opciones_cada(dias)
    granularidad = ("" if len(opciones) < 2 or vista not in VISTAS_PDF else
                    '<div class="cal-sw modo res-cada">' + "".join(
                        f'<a href="{enlace(rango, fin, hoy, vista, cada=k)}"{panel.ACTIVA if k == cada else ""}>{metricas.CADA[k][0]}</a>'
                        for k in opciones) + "</div>")
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
        f'<a class="hoy-b" href="{enlace(rango, hoy, hoy, vista, cada=cada)}">Hoy</a></div>'
        f'<div class="cal-sw modo">{rangos}</div>{granularidad}</div>'
        f'<div class="cal-filtros res-info"><div class="cal-sw res-vista">{vistas}</div>'
        + (f'<span><i class="punto"></i>{instagram}</span>'
           f'<span class="res-comp">{nota_comparacion(dias, cada)}</span>'
           if vista == "instagram" else
           '<span><i class="punto"></i>Anuncios: números de Meta y de tus chats, cada 10 minutos</span>'
           if vista == "anuncios" else
           '<span><i class="punto"></i>Comentarios: se actualizan cada 10 minutos</span>'
           if vista == "comentarios" else
           '<span><i class="punto"></i>Mensajes: se actualizan cada 10 minutos</span>')
        + ("" if vista not in VISTAS_PDF else descargas(rango, fin, hoy, vista, cada)) + "</div></div>"
    )


def temas_de(guardados: dict, dias: int) -> dict:
    """Los temas que escribió Claude para el periodo más parecido (7 días, o 30 para lo demás)."""
    clave = "7" if dias <= 7 else "30"
    return {**(guardados.get(clave) or {}), "dias": int(clave)}


def embudo_html(d: dict) -> str:
    """Hasta dónde llegaron las conversaciones: te escribieron → pidieron cotización → agendaron cita →
    compraron, con el porcentaje de cada paso respecto a quienes escribieron. Solo los pasos con datos."""
    e = d["embudo"]
    pasos = [("Te escribieron", d["escribieron"], "")]
    if e["cotizaciones"]:
        pasos.append(("Pidieron cotización", e["cotizaciones"], ""))
    if e["citas"]:
        pasos.append(("Agendaron cita", e["citas"], ""))
    if e["ventas"]:
        pasos.append(("Compraron", e["compradores"] or e["ventas"],
                      f'{plural(e["ventas"], "venta")} · {dinero(e["monto"], e["moneda"])}'))
    tope = max(n for _, n, _ in pasos) or 1
    filas = []
    for i, (nombre, n, nota) in enumerate(pasos):
        pct = f"{n / d['escribieron']:.0%}" if i and d["escribieron"] else ""
        filas.append(f'<li><span class="rt-nombre">{nombre}</span>'
                     f'<span class="rt-barra"><i style="width:{max(n / tope * 100, 2):.0f}%"></i></span>'
                     f'<b>{n:,}</b>' + (f"<small>{escape(nota)}{' · ' if nota and pct else ''}{pct}{' de quienes escribieron' if pct else ''}</small>" if nota or pct else "") + "</li>")
    ayuda = ("" if len(pasos) > 1 else
             '<p class="ayuda chica">Cuando tu asistente anote cotizaciones, agende citas o cobre, aquí verás hasta dónde llegó cada conversación.</p>')
    return f'<ul class="res-tipos res-embudo">{"".join(filas)}</ul>{ayuda}'


def temas_html(d: dict) -> str:
    t = d.get("temas") or {}
    if not t.get("texto"):
        return ""
    cuando = ""
    if t.get("hasta"):
        try:
            cuando = f' · al {date.fromisoformat(t["hasta"]):%d/%m}'
        except ValueError:
            pass
    parrafos = "".join(f"<p>{escape(x.strip())}</p>" for x in t["texto"].split("\n") if x.strip())
    return (f'<div class="tarjeta res-temas"><span class="num">Últimos {t["dias"]} días{cuando}</span>'
            f'<h2>Lo que más preguntan</h2>{parrafos}'
            '<p class="ayuda chica">Lo escribe tu asistente una vez al día a partir de los mensajes de tus clientes.</p></div>')


def conversaciones_html(c: Cuenta, d: dict, cada: str = "dia") -> str:
    if d["recibidos"]:
        pct = round(d["resueltas"] / d["escribieron"] * 100) if d["escribieron"] else 0
        e = d["embudo"]
        dejaron = "".join(x for x in (
            kpi("Pidieron cotización", e["cotizaciones"], "cotización, visita o sus datos") if e["cotizaciones"] else "",
            kpi("Agendaron cita", e["citas"], "con tu asistente") if e["citas"] else "",
            kpi("Ventas por chat", e["ventas"], dinero(e["monto"], e["moneda"])) if e["ventas"] else "",
            kpi("Pasaron a tu equipo", e["equipo"], "1 conversación" if e["equipo"] == 1 else f'{e["equipo"]} conversaciones') if e["equipo"] else "",
        ))
        cuerpo = (
            '<div class="hoy kpis res-kpis">'
            + kpi("Conversaciones", d["personas"], plural(d["nuevas"], "nueva"))
            + kpi("Mensajes recibidos", d["recibidos"], plural(d["respuestas_bot"], "respuesta"))
            + kpi("Resueltas solas", f"{pct}%", f'{d["resueltas"]} de {d["escribieron"]} sin ayuda')
            + kpi("Tiempo de respuesta", duracion(d["respuesta"]), "lo normal (mediana)")
            + kpi("Fuera de horario", d["fuera"], plural(d["fuera"], "respuesta"))
            + "</div>"
            + (f'<div class="hoy kpis res-kpis">{dejaron}</div>' if dejaron else "")
            + '<div class="res-dos"><div class="tarjeta"><span class="num">Por día</span><h2>Mensajes recibidos</h2>'
            f"{por_dia(d, cada)}</div>"
            f'<div class="tarjeta"><span class="num">Hasta dónde llegaron</span><h2>Lo que dejaron</h2>{embudo_html(d)}</div></div>'
            + temas_html(d)
            + '<div class="tarjeta"><span class="num">Por hora</span><h2>A qué hora te escriben</h2>'
            f"{por_hora(d, c)}"
            '<div class="leyenda"><span><i></i>Dentro de tu horario</span><span><i class="fuera"></i>Fuera de tu horario</span></div></div>'
        )
    else:
        cuerpo = ('<div class="tarjeta"><h2>Aún no hay mensajes en este periodo</h2>'
                  '<p class="ayuda">Cuando tu asistente esté encendido y te escriban, aquí verás lo que hizo por ti.</p>'
                  '<div class="acciones"><a class="boton" href="/panel/asistente">Ir a tu asistente</a></div></div>')
    return cuerpo


# --- Comentarios ---

# Los tipos de Configura tus interacciones (interacciones.CASOS), con nombre corto para la gráfica.
TIPOS_COMENTARIO = {
    "elogio": "Elogio", "pregunta": "Pregunta", "compra": "Quiere comprar", "queja": "Queja o crítica",
    "etiqueta": "Etiqueta a un amigo", "emojis": "Solo emojis", "otro": "Otro",
}
MENCION = "mencion"  # interacciones.mencion las guarda en comments con este user_id
MAX_POSTS_COMENTARIOS = 5
POST_INFO_SEGUNDOS = 6 * config.HORA  # la foto de Instagram (su URL vence) y el texto de cada post, guardados un rato


def tipo_de(matched: str) -> str:
    """El tipo de un comentario según lo que anotó comentarios.handle o la tabla de interacciones."""
    if matched.startswith("tipo: "):
        tipo = matched[6:].strip()
        return tipo if tipo in TIPOS_COMENTARIO else "otro"
    return "clave" if matched else "sin"


async def numeros_comentarios(c: Cuenta, rango: str, fin: date) -> dict:
    zona = ZoneInfo(c.timezone)
    dias = int(rango)
    inicio = datetime(fin.year, fin.month, fin.day, tzinfo=zona) - timedelta(days=dias - 1)
    filas = await c.store.comments_between(inicio.timestamp(), (inicio + timedelta(days=dias)).timestamp())
    menciones = [f for f in filas if f[0] == MENCION]
    todos = [f for f in filas if f[0] != MENCION]
    normales = [f for f in todos if f[2] != SPAM]
    por_dia = [0] * dias
    tipos: dict[str, list[int]] = {}
    for _, _, matched, dm, publica, t in normales:
        dia = (datetime.fromtimestamp(t, zona).date() - inicio.date()).days
        if 0 <= dia < dias:
            por_dia[dia] += 1
        x = tipos.setdefault(tipo_de(matched), [0, 0])
        x[0] += 1
        x[1] += 1 if (dm or publica) else 0
    por_post = Counter(f[1] for f in normales if f[1])
    return {
        "inicio": inicio, "dias": dias, "por_dia": por_dia, "tipos": tipos,
        "comentarios": len(normales),
        "contestados": sum(1 for f in normales if f[3] or f[4]),
        "publicas": sum(f[4] for f in normales),
        "dms": sum(f[3] for f in normales),
        "spam": len(todos) - len(normales),
        "menciones": len(menciones),
        "palabras": Counter(w for f in normales if tipo_de(f[2]) == "clave"
                            for w in f[2].split("; ") if w).most_common(6),
        "posts": por_post.most_common(MAX_POSTS_COMENTARIOS),
    }


async def info_post(c: Cuenta, media_id: str) -> dict:
    """Foto, texto y enlace de un post de Instagram (los suba quien los suba), guardados POST_INFO_SEGUNDOS."""
    clave = f"post_info:{media_id}"
    if guardado := await c.store.get(clave):
        datos = json.loads(guardado)
        if datos.get("t", 0) > time.time() - POST_INFO_SEGUNDOS:
            return datos
    try:
        x = await c.instagram.media_basico(media_id)
    except Exception:
        return {}
    datos = {"t": time.time(), "permalink": x.get("permalink", ""), "caption": x.get("caption", ""),
             "imagen": x.get("thumbnail_url") or x.get("media_url", ""),
             "fecha": x.get("timestamp", ""), "tipo": x.get("media_product_type") or x.get("media_type", "")}
    await c.store.set(clave, json.dumps(datos, ensure_ascii=False))
    return datos


def tipos_html(d: dict) -> str:
    orden = sorted(d["tipos"].items(), key=lambda x: -x[1][0])
    tope = max((n for n, _ in d["tipos"].values()), default=0) or 1
    nombres = {**TIPOS_COMENTARIO, "clave": "Palabra clave", "sin": "Sin clasificar"}
    filas = "".join(
        f'<li><span class="rt-nombre">{nombres[k]}</span>'
        f'<span class="rt-barra"><i style="width:{max(n / tope * 100, 2):.0f}%"></i></span>'
        f'<b>{n:,}</b><small>{plural(si, "contestado")}</small></li>'
        for k, (n, si) in orden)
    ayuda = ("" if any(k in TIPOS_COMENTARIO for k in d["tipos"]) else
             '<p class="ayuda chica">Los comentarios se separan por tipo cuando eliges qué contestar en '
             '<a class="enlace" href="/panel/interacciones">Configura tus interacciones</a>.</p>')
    return f'<ul class="res-tipos">{filas}</ul>{ayuda}'


def comentarios_por_dia(d: dict) -> str:
    fechas = [d["inicio"] + timedelta(days=i) for i in range(d["dias"])]
    if d["dias"] <= 7:
        etiquetas = [DIAS[f.weekday()] for f in fechas]
    else:
        cada = 5 if d["dias"] <= 30 else 15
        etiquetas = [f"{f:%d/%m}" if (d["dias"] - 1 - i) % cada == 0 else "" for i, f in enumerate(fechas)]
    pico = max(range(d["dias"]), key=lambda i: d["por_dia"][i])
    resumen = (f'Promedio: {sum(d["por_dia"]) / d["dias"]:.1f} al día · el día con más fue el'
               f' {DIAS[fechas[pico].weekday()]} {fechas[pico]:%d/%m} ({d["por_dia"][pico]}).').replace(".0 al", " al")
    return (barras(d["por_dia"], etiquetas, [""] * d["dias"], descripcion="Comentarios por día")
            + f'<p class="ayuda" style="margin-top:.75rem">{resumen}</p>')


async def posts_comentados(c: Cuenta, d: dict) -> str:
    tarjetas = []
    for media_id, n in d["posts"]:
        x = await info_post(c, media_id)
        foto = (f'<img src="{escape(x["imagen"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                if x.get("imagen") else '<div class="sin-foto">Sin imagen</div>')
        if x.get("permalink", "").startswith("https://"):
            foto = f'<a href="{escape(x["permalink"])}" rel="noopener" title="Ver en Instagram">{foto}</a>'
        fecha = ""
        if x.get("fecha"):
            try:
                cuando = datetime.fromisoformat(x["fecha"].replace("+0000", "+00:00")).astimezone(ZoneInfo(c.timezone))
                fecha = f'<p class="pp-fecha">{"Reel" if x.get("tipo") == "REELS" else "Post"} · {cuando:%d/%m}</p>'
            except ValueError:
                pass
        tarjetas.append(
            f'<article class="pp"><div class="pp-foto">{foto}</div><div class="pp-cuerpo">{fecha}'
            f'<p class="pp-texto">{escape((x.get("caption") or "Publicación")[:140])}</p>'
            f'<div class="pp-tasa"><b class="tasa">{n:,}</b><small>{"comentario" if n == 1 else "comentarios"}</small></div>'
            "</div></article>")
    return ('<section class="met-c ancho res-posts res-com"><div class="met-cab"><span>Posts con más comentarios</span></div>'
            f'<div class="pp-grid">{"".join(tarjetas)}</div></section>')


async def comentarios_html(c: Cuenta, rango: str, fin: date) -> str:
    """Resultados > Comentarios: cuántos llegaron, qué hizo tu asistente, de qué tipo son, por día y en qué posts."""
    clave = (c.slug, "comentarios", rango, fin)
    guardado = _cache.get(clave)
    if guardado and guardado[0] > time.time():
        d = guardado[1]
    else:
        d = await numeros_comentarios(c, rango, fin)
        _cache[clave] = (time.time() + CACHE_SEGUNDOS, d)
    if not d["comentarios"] and not d["spam"] and not d["menciones"]:
        return ('<div class="tarjeta"><h2>Aún no hay comentarios en este periodo</h2>'
                '<p class="ayuda">Cuando comenten tus publicaciones, aquí verás cuántos llegaron, de qué tipo son y '
                'cuántos contestó tu asistente.</p><div class="acciones">'
                '<a class="boton" href="/panel/interacciones">Configura tus interacciones</a></div></div>')
    pct = round(d["contestados"] / d["comentarios"] * 100) if d["comentarios"] else 0
    kpis = (
        '<div class="hoy kpis res-kpis">'
        + kpi("Comentarios", f'{d["comentarios"]:,}', "en tus publicaciones")
        + kpi("Contestados", f"{pct}%", f'{d["contestados"]} de {d["comentarios"]} por tu asistente')
        + kpi("Respuestas públicas", f'{d["publicas"]:,}', "en los comentarios")
        + kpi("Mensajes directos", f'{d["dms"]:,}', "enviados por un comentario")
        + (kpi("Spam ocultado", f'{d["spam"]:,}') if d["spam"] else "")
        + (kpi("Menciones", f'{d["menciones"]:,}', "contestadas en su post") if d["menciones"] else "")
        + "</div>"
    )
    if not d["comentarios"]:
        return kpis
    palabras = "".join(f'<span class="chip">{escape(w)} · {n}</span> ' for w, n in d["palabras"])
    cuerpo = (
        kpis
        + '<div class="res-dos"><div class="tarjeta"><span class="num">Por día</span><h2>Comentarios recibidos</h2>'
        f"{comentarios_por_dia(d)}</div>"
        f'<div class="tarjeta"><span class="num">Por tipo</span><h2>Qué te comentan</h2>{tipos_html(d)}</div></div>'
    )
    if d["posts"]:
        cuerpo += f'<div class="met">{await posts_comentados(c, d)}</div>'
    if palabras:
        cuerpo += ('<div class="tarjeta"><span class="num">De comentario a mensaje</span><h2>Palabras clave más usadas</h2>'
                   f"<div>{palabras}</div></div>")
    return cuerpo


def dinero(v: float, moneda: str) -> str:
    return f"${v:,.0f}" + (f" {moneda}" if moneda else "")


def _veces(n: int, monto: float = 0, moneda: str = "") -> str:
    return (f"{n} · {dinero(monto, moneda)}" if monto else f"{n:,}") if n else "0"


async def anuncios_html(c: Cuenta, ini: date, fin: date) -> str:
    """Por anuncio: lo de Meta (gasto, clics y lo que mide su Pixel) junto a lo de sus chats (chats y hasta dónde
    llegaron). Solo salen las columnas con datos: una tienda ve compras y ventas; un spa, cotizaciones y citas."""
    zona = ZoneInfo(c.timezone)
    desde = datetime(ini.year, ini.month, ini.day, tzinfo=zona).timestamp()
    hasta = datetime(fin.year, fin.month, fin.day, tzinfo=zona).timestamp() + config.DIA
    nuestros = await c.store.por_anuncio(desde, hasta)
    datos = await anuncios_meta.conexion(c)
    meta = await anuncios_meta.por_anuncio(c, ini, fin)
    moneda = datos.get("moneda", "")
    filas: dict[str, dict] = {}
    for x in meta or []:
        if (x["gasto"] or x["clics"] or x["compras"] or x["prospectos"] or x["citas_meta"]
                or x["ad_id"] in nuestros):
            filas[x["ad_id"]] = {**x}
    for ad, x in nuestros.items():
        filas.setdefault(ad, {"ad_id": ad, "nombre": x["titulo"] or f"Anuncio {ad[-6:]}"})
        filas[ad].update(chats=x["chats"], cotizaciones=x["cotizaciones"], citas=x["citas"], ventas=x["ventas"],
                         monto=x["monto"], moneda_ventas=x["moneda"])
    aviso = ""
    if anuncios_meta.enabled() and (not datos.get("token") or anuncios_meta.vencida(datos)):
        aviso = ('<div class="tarjeta"><h2>Conecta tu cuenta de anuncios</h2><p class="ayuda">Suma aquí lo que ya'
                 " mide Meta de cada anuncio: el gasto, los clics y lo que registra tu Pixel (compras, prospectos o"
                 ' citas).</p><div class="acciones"><a class="boton" href="/panel/anuncios">Conectar tu cuenta de'
                 " anuncios</a></div></div>")
    elif anuncios_meta.enabled() and meta is None:
        aviso = ('<div class="tarjeta"><p class="ayuda">Meta no nos dio tus números de anuncios esta vez. Intenta en'
                 ' unos minutos o revisa tu conexión en <a class="enlace" href="/panel/anuncios">Tu cuenta de'
                 " anuncios</a>.</p></div>")
    if not filas:
        return aviso + ('<div class="tarjeta"><h2>Sin anuncios en este periodo</h2><p class="ayuda">Cuando tengas'
                        " anuncios activos, aquí verás lo que dejó cada uno: en tu página y en tus chats.</p></div>")

    def total(campo: str) -> float:
        return sum(x.get(campo, 0) for x in filas.values())

    moneda_ventas = next((x["moneda_ventas"] for x in filas.values() if x.get("moneda_ventas")), "")
    # (título, celda, total para su tarjeta, explicación al pie). Gasto, clics y chats siempre; lo demás si hay.
    de_meta = [
        ("compras", "Compras en tu página", lambda x: _veces(x.get("compras", 0), x.get("valor_compras", 0), moneda),
         dinero(total("valor_compras"), moneda) + " según tu Pixel", "las que registró tu Pixel"),
        ("prospectos", "Prospectos en Meta", lambda x: _veces(x.get("prospectos", 0)),
         "formularios y tu página", "los datos que dejaron en un formulario de Meta o en tu página"),
        ("contactos", "Contactos en tu página", lambda x: _veces(x.get("contactos", 0)),
         "según tu Pixel", "quienes te llamaron o escribieron desde tu página"),
        ("citas_meta", "Citas en tu página", lambda x: _veces(x.get("citas_meta", 0)),
         "según tu Pixel", "las que se agendaron en tu página"),
    ] if meta is not None else []
    de_chats = [
        ("cotizaciones", "Pidieron cotización", lambda x: _veces(x.get("cotizaciones", 0)),
         "en el chat", "quienes pidieron cotización, visita o dejaron sus datos"),
        ("citas", "Agendaron cita", lambda x: _veces(x.get("citas", 0)),
         "con tu asistente", "las citas que agendó tu asistente"),
        ("ventas", "Ventas por chat", lambda x: _veces(x.get("ventas", 0), x.get("monto", 0), x.get("moneda_ventas", "")),
         dinero(total("monto"), moneda_ventas), "pagos con link, pedidos de tu tienda y las que marcaste como vendidas"),
    ]
    de_meta = [col for col in de_meta if total(col[0])]
    de_chats = [col for col in de_chats if total(col[0])]
    con_meta = meta is not None
    tarjetas = []
    if con_meta:
        tarjetas.append(kpi("Gasto en anuncios", dinero(total("gasto"), moneda)))
        if de_meta:  # lo principal que mide su Pixel
            campo, titulo, _, sub, _ = de_meta[0]
            tarjetas.append(kpi(titulo, f"{total(campo):,.0f}", sub))
    tarjetas.append(kpi("Chats de anuncios", f"{total('chats'):,.0f}"))
    # Con Meta caben cuatro tarjetas: del chat va solo hasta dónde llegó (venta, si no cita, si no cotización).
    for campo, titulo, _, sub, _ in de_chats[-1:] if con_meta else de_chats:
        tarjetas.append(kpi(titulo, f"{total(campo):,.0f}", sub))
    kpis = '<div class="hoy kpis res-kpis">' + "".join(tarjetas) + "</div>"
    columnas = ([("Gasto", lambda x: dinero(x.get("gasto", 0), moneda)), ("Clics", lambda x: f'{x.get("clics", 0):,}')]
                if con_meta else [])
    columnas += [(t, f) for _, t, f, _, _ in de_meta] + [("Chats", lambda x: str(x.get("chats", 0)))]
    columnas += [(t, f) for _, t, f, _, _ in de_chats]
    orden = sorted(filas.values(), key=lambda x: (x.get("gasto", 0), x.get("chats", 0)), reverse=True)
    cuerpo = "".join("<tr><td>" + escape(x.get("nombre") or "") + "</td>"
                     + "".join(f"<td>{f(x)}</td>" for _, f in columnas) + "</tr>" for x in orden)
    pie = " ".join(f"{t}: {nota}." for _, t, _, _, nota in de_meta + de_chats)
    tabla = ('<div class="tarjeta"><span class="num">Por anuncio</span><h2>Lo que dejó cada anuncio</h2>'
             '<div class="ads-tabla"><table><tr><th>Anuncio</th>' + "".join(f"<th>{h}</th>" for h, _ in columnas)
             + "</tr>" + cuerpo + "</table></div>"
             + (f'<p class="ayuda">{pie}</p>' if pie else "") + "</div>")
    return aviso + kpis + tabla


async def picos_de(c: Cuenta, d: dict) -> dict[date, str]:
    """{día: texto del post que salió ese día}, para explicar el pico de vistas."""
    zona = ZoneInfo(c.timezone)
    salida: dict[date, str] = {}
    for p in sorted(d.get("publicadas", []), key=lambda p: p["publish_at"] or p["created_at"]):
        salida.setdefault(datetime.fromtimestamp(p["publish_at"] or p["created_at"], zona).date(),
                          (p["caption"] or "").split("\n")[0] or "una publicación")
    return salida


async def pagina(c: Cuenta, rango: str, fin: date | None = None, vista: str = "instagram",
                 formato: str = "", cada: str = "") -> HTMLResponse:
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    fin = min(fin or hoy, hoy)
    cada = metricas.leer_cada(cada, int(rango))
    if vista == "anuncios":
        cuerpo = await anuncios_html(c, fin - timedelta(days=int(rango) - 1), fin)
    elif vista == "comentarios":
        cuerpo = await comentarios_html(c, rango, fin)
    elif vista == "conversaciones":
        cuerpo = conversaciones_html(c, await numeros_de(c, rango, fin), cada)
    else:
        d = await numeros_de(c, rango, fin) if c.imagenes_mes > 0 else {}
        cuerpo = metricas.seccion(await metricas.numeros(c, int(rango), fin, cada), formato,
                                  lambda f: enlace(rango, fin, hoy, vista, formato=f, cada=cada), await picos_de(c, d))
        if c.imagenes_mes > 0:
            cuerpo += f'<div class="met">{await seccion_publicaciones(c, d)}</div>'
    return panel.pagina(
        await panel.menu_de(c, "resultados")
        + f'<main><div class="contenedor res">{await barra(c, rango, fin, hoy, vista, cada)}{cuerpo}</div></main>'
    )


def leer_fin(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def leer_rango(dias: str, desde: str, fin: date | None, hoy: date) -> tuple[str, date]:
    """(días, último día): 7, 30 o 90 con ?dias=, o lo que haya entre ?desde= y ?fin= (hasta MAX_DIAS)."""
    fin = min(fin or hoy, hoy)
    if (ini := leer_fin(desde)) and ini <= fin:
        return str(min((fin - ini).days + 1, MAX_DIAS)), fin
    return (dias if dias in RANGOS else "7"), fin


@router.get("")
async def ver(request: Request, dias: str = "7", fin: str = "", ver: str = "instagram", desde: str = "",
              formato: str = "", cada: str = ""):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    rango, fin_ = leer_rango(dias, desde, leer_fin(fin), hoy)
    return await pagina(c, rango, fin_, ver if ver in vistas_de(c) else "instagram",
                        formato if formato in metricas.NOMBRES_FORMATO else "", cada)


# --- descargas: PDF y CSV, del periodo (día por día) o por publicación ---

def csv_de(filas: list[list], columnas: list[str]) -> bytes:
    """CSV con BOM para que Excel lo abra con acentos; los números sin separador de miles."""
    import csv
    import io
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(columnas)
    for fila in filas:
        w.writerow(["" if v is None else v for v in fila])
    return ("\ufeff" + salida.getvalue()).encode("utf-8")


def csv_cuenta(m: dict, d: dict) -> bytes:
    """Un renglón por día del periodo con todo lo que guardamos de Instagram y los mensajes de ese día."""
    columnas = ["dia", "seguidores", "seguidores_nuevos", "dejaron_de_seguir", "alcance", "alcance_seguidores",
                "alcance_no_seguidores", "vistas", "vistas_seguidores", "vistas_no_seguidores", "vistas_posts",
                "vistas_reels", "vistas_historias", "vistas_anuncios", "interacciones", "likes", "comentarios",
                "compartidos", "guardados", "respuestas_historias", "interaccion_pct", "cuentas_que_interactuaron",
                "clics_perfil", "mensajes_recibidos"]
    por_dia = {(d["inicio"] + timedelta(days=i)).date(): n for i, n in enumerate(d["por_dia"])}
    filas = []
    for f, x in zip(m["dias_fechas"], m["serie"] if "dias_serie" not in m else m["dias_serie"]):
        seg_v, seg_a, tipos = x.get("vistas_seg") or {}, x.get("alcance_seg") or {}, x.get("vistas_tipo") or {}
        otros = lambda g: (g.get("otros", 0) + g.get("sin_dato", 0)) if g else None  # noqa: E731
        filas.append([f.isoformat(), x.get("seguidores"), x.get("nuevos"), x.get("perdidos"), x.get("alcance"),
                      seg_a.get("seguidores") if seg_a else None, otros(seg_a), x.get("vistas"),
                      seg_v.get("seguidores") if seg_v else None, otros(seg_v),
                      tipos.get("posts") if tipos else None, tipos.get("reels") if tipos else None,
                      tipos.get("historias") if tipos else None, tipos.get("anuncios") if tipos else None,
                      x.get("interacciones"), x.get("likes"), x.get("comentarios"), x.get("compartidos"),
                      x.get("guardados"), x.get("respuestas"), metricas.tasa_dias([x]) if x else None, x.get("cuentas"),
                      x.get("clics"), por_dia.get(f)])
    return csv_de(filas, columnas)


async def filas_posts(c: Cuenta, d: dict) -> list[list]:
    """Una fila por publicación del periodo: cuándo salió, qué decía y sus números."""
    zona = ZoneInfo(c.timezone)
    filas = []
    for x in await posts_con_numeros(c, d.get("publicadas", []), d.get("inicio"), d.get("dias", 0)):
        p, n = x["post"], x["datos"] or {}
        cuando = datetime.fromtimestamp(p["publish_at"] or p["created_at"], zona)
        filas.append([cuando.strftime("%Y-%m-%d"), cuando.strftime("%H:%M"), p.get("tipo") or "post",
                      (p["caption"] or "").replace("\n", " ")[:140], p["permalink"] if p["permalink"].startswith("https://") else "",
                      n.get("reach"), n.get("views"), n.get("likes"), n.get("comments"), n.get("saved"), n.get("shares"),
                      x["tasa"], n.get("profile_visits"), n.get("follows"), n.get("replies"),
                      "instagram" if p.get("externo") else "skandar"])
    return filas


COLUMNAS_POSTS = ["fecha", "hora", "tipo", "texto", "enlace", "alcance", "vistas", "likes", "comentarios", "guardados",
                  "compartidos", "interaccion_pct", "visitas_perfil", "seguidores_nuevos", "respuestas", "origen"]


@router.get("/descargar")
async def descargar(request: Request, dias: str = "7", fin: str = "", ver: str = "instagram", desde: str = "",
                    archivo: str = "pdf", que: str = "cuenta", cada: str = ""):
    """Lo que se ve en Resultados (Instagram o Conversaciones, en su periodo): en PDF o CSV, del periodo
    día por día (`que=cuenta`) o una fila por publicación (`que=posts`, solo Instagram)."""
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    from . import informe
    ver = ver if ver in VISTAS_PDF else "instagram"
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    rango, fin_ = leer_rango(dias, desde, leer_fin(fin), hoy)
    ini = fin_ - timedelta(days=int(rango) - 1)
    que = "posts" if que == "posts" and ver == "instagram" else "cuenta"
    archivo = "csv" if archivo == "csv" else "pdf"
    d = await numeros_de(c, rango, fin_)
    cada = metricas.leer_cada(cada, int(rango))
    m = await metricas.numeros(c, int(rango), fin_, cada)  # el PDF sigue la granularidad; el CSV va día por día
    nombre = f"resultados-{ver if que == 'cuenta' else 'publicaciones'}-{ini.isoformat()}-al-{fin_.isoformat()}.{archivo}"
    if archivo == "csv":
        cuerpo = await filas_posts(c, d) if que == "posts" else None
        datos = csv_de(cuerpo, COLUMNAS_POSTS) if que == "posts" else csv_cuenta(m, d)
        tipo = "text/csv; charset=utf-8"
    elif que == "posts":
        datos = await informe.posts_pdf(c, titulo_periodo(ini, fin_), await filas_posts(c, d), await panel.usuario(c))
        tipo = "application/pdf"
    else:
        datos = await informe.resultados_pdf(c, titulo_periodo(ini, fin_), d, m, await panel.usuario(c), ver, cada)
        tipo = "application/pdf"
    return Response(datos, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
