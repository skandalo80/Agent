"""Métricas diarias de Instagram: se guardan una vez al día y se ven en Resultados.

Cada día, desde las HORA, el bucle de fondo de app.py (tick) guarda por cuenta:
- Los seguidores, seguidos y publicaciones de ese momento (Instagram solo da el número
  de hoy: si no se guarda, el historial se pierde).
- Los insights de los días anteriores (alcance, vistas, cuentas que interactuaron,
  likes, comentarios, compartidos, guardados, clics al enlace y seguidores ganados y
  perdidos). Se piden los que falten de los últimos DIAS_ATRAS y se vuelven a pedir los
  dos últimos, que Instagram todavía ajusta.
- Una vez por semana, el público: edades, género y ciudades (Instagram solo lo da a
  cuentas con 100 seguidores o más).

Son unas 5 llamadas al día por cuenta (15 la primera vez) y una fila al día en
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

from . import tools
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.metricas")

HORA = 6
DIAS_ATRAS = 7
CLAVE_INTENTO = "metricas_intento"
CLAVE_PUBLICO = "metricas_publico"

# nombre en Instagram -> nombre guardado
METRICAS = {
    "reach": "alcance", "views": "vistas", "accounts_engaged": "cuentas", "total_interactions": "interacciones",
    "likes": "likes", "comments": "comentarios", "shares": "compartidos", "saves": "guardados",
    "profile_links_taps": "clics",
}
PERFIL = {"followers_count": "seguidores", "follows_count": "siguiendo", "media_count": "publicaciones"}
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
    rango = {"period": "day", "metric_type": "total_value", "since": desde, "until": desde + 86400}
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
    return valores


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


async def guardar(c: Cuenta, hoy: date) -> None:
    store = c.store
    await store.guardar_metricas(hoy.isoformat(), {
        PERFIL[k]: v for k, v in (await c.instagram.numeros_cuenta()).items()})
    guardados = await store.metricas((hoy - timedelta(days=DIAS_ATRAS)).isoformat())
    for atras in range(DIAS_ATRAS, 0, -1):
        dia = hoy - timedelta(days=atras)
        if atras <= 2 or "alcance" not in guardados.get(dia.isoformat(), {}):
            if valores := await insights_del_dia(c, dia):
                await store.guardar_metricas(dia.isoformat(), valores)
    semana = f"{hoy.isocalendar()[0]}-{hoy.isocalendar()[1]}"
    anterior = json.loads(await store.get(CLAVE_PUBLICO) or "{}")
    if anterior.get("semana") != semana:
        await store.set(CLAVE_PUBLICO, json.dumps({"semana": semana, **await publico(c)}))


async def tick(c: Cuenta) -> None:
    """Lo llama el bucle de fondo de app.py cada pocos minutos: una vez al día, desde las HORA."""
    if not c.access_token:
        return
    with usar(c):
        ahora = tools.now()
        hoy = ahora.date()
        if ahora.hour < HORA or await c.store.get(CLAVE_INTENTO) == hoy.isoformat():
            return
        await c.store.set(CLAVE_INTENTO, hoy.isoformat())  # antes de llamar: un intento al día
        await guardar(c, hoy)
        log.info("Métricas del %s guardadas para %s", hoy, c.slug or "la principal")


# --- números para Resultados ---

def cambio(actual: int | None, antes: int | None) -> float | None:
    if actual is None or not antes:
        return None
    return (actual - antes) / antes * 100


def seguidores_en(dias: dict[str, dict], dia: date) -> int | None:
    """Seguidores guardados ese día, o el día guardado más cercano antes de él."""
    previos = [d for d in dias if d <= dia.isoformat() and "seguidores" in dias[d]]
    return dias[max(previos)]["seguidores"] if previos else None


async def numeros(c: Cuenta, rango_dias: int) -> dict:
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    dias = await c.store.metricas((hoy - timedelta(days=max(rango_dias, 30) + 1)).isoformat())
    fechas = [hoy - timedelta(days=i) for i in range(rango_dias, 0, -1)]  # sin hoy: aún no termina
    serie = [dias.get(f.isoformat(), {}) for f in fechas]
    con_seguidores = [d for d in dias if "seguidores" in dias[d]]
    actual = dias[max(con_seguidores)]["seguidores"] if con_seguidores else None
    suma = lambda k: sum(d.get(k, 0) for d in serie)  # noqa: E731
    return {
        "fechas": fechas, "serie": serie, "hay": bool(dias),
        "seguidores": actual,
        "semana": cambio(actual, seguidores_en(dias, hoy - timedelta(days=7))),
        "mes": cambio(actual, seguidores_en(dias, hoy - timedelta(days=30))),
        "desde": min(con_seguidores) if con_seguidores else None,
        "nuevos": suma("nuevos"), "perdidos": suma("perdidos"),
        "vistas": suma("vistas"), "clics": suma("clics"),
        "interacciones": {k: suma(k) for k in ("likes", "comentarios", "compartidos", "guardados")},
        "alcance_dia": [d.get("alcance") for d in serie],
        "publico": json.loads(await c.store.get(CLAVE_PUBLICO) or "{}"),
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


def linea(valores: list[int | None], fechas: list[date], nombre: str) -> str:
    """Línea por día con su relleno; los días sin dato se saltan. Al pasar el cursor sale el valor."""
    ancho, alto = 10 * len(valores), 100
    hay = [v for v in valores if v is not None]
    if not hay:
        return '<p class="ayuda met-vacio">Aún sin datos de estos días.</p>'
    bajo, tope = min(hay), max(hay)
    if tope == bajo:
        bajo, tope = bajo - 1, tope + 1
    y = lambda v: 8 + (tope - v) / (tope - bajo) * 84  # noqa: E731
    puntos = [(i * 10 + 5, y(v)) for i, v in enumerate(valores) if v is not None]
    camino = suave(puntos)
    relleno = f"{camino}L{puntos[-1][0]:.1f},{alto}L{puntos[0][0]:.1f},{alto}Z"
    guias = "".join(f'<line class="guia" x1="0" x2="{ancho}" y1="{g}" y2="{g}" vector-effect="non-scaling-stroke"/>'
                    for g in (8, 50, 92))
    zonas = "".join(
        f'<rect class="zona" x="{i * 10}" y="0" width="10" height="{alto}"><title>'
        f'{escape(f"{tools.DAYS[f.weekday()]} {f:%d/%m}: " + ("sin dato" if v is None else f"{cifra(v)} {nombre}"))}'
        "</title></rect>"
        for i, (f, v) in enumerate(zip(fechas, valores)))
    cada = 1 if len(fechas) <= 7 else 7
    eje = "".join(f"<span>{f:%d/%m}</span>" if (len(fechas) - 1 - i) % cada == 0 else "<span></span>"
                  for i, f in enumerate(fechas))
    return (f'<div class="met-graf"><div class="met-escala"><span>{cifra(tope)}</span><span>{cifra(bajo)}</span></div>'
            f'<svg class="met-linea" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none" role="img"'
            f' aria-label="{escape(nombre)} por día">{guias}<path class="area" d="{relleno}"/>'
            f'<path class="trazo" d="{camino}" vector-effect="non-scaling-stroke"/>{zonas}</svg></div>'
            f'<div class="ejex met-eje">{eje}</div>')


def dona(partes: list[tuple[str, int]], centro: str, debajo: str) -> str:
    """Dona con leyenda; los colores salen de .met-dona .c0..c3."""
    suma = sum(v for _, v in partes) or 1
    r, vuelta, avance, arcos = 40, 2 * 3.14159 * 40, 0.0, []
    for i, (nombre, v) in enumerate(partes):
        largo = v / suma * vuelta
        if largo:
            arcos.append(f'<circle class="c{i}" r="{r}" cx="50" cy="50" stroke-dasharray="{largo:.2f} {vuelta:.2f}"'
                         f' stroke-dashoffset="{-avance:.2f}"><title>{escape(nombre)}: {cifra(v)}</title></circle>')
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


def seccion(d: dict) -> str:
    """Las tarjetas de Instagram en Resultados."""
    if not d["hay"]:
        return ('<div class="met"><section class="met-c ancho"><div class="met-cab"><span>Instagram</span></div>'
                '<p class="ayuda">Empezamos a guardar tus números de Instagram. Mañana verás tus seguidores, '
                'alcance y vistas aquí, y cada día se irá formando tu historial.</p></section></div>')
    fechas, serie = d["fechas"], d["serie"]
    variacion = (f'<div class="met-cambios"><span>{pct(d["semana"]) or "—"} vs. semana pasada</span>'
                 f'<span>{pct(d["mes"]) or "—"} vs. mes pasado</span></div>')
    desde = (f'<p class="ayuda chica">Guardando desde el {date.fromisoformat(d["desde"]):%d/%m/%Y}.</p>'
             if d["desde"] and (d["semana"] is None or d["mes"] is None) else "")
    grande = tarjeta("Seguidores", f'<div class="met-grande">{cifra(d["seguidores"] or 0)}</div>{variacion}{desde}',
                     clase="t4")
    neto = d["nuevos"] - d["perdidos"]
    nuevos = tarjeta("Seguidores nuevos", linea([x.get("nuevos") for x in serie], fechas, "nuevos")
                     + f'<p class="ayuda chica">{cifra(d["perdidos"])} dejaron de seguirte · neto {"+" if neto > 0 else ""}{cifra(neto)}</p>',
                     f'+{cifra(d["nuevos"])}', "t4")
    hay_alcance = [v for v in d["alcance_dia"] if v is not None]
    alcance = tarjeta("Alcance", linea(d["alcance_dia"], fechas, "cuentas alcanzadas"),
                      f'{cifra(sum(hay_alcance) / len(hay_alcance))} <small>al día</small>' if hay_alcance else "", "t4")
    vistas = tarjeta("Vistas", linea([x.get("vistas") for x in serie], fechas, "vistas"), cifra(d["vistas"]), "t6")
    inter = d["interacciones"]
    total_inter = sum(inter.values())
    interacciones = tarjeta("Interacciones", dona(
        [("Likes", inter["likes"]), ("Comentarios", inter["comentarios"]),
         ("Compartidos", inter["compartidos"]), ("Guardados", inter["guardados"])],
        cifra(total_inter), "interacciones")
        + (f'<p class="ayuda chica">{cifra(d["clics"])} clics al enlace de tu perfil.</p>' if d["clics"] else ""),
        clase="t6")
    p = d["publico"]
    if p.get("edad") or p.get("genero"):
        genero = p.get("genero", {})
        publico = (tarjeta("Edad", edades(p["edad"]) if p.get("edad") else "", cifra(sum(p.get("edad", {}).values())), "t6")
                   + tarjeta("Género", dona([(GENEROS.get(k, k), genero.get(k, 0)) for k in GENEROS if genero.get(k)],
                                            cifra(sum(genero.values())), "seguidores"), clase="t3")
                   + tarjeta("Ciudades", '<ol class="met-lista">' + "".join(
                       f"<li>{escape(ciudad.split(',')[0])}<b>{cifra(n)}</b></li>"
                       for ciudad, n in sorted(p.get("ciudad", {}).items(), key=lambda x: -x[1])[:6]) + "</ol>",
                       clase="t3"))
    else:
        publico = tarjeta("Tu público", '<p class="ayuda">Instagram muestra edades, género y ciudades de tus '
                                        'seguidores cuando la cuenta tiene 100 o más.</p>', clase="ancho")
    return f'<div class="met">{grande}{nuevos}{alcance}{vistas}{interacciones}{publico}</div>'
