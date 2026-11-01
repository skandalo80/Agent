"""Métricas diarias de Instagram: se guardan una vez al día y se ven en Resultados.

Cada día, desde las HORA, el bucle de fondo de app.py (tick) guarda por cuenta:
- Los seguidores, seguidos y publicaciones de ese momento (Instagram solo da el número
  de hoy: si no se guarda, el historial se pierde).
- Los insights de los días anteriores (alcance, vistas, cuentas que interactuaron,
  likes, comentarios, compartidos, guardados, clics al enlace y seguidores ganados y
  perdidos), con sus desgloses: vistas y alcance de seguidores y de quien no te sigue y por
  formato (posts, reels, historias, anuncios), y los clics por botón (WhatsApp, llamar...).
  Se piden los que falten de los últimos DIAS_ATRAS y se vuelven a pedir los dos últimos, que
  Instagram todavía ajusta.
- El historial: al conectar una cuenta solo existe el día de hoy. Cada vuelta del bucle (10 min)
  se rellenan LOTE_RELLENO días hacia atrás hasta completar DIAS_HISTORIAL (Instagram da hasta
  30 días por consulta y guarda dos años), así una cuenta nueva ve sus 90 días en unas horas sin
  pasarse del límite de llamadas.
- Una vez por semana, el público: edades, género y ciudades (Instagram solo lo da a
  cuentas con 100 seguidores o más).
- Las estadísticas de cada post publicado en los últimos 30 días (informe.refrescar_numeros), para que
  Campañas, Pasadas y Resultados las lean de la base sin preguntarle a Instagram.

Son unas 6 llamadas por día guardado (los desgloses van en llamadas aparte, porque Instagram no deja
mezclar métricas con desgloses distintos) más una por post de esos 30 días, y una fila al día en
metricas_dia. Las métricas que Instagram no da (cuenta chica, permiso que falta) se
saltan sin romper las demás.

La sección de Resultados son tarjetas con números, líneas y donas en SVG hechas aquí
(el CSP del panel no deja JavaScript); sus estilos están en panel.ESTILO (.met).
"""
import json
import logging
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from . import config, informe, tools
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.metricas")

HORA = 6
DIAS_ATRAS = 7
DIAS_HISTORIAL = 90  # cuánto se rellena hacia atrás al conectar una cuenta
LOTE_RELLENO = 5  # días que se rellenan por vuelta del bucle (unas 30 llamadas)
VERSION_DIA = 3  # qué se pidió de cada día; sube cuando se agregan métricas y los días viejos se vuelven a pedir
CLAVE_INTENTO = "metricas_intento"
CLAVE_RELLENO = "metricas_relleno"  # hasta qué día (hacia atrás) ya se revisó el historial; "listo" al terminar
CLAVE_PUBLICO = "metricas_publico"
CLAVE_HORA = "metricas_hora"  # cuándo fue el último guardado (para "actualizado hoy a las 6:00")
CLAVE_HORAS = "metricas_horas"  # a qué horas están conectados sus seguidores (semanal; las campañas eligen la hora)
CLAVE_TEMAS = "metricas_temas"  # lo que más preguntan por chat, escrito por Claude una vez al día (7 y 30 días)
TEMAS_DIAS = (7, 30)
CADA = {"dia": ("Por día", 1), "semana": ("Por semana", 7), "mes": ("Por mes", 30)}  # granularidad de las gráficas
TOP_HORAS = 3

# nombre en Instagram -> nombre guardado
METRICAS = {
    "reach": "alcance", "views": "vistas", "accounts_engaged": "cuentas", "total_interactions": "interacciones",
    "likes": "likes", "comments": "comentarios", "shares": "compartidos", "saves": "guardados",
    "replies": "respuestas", "profile_links_taps": "clics",
}
ACCIONES = ("likes", "comentarios", "compartidos", "guardados", "respuestas")  # lo que cuenta como interacción
REFERENCIA = "Entre 3 % y 5 % es lo normal en negocios; más de 6 % es muy bueno."
PERFIL = {"followers_count": "seguidores", "follows_count": "siguiendo", "media_count": "publicaciones"}
# Desgloses que se piden aparte: (métricas, breakdown) -> clave guardada por métrica. Instagram no deja mezclar
# en una llamada métricas con desgloses distintos, y con un desglose que no existe falla toda la llamada.
DESGLOSES = [
    ("reach,views", "media_product_type", {"reach": "alcance_tipo", "views": "vistas_tipo"}),
    ("views", "follower_type", {"views": "vistas_seg"}),
    ("reach", "follow_type", {"reach": "alcance_seg"}),
    ("profile_links_taps", "contact_button_type", {"profile_links_taps": "clics_tipo"}),
]
# Formatos de Instagram -> cómo se agrupan en Resultados (los carruseles son posts).
FORMATOS = {"POST": "posts", "CAROUSEL_CONTAINER": "posts", "FEED": "posts", "REEL": "reels", "REELS": "reels",
            "STORY": "historias", "AD": "anuncios"}
NOMBRES_FORMATO = {"posts": "Posts", "reels": "Reels", "historias": "Historias", "anuncios": "Anuncios"}
BOTONES = {"TEXT": "WhatsApp o SMS", "CALL": "Llamadas", "DIRECTION": "Cómo llegar", "EMAIL": "Correo",
           "BOOK_NOW": "Reservar", "INSTANT_EXPERIENCE": "Otro", "UNDEFINED": "Otro"}
EDADES = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENEROS = {"F": "Mujeres", "M": "Hombres", "U": "Sin dato"}


# --- guardar ---

def total(m: dict) -> int:
    return int((m.get("total_value") or {}).get("value") or 0)


def desglose(m: dict) -> dict[str, int]:
    """{valor de la dimensión: número} de una métrica pedida con breakdown."""
    resultados = ((m.get("total_value") or {}).get("breakdowns") or [{}])[0].get("results") or []
    return {"/".join(r.get("dimension_values") or []): int(r.get("value") or 0) for r in resultados}


async def insights_del_dia(c: Cuenta, dia: date) -> dict:
    """Los números de un día completo (de 0:00 a 0:00 en la zona de la cuenta)."""
    desde = int(datetime(dia.year, dia.month, dia.day, tzinfo=ZoneInfo(c.timezone)).timestamp())
    rango = {"period": "day", "metric_type": "total_value", "since": desde, "until": desde + config.DIA}
    try:
        datos = await c.instagram.insights_cuenta(",".join(METRICAS), **rango)
    except RuntimeError as e:  # alguna no existe para esta cuenta: se piden una por una
        log.info("Insights juntos fallaron para %s (%s); van uno por uno", c.slug or "la principal", e)
        datos = []
        for m in METRICAS:
            try:
                datos += await c.instagram.insights_cuenta(m, **rango)
            except RuntimeError:
                pass
    valores = {METRICAS[m["name"]]: total(m) for m in datos if m.get("name") in METRICAS}
    try:
        seguir = await c.instagram.insights_cuenta("follows_and_unfollows", breakdown="follow_type", **rango)
        if seguir:
            d = desglose(seguir[0])
            valores.update(nuevos=d.get("FOLLOWER", 0), perdidos=d.get("NON_FOLLOWER", 0))
    except RuntimeError:  # menos de 100 seguidores
        pass
    for metricas_, breakdown, claves in DESGLOSES:
        try:
            datos = await c.instagram.insights_cuenta(metricas_, breakdown=breakdown, **rango)
        except RuntimeError:  # ese desglose no existe para esta cuenta (o aún no en la API)
            continue
        for m in datos:
            if (clave := claves.get(m.get("name", ""))) and (d := desglose(m)):
                valores[clave] = agrupar(d, breakdown)
    valores["v"] = VERSION_DIA
    return valores


def agrupar(d: dict[str, int], breakdown: str) -> dict[str, int]:
    """Un desglose de Instagram con nuestros nombres: formatos juntos (posts, reels, historias, anuncios),
    seguidores / otros, o el tipo de botón tal cual."""
    salida: dict[str, int] = {}
    for k, v in d.items():
        if breakdown == "media_product_type":
            k = FORMATOS.get(k, "otros")
        elif breakdown in ("follower_type", "follow_type"):
            k = "seguidores" if k == "FOLLOWER" else "otros" if k == "NON_FOLLOWER" else "sin_dato"
        salida[k] = salida.get(k, 0) + v
    return salida


async def publico(c: Cuenta) -> dict:
    """Edades, género y ciudades de los seguidores ({} si Instagram no los da)."""
    salida = {}
    for clave, dimension in (("edad", "age"), ("genero", "gender"), ("ciudad", "city")):
        try:
            datos = await c.instagram.insights_cuenta("follower_demographics", period="lifetime",
                                                      metric_type="total_value", breakdown=dimension)
        except RuntimeError:
            continue
        if datos and (d := desglose(datos[0])):
            salida[clave] = d
    return salida


async def horas_activas(c: Cuenta) -> dict[str, float]:
    """{hora local "0".."23": seguidores conectados en promedio} de los últimos días, según Instagram
    (online_followers viene por hora UTC); {} si no los da."""
    try:
        datos = await c.instagram.insights_cuenta("online_followers", period="lifetime")
    except RuntimeError:
        return {}
    suma: dict[int, float] = {}
    dias = 0
    for valor in (datos[0].get("values") if datos else None) or []:
        if not isinstance(valor.get("value"), dict):
            continue
        dias += 1
        for h, n in valor["value"].items():
            if str(h).isdigit():
                suma[int(h)] = suma.get(int(h), 0) + float(n or 0)
    if not dias:
        return {}
    corrimiento = int((datetime.now(ZoneInfo(c.timezone)).utcoffset() or timedelta()).total_seconds() // config.HORA)
    return {str((h + corrimiento) % 24): round(n / dias, 1) for h, n in sorted(suma.items())}


async def guardar(c: Cuenta, hoy: date) -> None:
    store = c.store
    await store.guardar_metricas(hoy.isoformat(), {
        PERFIL[k]: v for k, v in (await c.instagram.numeros_cuenta()).items()})
    guardados = await store.metricas((hoy - timedelta(days=DIAS_ATRAS)).isoformat())
    for atras in range(DIAS_ATRAS, 0, -1):
        dia = hoy - timedelta(days=atras)
        if atras <= 2 or not completo(guardados.get(dia.isoformat(), {})):
            if valores := await insights_del_dia(c, dia):
                await store.guardar_metricas(dia.isoformat(), valores)
    try:
        log.info("Estadísticas de %d posts refrescadas para %s", await informe.refrescar_numeros(c),
                 c.slug or "la principal")
    except Exception:  # no deja sin público ni seguidores a la cuenta
        log.exception("No se pudieron refrescar las estadísticas de los posts de %s", c.slug or "la principal")
    await guardar_temas(c, hoy)
    semana = f"{hoy.isocalendar()[0]}-{hoy.isocalendar()[1]}"
    anterior = json.loads(await store.get(CLAVE_PUBLICO) or "{}")
    if anterior.get("semana") != semana:
        await store.set(CLAVE_PUBLICO, json.dumps({"semana": semana, **await publico(c)}))
        await store.set(CLAVE_HORAS, json.dumps({"semana": semana, "horas": await horas_activas(c)}))


def completo(datos: dict) -> bool:
    """Si ese día ya se pidió con todo lo que hoy se guarda (métricas y desgloses)."""
    return "alcance" in datos and datos.get("v", 1) >= VERSION_DIA


async def rellenar(c: Cuenta, hoy: date) -> int:
    """El historial hacia atrás, LOTE_RELLENO días por vuelta: desde el día anterior al último revisado hasta
    DIAS_HISTORIAL días atrás. Devuelve cuántos días pidió. Los días que Instagram da vacíos se marcan
    revisados para no pedirlos otra vez."""
    store = c.store
    marca = await store.get(CLAVE_RELLENO)
    if marca == "listo":
        return 0
    tope = hoy - timedelta(days=DIAS_HISTORIAL)
    dia = (date.fromisoformat(marca) if marca else hoy) - timedelta(days=1)
    guardados = await store.metricas(tope.isoformat())
    pedidos = 0
    while dia >= tope and pedidos < LOTE_RELLENO:
        if not completo(guardados.get(dia.isoformat(), {})):
            valores = await insights_del_dia(c, dia)
            await store.guardar_metricas(dia.isoformat(), {**valores, "v": VERSION_DIA})
            pedidos += 1
        dia -= timedelta(days=1)
    await store.set(CLAVE_RELLENO, "listo" if dia < tope else (dia + timedelta(days=1)).isoformat())
    return pedidos


async def guardar_temas(c: Cuenta, hoy: date) -> None:
    """Lo que más preguntan por chat en los últimos 7 y 30 días, para Resultados > Conversaciones. Una llamada
    barata por periodo con mensajes; si Claude falla, se queda lo anterior."""
    from . import reporte
    zona = ZoneInfo(c.timezone)
    hasta = datetime(hoy.year, hoy.month, hoy.day, tzinfo=zona).timestamp()
    temas = json.loads(await c.store.get(CLAVE_TEMAS) or "{}")
    for dias in TEMAS_DIAS:
        try:
            texto = await reporte.topics(c.store, hasta - dias * config.DIA, hasta, f"los últimos {dias} días")
        except Exception:
            log.exception("No se pudieron resumir los temas de %d días para %s", dias, c.slug or "la principal")
            continue
        temas[str(dias)] = {"texto": texto, "hasta": hoy.isoformat()}
    await c.store.set(CLAVE_TEMAS, json.dumps(temas, ensure_ascii=False))


async def tick(c: Cuenta) -> None:
    """Lo llama el bucle de fondo de app.py cada pocos minutos: el guardado del día una vez, desde las HORA,
    y mientras falte historial, un lote de días hacia atrás en cada vuelta."""
    if not c.access_token:
        return
    with usar(c):
        ahora = tools.now()
        hoy = ahora.date()
        if ahora.hour >= HORA and await c.store.get(CLAVE_INTENTO) != hoy.isoformat():
            await c.store.set(CLAVE_INTENTO, hoy.isoformat())  # antes de llamar: un intento al día
            await guardar(c, hoy)
            await c.store.set(CLAVE_HORA, str(ahora.timestamp()))
            log.info("Métricas del %s guardadas para %s", hoy, c.slug or "la principal")
        if await c.store.get(CLAVE_INTENTO):  # ya hubo un primer guardado: el historial va después
            if n := await rellenar(c, hoy):
                log.info("Historial: %d días más para %s", n, c.slug or "la principal")
        try:
            if n := await informe.leer_historias(c):
                log.info("Números de %d historias guardados para %s", n, c.slug or "la principal")
        except Exception:
            log.exception("No se pudieron leer las historias de %s", c.slug or "la principal")


# --- números para Resultados ---

def cambio(actual: int | None, antes: int | None) -> float | None:
    if actual is None or not antes:
        return None
    return (actual - antes) / antes * 100


def tasa_dias(xs: list[dict]) -> float | None:
    """El % de interacción de unos días: la suma de sus interacciones entre la suma de su alcance (la misma
    fórmula que la de cada publicación, informe.tasa). None sin alcance."""
    alcance = sum(x.get("alcance") or 0 for x in xs)
    if not alcance:
        return None
    return round(sum(x.get(k, 0) for x in xs for k in ACCIONES) / alcance * 100, 1)


def puntos(actual: float | None, antes: float | None) -> float | None:
    """La diferencia entre dos tasas, en puntos (3,8 % contra 3,2 % son +0,6 pts, no +19 %)."""
    if actual is None or antes is None:
        return None
    return round(actual - antes, 1)


def seguidores_en(dias: dict[str, dict], dia: date) -> int | None:
    """Seguidores guardados ese día, o el día guardado más cercano antes de él."""
    previos = [d for d in dias if d <= dia.isoformat() and "seguidores" in dias[d]]
    return dias[max(previos)]["seguidores"] if previos else None


def cada_por_defecto(dias: int) -> str:
    """Hasta dos semanas se ve día por día; después por semana, y por mes a partir de medio año."""
    return "dia" if dias <= 14 else "semana" if dias <= 180 else "mes"


def opciones_cada(dias: int) -> list[str]:
    """Las granularidades con al menos dos bloques completos en el periodo."""
    return [k for k, (_, bloque) in CADA.items() if dias >= 2 * bloque]


def leer_cada(texto: str, dias: int) -> str:
    return texto if texto in opciones_cada(dias) else cada_por_defecto(dias)


def salto(dias: int, cada: str) -> int:
    """Cuántos días atrás está el periodo anterior: el mismo largo, pero en semanas o meses completos para
    que caigan los mismos días de la semana (un sábado contra un sábado, no contra un lunes)."""
    bloque = CADA[cada][1]
    return bloque * -(-dias // bloque)


def sumar_dias(xs: list[dict]) -> dict:
    """Un bloque (semana o mes) a partir de sus días: todo se suma; los seguidores son los del último día con
    dato y el alcance es el promedio al día (cada día cuenta personas distintas)."""
    con = [x for x in xs if x]
    if not con:
        return {}
    salida: dict = {}
    for x in con:
        for k, v in x.items():
            if k in ("v", "seguidores", "alcance", "siguiendo", "publicaciones"):
                continue
            if isinstance(v, dict):
                g = salida.setdefault(k, {})
                for kk, vv in v.items():
                    g[kk] = g.get(kk, 0) + vv
            elif isinstance(v, (int, float)):
                salida[k] = salida.get(k, 0) + v
    alcances = [x["alcance"] for x in con if x.get("alcance") is not None]
    if alcances:
        salida["alcance"] = round(sum(alcances) / len(alcances))
    for x in reversed(con):
        if "seguidores" in x:
            salida["seguidores"] = x["seguidores"]
            break
    return salida


def bloques(fechas: list[date], serie: list[dict], bloque: int) -> tuple[list[date], list[dict]]:
    """Los días en bloques completos de `bloque` días, contados desde el final (el último bloque termina en el
    último día). Devuelve la fecha en que empieza cada bloque y su suma; los días sueltos del principio sobran."""
    n = len(fechas) // bloque
    sobran = len(fechas) - n * bloque
    inicios = [fechas[sobran + i * bloque] for i in range(n)]
    return inicios, [sumar_dias(xs) for xs in trozos(serie, bloque)]


def trozos(serie: list[dict], bloque: int) -> list[list[dict]]:
    """Los días de cada bloque completo, contados desde el final (como `bloques`)."""
    n = len(serie) // bloque
    sobran = len(serie) - n * bloque
    return [serie[sobran + i * bloque: sobran + (i + 1) * bloque] for i in range(n)]


def suma_tipos(serie: list[dict], clave: str) -> dict[str, int]:
    """{formato o botón: total del periodo} de un desglose guardado por día."""
    total: dict[str, int] = {}
    for d in serie:
        for k, v in (d.get(clave) or {}).items():
            total[k] = total.get(k, 0) + v
    return total


def por_formato(d: dict, clave: str, formato: str) -> int | None:
    """El valor de un día para un formato ("posts", "reels"...), o None si ese día no tiene desglose."""
    tipos = d.get(clave)
    return None if tipos is None else tipos.get(formato, 0)


async def numeros(c: Cuenta, rango_dias: int, fin: date | None = None, cada: str = "dia") -> dict:
    """Los `rango_dias` que terminan en `fin` (hoy si no viene), y el periodo anterior del mismo largo para
    comparar. Hoy no tiene insights aún: las líneas llegan hasta ayer; los seguidores son los del último día
    guardado hasta `fin`. Con `cada` semana o mes, las series van en bloques completos (ver `bloques`) y el
    periodo anterior se corre semanas o meses enteros para que coincidan los días de la semana; los números
    del periodo (totales y comparaciones) siempre cuentan todos los días del rango."""
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    fin = min(fin or hoy, hoy)
    ultimo = min(fin, hoy - timedelta(days=1))
    atras = salto(rango_dias, cada)
    desde = fin - timedelta(days=rango_dias + max(atras, 30) + 1)
    dias = await c.store.metricas(desde.isoformat())
    dias = {k: v for k, v in dias.items() if k <= fin.isoformat()}
    fechas = [ultimo - timedelta(days=i) for i in range(rango_dias - 1, -1, -1)]  # días completos
    serie = [dias.get(f.isoformat(), {}) for f in fechas]
    antes = [dias.get((f - timedelta(days=atras)).isoformat(), {}) for f in fechas]  # el periodo anterior
    con_seguidores = [d for d in dias if "seguidores" in dias[d]]
    actual = seguidores_en(dias, fin)
    inicio_periodo = fin - timedelta(days=rango_dias)  # el día antes de que empiece el periodo
    bloque = CADA[cada][1]
    if bloque > 1:
        fechas_g, serie_g = bloques(fechas, serie, bloque)
        _, antes_g = bloques(fechas, antes, bloque)
    else:
        fechas_g, serie_g, antes_g = fechas, serie, antes
    suma = lambda k, s=serie: sum(d.get(k, 0) for d in s)  # noqa: E731
    promedio = lambda k, s: (lambda xs: sum(xs) / len(xs) if xs else None)([d[k] for d in s if d.get(k) is not None])  # noqa: E731
    inter = {k: suma(k) for k in ACCIONES}
    inter_antes = sum(suma(k, antes) for k in inter)
    tasa, tasa_antes = tasa_dias(serie), tasa_dias(antes)
    relleno = await c.store.get(CLAVE_RELLENO)
    return {
        "fechas": fechas_g, "serie": serie_g, "antes": antes_g, "hay": bool(dias),
        "cada": cada, "bloque": bloque, "dias_fechas": fechas, "dias_serie": serie,
        "seguidores": actual,
        "semana": cambio(actual, seguidores_en(dias, fin - timedelta(days=7))),
        "mes": cambio(actual, seguidores_en(dias, fin - timedelta(days=30))),
        "periodo": cambio(actual, seguidores_en(dias, inicio_periodo)),  # vs. cuando empezó el periodo
        "desde": min(con_seguidores) if con_seguidores else None,
        "nuevos": suma("nuevos"), "perdidos": suma("perdidos"),
        "nuevos_cambio": cambio(suma("nuevos"), suma("nuevos", antes)),
        "vistas": suma("vistas"), "vistas_cambio": cambio(suma("vistas"), suma("vistas", antes)),
        "clics": suma("clics"), "clics_cambio": cambio(suma("clics"), suma("clics", antes)),
        "interacciones": inter, "interacciones_cambio": cambio(sum(inter.values()), inter_antes),
        "interaccion": tasa, "interaccion_cambio": puntos(tasa, tasa_antes),  # % de interacción del periodo, en pts
        "interaccion_bloques": [tasa_dias(xs) for xs in trozos(serie, bloque)] if bloque > 1 else [tasa_dias([x]) for x in serie],
        "interaccion_antes": [tasa_dias(xs) for xs in trozos(antes, bloque)] if bloque > 1 else [tasa_dias([x]) for x in antes],
        "cuentas": promedio("cuentas", serie),  # cuentas distintas que interactuaron, al día
        "alcance_dia": [d.get("alcance") for d in serie_g],
        "alcance_promedio": promedio("alcance", serie),
        "alcance_cambio": cambio(promedio("alcance", serie), promedio("alcance", antes)),
        "vistas_seg": suma_tipos(serie, "vistas_seg"), "alcance_seg": suma_tipos(serie, "alcance_seg"),
        "vistas_tipo": suma_tipos(serie, "vistas_tipo"), "clics_tipo": suma_tipos(serie, "clics_tipo"),
        "publico": json.loads(await c.store.get(CLAVE_PUBLICO) or "{}"),
        "horas": json.loads(await c.store.get(CLAVE_HORAS) or "{}").get("horas") or {},
        "rellenando": bool(relleno) and relleno != "listo",
    }


# --- dibujo ---

def cifra(n: int | float) -> str:
    return f"{n:,.0f}"


def pct(n: float | None) -> str:
    if n is None:
        return ""
    clase = "sube" if n > 0 else "baja" if n < 0 else ""
    return f'<span class="met-pct {clase}">{"+" if n > 0 else ""}{n:.1f}%</span>'.replace(".0%", "%")


def suave(puntos: list[tuple[float, float]]) -> str:
    """Curva que pasa por los puntos (Catmull-Rom a Bézier), sin salirse de 0..100 en y."""
    if len(puntos) == 1:
        x, y = puntos[0]
        return f"M{x - 1:.1f},{y:.1f}L{x + 1:.1f},{y:.1f}"
    camino = f"M{puntos[0][0]:.1f},{puntos[0][1]:.1f}"
    for i in range(len(puntos) - 1):
        p0, p1, p2 = puntos[max(i - 1, 0)], puntos[i], puntos[i + 1]
        p3 = puntos[min(i + 2, len(puntos) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, min(max(p1[1] + (p2[1] - p0[1]) / 6, 0), 100))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, min(max(p2[1] - (p3[1] - p1[1]) / 6, 0), 100))
        camino += f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return camino


def puntos_hover(columnas: list[tuple[float | None, str, str]], punto: bool = True) -> str:
    """Una columna por dato encima de la gráfica (HTML, porque el SVG se estira y deformaría el punto y
    el texto): al pasar el cursor sale un puntito en `y` (% desde arriba) y la etiqueta con el valor y
    la fecha, dentro de la gráfica. Sin JavaScript (el CSP no lo deja): solo :hover."""
    n = len(columnas)
    partes = []
    for i, (y, valor, cuando) in enumerate(columnas):
        if y is None:
            partes.append('<div class="mh"></div>')
            continue
        lado = (" izq" if i < n * .2 else " der" if i >= n * .8 else "") + (" abajo" if y < 35 else "")
        partes.append(f'<div class="mh{lado}" style="--y:{y:.1f}%" tabindex="0">' + ('<i></i>' if punto else "")
                      + f'<span>{valor}' + (f"<small>{cuando}</small>" if cuando else "") + '</span></div>')
    return f'<div class="met-hover">{"".join(partes)}</div>'


def eje_fechas(fechas: list[date], cada: str = "dia") -> str:
    if cada != "dia":  # cada bloque lleva su fecha de inicio; en tiras largas, una de cada dos o tres
        paso = 1 if len(fechas) <= 8 else 2 if len(fechas) <= 16 else 3
    else:
        paso = 1 if len(fechas) <= 7 else 7 if len(fechas) <= 30 else 15
    return "".join(f"<span>{f:%d/%m}</span>" if (len(fechas) - 1 - i) % paso == 0 else "<span></span>"
                   for i, f in enumerate(fechas))


POR_CADA = {"dia": "por día", "semana": "por semana", "mes": "por mes"}
VACIO = {"dia": "Aún sin datos de estos días.", "semana": "Aún no hay una semana completa con datos.",
         "mes": "Aún no hay un mes completo con datos."}


def linea(valores: list[int | None], fechas: list[date], nombre: str, antes: list[int | None] | None = None,
          cada: str = "dia") -> str:
    """Línea por día (o por semana o mes) con su relleno, desde 0; los puntos sin dato se saltan. Con `antes`,
    el periodo anterior en gris punteado. Al pasar el cursor (o tocar) sale el valor y, si lo hay, el del
    periodo anterior."""
    ancho, alto = 10 * len(valores), 100
    hay = [v for v in valores if v is not None]
    if not hay:
        return f'<p class="ayuda met-vacio">{VACIO[cada]}</p>'
    antes = antes or [None] * len(valores)
    tope = max(hay + [v for v in antes if v is not None]) or 1
    y = lambda v: 8 + (tope - v) / tope * 84  # noqa: E731
    puntos = [(i * 10 + 5, y(v)) for i, v in enumerate(valores) if v is not None]
    camino = suave(puntos)
    relleno = f"{camino}L{puntos[-1][0]:.1f},{alto}L{puntos[0][0]:.1f},{alto}Z"
    guias = "".join(f'<line class="guia" x1="0" x2="{ancho}" y1="{g}" y2="{g}" vector-effect="non-scaling-stroke"/>'
                    for g in (8, 50, 92))
    previos = [(i * 10 + 5, y(v)) for i, v in enumerate(antes) if v is not None]
    gris = (f'<path class="antes" d="{suave(previos)}" vector-effect="non-scaling-stroke"/>' if previos else "")
    zonas = puntos_hover([(None, "", "") if v is None else
                          (y(v), f"<b>{cifra(v)}</b>", f"antes {cifra(a)}" if a is not None else "")
                          for v, a in zip(valores, antes)])
    return (f'<div class="met-graf"><div class="met-escala"><span>{cifra(tope)}</span><span>0</span></div>'
            f'<svg class="met-linea" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none" role="img"'
            f' aria-label="{escape(nombre)} {POR_CADA[cada]}">{guias}<path class="area" d="{relleno}"/>{gris}'
            f'<path class="trazo" d="{camino}" vector-effect="non-scaling-stroke"/></svg>{zonas}</div>'
            f'<div class="ejex met-eje">{eje_fechas(fechas, cada)}</div>')


def apiladas(partes: list[tuple[int | None, int | None]], fechas: list[date], antes: list[int | None],
             nombre: str, cada: str = "dia") -> str:
    """Barras por día (o semana o mes) apiladas (seguidores abajo, otros arriba) desde 0, con el periodo
    anterior en gris punteado. Cada parte es (seguidores, otros); sin desglose va (total, None)."""
    n = len(partes)
    ancho, alto = 10 * n, 100
    totales = [None if (a is None and b is None) else (a or 0) + (b or 0) for a, b in partes]
    hay = [v for v in totales if v is not None]
    if not hay:
        return f'<p class="ayuda met-vacio">{VACIO[cada]}</p>'
    tope = max(hay + [v for v in antes if v is not None]) or 1
    y = lambda v: 8 + (tope - v) / tope * 84  # noqa: E731
    barras = []
    for i, ((seg, otros), total) in enumerate(zip(partes, totales)):
        if total is None:
            continue
        x = i * 10 + 2
        if otros is None:  # sin desglose: una barra
            barras.append(f'<rect class="seg" x="{x}" y="{y(total):.1f}" width="6" height="{alto - y(total):.1f}"/>')
            continue
        base = alto - y(seg or 0)
        barras.append(f'<rect class="seg" x="{x}" y="{alto - base:.1f}" width="6" height="{base:.1f}"/>')
        if otros:
            barras.append(f'<rect class="otros" x="{x}" y="{y(total):.1f}" width="6" height="{alto - y(total) - base:.1f}"/>')
    previos = [(i * 10 + 5, y(v)) for i, v in enumerate(antes) if v is not None]
    gris = f'<path class="antes" d="{suave(previos)}" vector-effect="non-scaling-stroke"/>' if previos else ""
    guias = "".join(f'<line class="guia" x1="0" x2="{ancho}" y1="{g}" y2="{g}" vector-effect="non-scaling-stroke"/>'
                    for g in (8, 50, 92))
    zonas = puntos_hover([
        (None, "", "") if t is None else
        (y(t), f"<b>{cifra(t)}</b>", " · ".join(x for x in (
            f"{cifra(seg or 0)} seguidores" if otros is not None else "",
            f"antes {cifra(a)}" if a is not None else "") if x))
        for (seg, otros), t, a in zip(partes, totales, antes)], punto=False)
    return (f'<div class="met-graf apiladas"><div class="met-escala"><span>{cifra(tope)}</span><span>0</span></div>'
            f'<svg class="met-linea" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none" role="img"'
            f' aria-label="{escape(nombre)} {POR_CADA[cada]}">{guias}{"".join(barras)}{gris}</svg>{zonas}</div>'
            f'<div class="ejex met-eje">{eje_fechas(fechas, cada)}</div>')


def dona(partes: list[tuple[str, int]], centro: str, debajo: str) -> str:
    """Dona con leyenda; los colores salen de .met-dona .c0..c3."""
    suma = sum(v for _, v in partes) or 1
    r, vuelta, avance, arcos = 40, 2 * 3.14159 * 40, 0.0, []
    for i, (nombre, v) in enumerate(partes):
        largo = v / suma * vuelta
        if largo:
            arcos.append(f'<circle class="c{i}" r="{r}" cx="50" cy="50" stroke-dasharray="{largo:.2f} {vuelta:.2f}"'
                         f' stroke-dashoffset="{-avance:.2f}"/>')
        avance += largo
    leyenda = "".join(f'<li><i class="c{i}"></i>{escape(nombre)}<b>{cifra(v)}</b></li>'
                      for i, (nombre, v) in enumerate(partes))
    return (f'<div class="met-dona"><div class="aro"><svg viewBox="0 0 100 100" role="img"'
            f' aria-label="{escape(debajo)}"><circle class="fondo" r="{r}" cx="50" cy="50"/>{"".join(arcos)}</svg>'
            f'<div class="centro"><b>{centro}</b><small>{escape(debajo)}</small></div></div>'
            f'<ul>{leyenda}</ul></div>')


def tarjeta(titulo: str, cuerpo: str, numero: str = "", clase: str = "") -> str:
    return (f'<section class="met-c {clase}"><div class="met-cab"><span>{escape(titulo)}</span>'
            f'<b>{numero}</b></div>{cuerpo}</section>')


def edades(d: dict[str, int]) -> str:
    from .resultados import barras
    etiquetas = [e for e in EDADES if e in d] or sorted(d)
    valores = [d.get(e, 0) for e in etiquetas]
    return barras(valores, etiquetas, [f"{e} años: {cifra(v)}" for e, v in zip(etiquetas, valores)],
                  descripcion="Seguidores por edad")


def mejores_horas(horas: dict[str, float], n: int = TOP_HORAS) -> list[int]:
    """Las n horas (0-23) con más seguidores conectados, de mayor a menor."""
    return [int(h) for h, _ in sorted(horas.items(), key=lambda x: (-float(x[1]), int(x[0])))[:n] if float(horas[h])]


def horas_html(horas: dict[str, float], clase: str = "t6") -> str:
    """La tarjeta de a qué horas está conectado su público: barras por hora y las mejores en palabras."""
    from .resultados import barras
    valores = [round(float(horas.get(str(h), 0))) for h in range(24)]
    mejores = mejores_horas(horas)
    if not mejores:
        return ""
    lista = ", ".join(f"{h:02d}:00" for h in mejores[:-1]) + (f" y {mejores[-1]:02d}:00" if len(mejores) > 1
                                                                 else f"{mejores[0]:02d}:00")
    return tarjeta("Cuándo está tu público", barras(
        valores, [f"{h:02d}" if h % 6 == 0 else "" for h in range(24)],
        [f"{h:02d}:00: {cifra(v)} seguidores conectados" for h, v in enumerate(valores)],
        ["mejor" if h in mejores else "" for h in range(24)], descripcion="Seguidores conectados por hora")
        + f'<p class="ayuda chica">Más seguidores conectados a las {lista}. Las campañas con horario'
        ' "Cuando está tu público" publican a esas horas.</p>', clase=clase)


def ciudades(p: dict, titulo: str = "Ciudades") -> str:
    """Las ciudades de los seguidores (Instagram las da con 100 o más), en Instagram y en Conversaciones."""
    if not p.get("ciudad"):
        cuerpo = '<p class="ayuda">Instagram muestra las ciudades de tus seguidores cuando la cuenta tiene 100 o más.</p>'
    else:
        total = sum(p["ciudad"].values()) or 1
        cuerpo = '<ol class="met-lista">' + "".join(
            f'<li>{escape(ciudad.split(",")[0])}<b>{cifra(n)}</b><small>{n / total:.0%}</small></li>'
            for ciudad, n in sorted(p["ciudad"].items(), key=lambda x: -x[1])[:6]) + "</ol>"
    return tarjeta(titulo, cuerpo, clase="t3")


def comparado(n: float | None, con: str = "vs. periodo anterior") -> str:
    return f"{pct(n)} {con}" if n is not None else "sin periodo anterior"


def comparado_pts(n: float | None, con: str = "vs. periodo anterior") -> str:
    """Una diferencia de tasas, en puntos."""
    if n is None:
        return "sin periodo anterior"
    clase = "sube" if n > 0 else "baja" if n < 0 else ""
    return f'<span class="met-pct {clase}">{"+" if n > 0 else ""}{n:.1f} pts</span> {con}'.replace(".0 pts", " pts")


def interaccion_html(d: dict) -> str:
    """Debajo de la dona: el % de interacción del periodo y qué es, las cuentas que interactuaron al día y, por
    semana o por mes, la tasa de cada bloque."""
    tasa = d.get("interaccion")
    if tasa is None:
        return ""
    cada = d.get("cada", "dia")
    partes = [f"<b>{tasa:.1f}%</b> de interacción: interacciones entre alcance.".replace(".0%", "%")]
    if d.get("cuentas"):
        partes.append(f"{cifra(d['cuentas'])} cuentas interactuaron al día, en promedio.")
    partes.append("Incluye anuncios. " + REFERENCIA)
    bloques_ = ""
    if cada != "dia" and any(v is not None for v in d.get("interaccion_bloques", [])):
        nombre = {"semana": "semana del", "mes": "mes del"}[cada]
        bloques_ = '<ul class="met-lista chica">' + "".join(
            f'<li>{nombre} {f:%d/%m}<b>{v:.1f}%</b></li>'.replace(".0%", "%") if v is not None else f"<li>{nombre} {f:%d/%m}<b>—</b></li>"
            for f, v in zip(d["fechas"], d["interaccion_bloques"])) + "</ul>"
    return f'<p class="ayuda chica">{" ".join(partes)}</p>{bloques_}'


def kpi(etiqueta: str, valor: str, nota: str) -> str:
    return f'<div class="kpi"><span>{etiqueta}</span><b>{valor}</b><small>{nota}</small></div>'


def botones_html(clics: dict[str, int], total: int) -> str:
    """Clics a los botones del perfil por tipo (WhatsApp, llamar, cómo llegar...), como barras cortas."""
    if not clics:
        return f'<p class="ayuda">{cifra(total)} clics al enlace de tu perfil en el periodo.</p>' if total else \
            '<p class="ayuda">Aquí verás cuántas personas tocaron tus botones de contacto: WhatsApp, llamar o cómo llegar.</p>'
    juntos: dict[str, int] = {}
    for k, v in clics.items():
        nombre = BOTONES.get(k, "Otro")
        juntos[nombre] = juntos.get(nombre, 0) + v
    tope = max(juntos.values()) or 1
    filas = "".join(
        f'<li><span class="rt-nombre">{escape(nombre)}</span>'
        f'<span class="rt-barra"><i style="width:{max(n / tope * 100, 2):.0f}%"></i></span><b>{cifra(n)}</b></li>'
        for nombre, n in sorted(juntos.items(), key=lambda x: -x[1]))
    return f'<ul class="res-tipos">{filas}</ul>'


def seccion(d: dict, formato: str = "", enlace_formato=None, picos: dict[date, str] | None = None) -> str:
    """Las tarjetas de Instagram en Resultados: la fila de números contra el periodo anterior, Vistas en
    grande (apiladas por seguidores y filtradas por formato), Alcance y Seguidores nuevos, Interacciones y
    Clics a tu perfil, y Tu público plegado. `picos` da el post que salió cada día, para explicar el pico."""
    if not d["hay"]:
        return ('<div class="met"><section class="met-c ancho"><div class="met-cab"><span>Instagram</span></div>'
                '<p class="ayuda">Empezamos a guardar tus números de Instagram. Mañana verás tus seguidores, '
                'alcance y vistas aquí, y en unas horas tu historial de los últimos 90 días.</p></section></div>')
    fechas, serie, antes = d["fechas"], d["serie"], d["antes"]
    cada, bloque = d.get("cada", "dia"), d.get("bloque", 1)
    inter = d["interacciones"]
    total_inter = sum(inter.values())
    alcance_prom = d["alcance_promedio"]
    numeros_ = '<div class="hoy kpis res-kpis">' + "".join((
        kpi("Seguidores", "—" if d["seguidores"] is None else cifra(d["seguidores"]),
            comparado(d["periodo"], "desde que empezó el periodo")),
        kpi("Vistas", cifra(d["vistas"]), comparado(d["vistas_cambio"])),
        kpi("Alcance al día", cifra(alcance_prom) if alcance_prom is not None else "—", comparado(d["alcance_cambio"])),
        kpi("Interacción", f"{d['interaccion']:.1f}%".replace(".0%", "%") if d.get("interaccion") is not None else "—",
            comparado_pts(d.get("interaccion_cambio"))),
        kpi("Clics a tu perfil", cifra(d["clics"]), comparado(d["clics_cambio"])),
    )) + "</div>"
    # Vistas: por formato si se pidió, apiladas seguidores / otros cuando hay desglose.
    if formato:
        partes = [(por_formato(x, "vistas_tipo", formato), None) for x in serie]
        de_antes = [por_formato(x, "vistas_tipo", formato) for x in antes]
        total_vistas = d["vistas_tipo"].get(formato, 0)
    else:
        partes = [((x["vistas_seg"].get("seguidores", 0), x["vistas_seg"].get("otros", 0) + x["vistas_seg"].get("sin_dato", 0))
                   if x.get("vistas_seg") else (x.get("vistas"), None)) for x in serie]
        de_antes = [x.get("vistas") for x in antes]
        total_vistas = d["vistas"]
    formatos = [k for k in NOMBRES_FORMATO if d["vistas_tipo"].get(k)]
    pastillas = ""
    if formatos and enlace_formato:
        pastillas = ('<div class="cal-sw modo met-formatos">'
                     + f'<a href="{enlace_formato("")}"{" class=on" if not formato else ""}>Todo</a>'
                     + "".join(f'<a href="{enlace_formato(k)}"{" class=on" if k == formato else ""}>{NOMBRES_FORMATO[k]}</a>'
                               for k in formatos) + "</div>")
    seg = d["vistas_seg"]
    otros = seg.get("otros", 0) + seg.get("sin_dato", 0)
    nota_seg = (f'<small>· {otros / (seg.get("seguidores", 0) + otros):.0%} de personas que no te siguen</small>'
                if not formato and seg.get("seguidores", 0) + otros else "")
    totales = [None if (a is None and b is None) else (a or 0) + (b or 0) for a, b in partes]
    pico = ""
    if any(t for t in totales if t):
        i = max(range(len(totales)), key=lambda i: totales[i] or 0)
        from .resultados import DIAS
        if cada == "dia":
            pico = f"Pico el {DIAS[fechas[i].weekday()]} {fechas[i]:%d/%m}: {cifra(totales[i])} vistas"
        else:
            pico = (f"Pico {'la semana' if cada == 'semana' else 'el mes'} del {fechas[i]:%d/%m}"
                    f" al {fechas[i] + timedelta(days=bloque - 1):%d/%m}: {cifra(totales[i])} vistas")
        salieron = [picos[f] for f in (fechas[i] + timedelta(days=k) for k in range(bloque)) if picos and f in picos]
        if salieron:
            pico += (f', el día que salió "{escape(salieron[0][:60])}"' if cada == "dia" else
                     f', cuando salió "{escape(salieron[0][:60])}"' if len(salieron) == 1 else
                     f", cuando salieron {len(salieron)} publicaciones")
        pico = f'<p class="ayuda chica">{pico}.</p>'
    leyenda = ('<div class="leyenda"><span><i class="seg"></i>Seguidores</span><span><i class="otros"></i>No te siguen</span>'
               '<span><i class="antes"></i>Periodo anterior</span></div>'
               if not formato and any(b is not None for _, b in partes) else
               '<div class="leyenda"><span><i class="seg"></i>Vistas</span><span><i class="antes"></i>Periodo anterior</span></div>')
    vistas = tarjeta("Vistas", pastillas + apiladas(partes, fechas, de_antes, "vistas", cada) + leyenda + pico,
                     f"{cifra(total_vistas)} {nota_seg}", "ancho")
    alc_seg = d["alcance_seg"]
    alc_otros = alc_seg.get("otros", 0) + alc_seg.get("sin_dato", 0)
    nota_alc = (f' <small>al día · {alc_otros / (alc_seg.get("seguidores", 0) + alc_otros):.0%} no te siguen</small>'
                if alc_seg.get("seguidores", 0) + alc_otros else " <small>al día</small>")
    alcance = tarjeta("Alcance", linea(d["alcance_dia"], fechas, "cuentas alcanzadas", [x.get("alcance") for x in antes], cada),
                      (cifra(alcance_prom) + nota_alc) if alcance_prom is not None else "", "t6")
    neto = d["nuevos"] - d["perdidos"]
    nuevos = tarjeta("Seguidores nuevos", linea([x.get("nuevos") for x in serie], fechas, "nuevos", [x.get("nuevos") for x in antes], cada)
                     + f'<p class="ayuda chica">{cifra(d["perdidos"])} dejaron de seguirte · neto {"+" if neto > 0 else ""}{cifra(neto)}'
                     + (f' · {comparado(d["nuevos_cambio"])}' if d["nuevos_cambio"] is not None else "") + "</p>",
                     f'+{cifra(d["nuevos"])}', "t6")
    interacciones = tarjeta("Interacciones", dona(
        [("Likes", inter["likes"]), ("Comentarios", inter["comentarios"]),
         ("Compartidos", inter["compartidos"]), ("Guardados", inter["guardados"])]
        + ([("Respuestas a historias", inter["respuestas"])] if inter.get("respuestas") else []),
        cifra(total_inter), "interacciones") + interaccion_html(d), clase="t6")
    botones = tarjeta("Clics a tu perfil", botones_html(d["clics_tipo"], d["clics"]), cifra(d["clics"]), "t6")
    p = d["publico"]
    resumen = []
    if p.get("genero"):
        g = max(p["genero"].items(), key=lambda x: x[1])[0]
        resumen.append(GENEROS.get(g, g).lower())
    if p.get("edad"):
        resumen.append(f'de {max(p["edad"].items(), key=lambda x: x[1])[0]} años')
    if p.get("ciudad"):
        resumen.append(f'en {max(p["ciudad"].items(), key=lambda x: x[1])[0].split(",")[0]}')
    if p.get("edad") or p.get("genero"):
        genero = p.get("genero", {})
        dentro = (tarjeta("Edad", edades(p["edad"]) if p.get("edad") else "", cifra(sum(p.get("edad", {}).values())), "t4")
                  + tarjeta("Género", dona([(GENEROS.get(k, k), genero.get(k, 0)) for k in GENEROS if genero.get(k)],
                                           cifra(sum(genero.values())), "seguidores"), clase="t4")
                  + ciudades(p).replace('class="met-c t3"', 'class="met-c t4"'))
    else:
        dentro = ('<section class="met-c ancho"><p class="ayuda">Instagram muestra edades, género y ciudades de tus '
                  'seguidores cuando la cuenta tiene 100 o más.</p></section>')
    dentro += horas_html(d.get("horas") or {}, "ancho")
    publico = (f'<details class="met-c ancho met-pleg"><summary><div class="met-cab"><span>Tu público</span>'
               f'<b>{cifra(sum(p.get("genero", {}).values())) if p.get("genero") else ""}'
               f' <small>{"· " + " ".join(resumen) if resumen else "edad, género, ciudades y horarios"} ›</small></b></div></summary>'
               f'<div class="met met-dentro">{dentro}</div></details>')
    aviso = ('<p class="ayuda chica met-aviso">Estamos completando tu historial de los últimos 90 días; '
             'en unas horas verás todos los días.</p>' if d.get("rellenando") else "")
    return numeros_ + f'<div class="met">{vistas}{alcance}{nuevos}{interacciones}{botones}{publico}</div>' + aviso
