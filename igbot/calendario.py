"""Contenido › Calendario: los posts por mes, semana o día, con el color de su estado
(borrador, programado, publicado), y la misma selección como lista.

Todo sale del servidor: cada vista, flecha y filtro es un enlace o un formulario GET
(el panel no usa fetch). Solo se carga el rango que se ve."""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import urlencode

from . import campanas, contenido, panel, tools
from .admin import PANEL
from .comentarios import shortcode
from .cuenta import Cuenta, usar

log = logging.getLogger(__name__)

BASE = "/panel/contenido"
VISTAS = {"mes": "Mes", "semana": "Semana", "dia": "Día"}
# Estado en el calendario: (nombre, clase de color). "Falló" casi nunca sale, pero no se esconde.
ESTADOS = {"borrador": ("Borrador", "e-borrador"), "programado": ("Programado", "e-programado"),
           "publicado": ("Publicado", "e-publicado"), "fallo": ("No salió", "e-fallo")}
DE_POST = {"borrador": "borrador", "programada": "programado", "publicando": "programado",
           "publicada": "publicado", "fallida": "fallo"}
POR_CELDA = 3  # tarjetas por día en el mes; las demás, "+N más" (lleva al día)
HORAS = (8, 20)  # la semana y el día van de 8:00 a 20:00, o más si hay posts fuera
PAGINAS_INSTAGRAM = 5  # lo subido desde la app: hasta 50 posts hacia atrás
DIAS_LARGOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


@dataclass
class Item:
    cuando: datetime | None  # None: borrador sin fecha (solo sale en la lista)
    titulo: str
    sub: str  # la campaña, "Desde Instagram" o "Post suelto"
    estado: str  # una llave de ESTADOS
    href: str
    imagen: str = ""
    externo: bool = False  # abre Instagram
    ig_id: str = ""
    tipo: str = "post"  # o "historia" (campanas.MEDIOS)
    mover: str = ""  # para arrastrarlo: "p:<id del post>" o "c:<id de la campaña>:<k>"; vacío si ya no se mueve
    texto: str = ""  # el caption, para la descarga
    foto: str = ""  # el token de su imagen en el servidor (lo de la app de Instagram no lo tiene)
    idea: str = ""  # lo que mostrará la imagen, si aún no tiene


def rango(vista: str, fecha: date) -> tuple[date, date]:
    """Los días que se ven (el mes completa sus semanas de lunes a domingo)."""
    if vista == "dia":
        return fecha, fecha
    if vista == "semana":
        lunes = fecha - timedelta(days=fecha.weekday())
        return lunes, lunes + timedelta(days=6)
    primero = fecha.replace(day=1)
    ultimo = campanas.otro_mes(primero, 1) - timedelta(days=1)
    return primero - timedelta(days=primero.weekday()), ultimo + timedelta(days=6 - ultimo.weekday())


def mover(vista: str, fecha: date, n: int) -> date:
    if vista == "dia":
        return fecha + timedelta(days=n)
    if vista == "semana":
        return fecha + timedelta(days=7 * n)
    return campanas.otro_mes(fecha.replace(day=1), n)


def titulo_rango(vista: str, fecha: date) -> str:
    if vista == "dia":
        return f"{DIAS_LARGOS[fecha.weekday()].capitalize()} {fecha.day} de {MESES[fecha.month - 1]}"
    if vista == "semana":
        ini, fin = rango(vista, fecha)
        if ini.month == fin.month:
            return f"{ini.day} al {fin.day} de {MESES[fin.month - 1]} de {fin.year}"
        return f"{ini.day} de {MESES[ini.month - 1]} al {fin.day} de {MESES[fin.month - 1]} de {fin.year}"
    return f"{MESES[fecha.month - 1].capitalize()} de {fecha.year}"


async def de_instagram(c: Cuenta, desde: float, hasta: float, del_panel: set[str]) -> list[Item]:
    """Lo publicado desde la app en el rango (lo del panel ya viene de la base). Pide páginas
    de 10 hasta pasar el inicio del rango, como mucho PAGINAS_INSTAGRAM."""
    items = []
    try:
        for hoja in range(1, PAGINAS_INSTAGRAM + 1):
            posts, ts = await contenido.hoja_instagram(c, hoja), hasta
            for m in posts:
                try:
                    ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                except (KeyError, ValueError):
                    continue
                if not desde <= ts < hasta or shortcode(m.get("permalink", "")) in del_panel:
                    continue
                titulo = contenido.recortar((m.get("caption") or "").strip().split("\n")[0], 50) or "Post de Instagram"
                items.append(Item(datetime.fromtimestamp(ts, tools.tz()), titulo, "Desde Instagram", "publicado",
                                  m.get("permalink", ""), m.get("thumbnail_url") or m.get("media_url") or "",
                                  externo=True, ig_id=m.get("id", ""), texto=m.get("caption") or ""))
            if len(posts) < contenido.POR_HOJA or ts < desde:  # ya no hay más, o ya pasamos el inicio
                break
    except Exception as e:  # sin Instagram el calendario sale igual, con lo del panel
        log.warning("No se pudieron leer los posts de Instagram de %s: %s", c.slug, e)
    return items


async def items_de(c: Cuenta, ini: date, fin: date, con_sin_fecha: bool = False) -> list[Item]:
    """Todo lo del rango: posts del panel, propuestas de campaña que aún no tienen imagen y lo
    publicado desde la app."""
    camps = await campanas.todas_las(c)
    with usar(c):
        zona = tools.tz()
        desde = campanas.momento(ini.isoformat(), "00:00")
        hasta = campanas.momento((fin + timedelta(days=1)).isoformat(), "00:00")
    de_camp = {p["post_id"]: (x, p) for x in camps for p in x.get("posts", []) if p.get("post_id")}
    posts = await c.store.posts(tuple(DE_POST), due_before=hasta - 1, user_id=PANEL, desde=desde)
    if con_sin_fecha:
        posts += [p for p in await c.store.posts(("borrador",), user_id=PANEL) if not p["publish_at"]]
    items = []
    for post in posts:
        p = await contenido.pieza(c.store, post["id"])
        media = await c.store.media(post["media_ids"][0]) if post["media_ids"] else None
        camp, de_la_campana = de_camp.get(post["id"], (None, {}))
        publicado = post["status"] == "publicada" and post["permalink"]
        items.append(item := Item(
            datetime.fromtimestamp(post["publish_at"], zona) if post["publish_at"] else None,
            # El título de la campaña también en foto sola, collage e historias, que no lo llevan en la imagen.
            de_la_campana.get("titulo") or p.get("titulo") or contenido.recortar(post["caption"], 50) or "Sin título",
            camp["nombre"] if camp else "Post suelto", DE_POST[post["status"]],
            post["permalink"] if publicado else f"{BASE}/post/{post['id']}",
            contenido.url_imagen(c, media[0]) if media else "", externo=bool(publicado), tipo=post.get("tipo") or "post",
            texto=post["caption"], foto=media[0] if media else "", idea=de_la_campana.get("idea", "")))
        if post["status"] in contenido.EDITABLES and post["publish_at"]:
            item.mover = f"p:{post['id']}"
    for camp in camps:  # propuestas sin imagen: borradores que llevan a su campaña
        for p in camp.get("posts", []):
            if not p.get("post_id") and ini.isoformat() <= p["dia"] <= fin.isoformat():
                items.append(Item(datetime.fromisoformat(f"{p['dia']}T{p['hora']}").replace(tzinfo=zona),
                                  p.get("titulo") or "Sin título", camp["nombre"], "borrador",
                                  f"{campanas.url_de(camp)}?post={p['k']}#detalle", tipo=campanas.medio_de(p),
                                  mover=f"c:{camp['id']}:{p['k']}", texto=p.get("caption", ""), idea=p.get("idea", "")))
    del_panel = {shortcode(post["permalink"]) for post in posts if post["permalink"]}
    with usar(c):
        items += await de_instagram(c, desde, hasta, del_panel)
    items.sort(key=lambda x: (x.cuando is None, x.cuando or datetime.min.replace(tzinfo=zona), x.titulo))
    return items


def filtrar(items: list[Item], q: str, estado: str, campana: str) -> list[Item]:
    q = q.strip().lower()
    return [x for x in items
            if (not estado or x.estado == estado or (estado == "programado" and x.estado == "fallo"))
            and (not campana or x.sub == campana)
            and (not q or q in x.titulo.lower() or q in x.sub.lower())]


# --- el HTML ---

def enlace(estado_: dict, **cambios) -> str:
    datos = {**estado_, **cambios}
    limpio = {k: v for k, v in datos.items() if v and not (k == "vista" and v == "mes") and not (k == "ver" and v == "cal")}
    return BASE + ("?" + urlencode(limpio) if limpio else "") + "#cal"


def medio(x: Item) -> str:
    return campanas.MEDIOS.get(x.tipo, campanas.MEDIOS["post"])[0]


def tarjeta(x: Item) -> str:
    """El título arriba; abajo la hora y si es post o historia; a la izquierda la miniatura, si ya tiene imagen
    (para acordarse de cuál era). La campaña va en el título flotante."""
    nombre, clase = ESTADOS[x.estado]
    hora = f"{x.cuando:%H:%M}" if x.cuando else "Sin fecha"
    destino = ' target="_blank" rel="noopener"' if x.externo else ""
    mini = (f'<img src="{escape(x.imagen)}" alt="" loading="lazy" draggable="false" referrerpolicy="no-referrer">'
            if x.imagen else "")
    arrastre = f' draggable="true" data-mover="{escape(x.mover)}"' if x.mover else ""
    return (f'<a class="ev {clase}{" con-img" if mini else ""}" href="{escape(x.href)}"{destino}{arrastre}'
            f' title="{escape(x.titulo)} · {escape(x.sub)} · {nombre}">{mini}'
            f'<b>{escape(x.titulo)}</b><small>{hora} · <span class="m-{x.tipo}">{medio(x)}</span></small></a>')


def vista_mes(items: list[Item], fecha: date, hoy: date, est: dict) -> str:
    ini, fin = rango("mes", fecha)
    por_dia: dict[date, list[Item]] = {}
    for x in items:
        if x.cuando:
            por_dia.setdefault(x.cuando.date(), []).append(x)
    celdas = [f'<div class="cal-dn">{d}</div>' for d in campanas.DIAS]
    d = ini
    while d <= fin:
        del_dia = por_dia.get(d, [])
        mas = (f'<a class="ev-mas" href="{enlace(est, vista="dia", fecha=d.isoformat())}">'
               f'+{len(del_dia) - POR_CELDA} más</a>' if len(del_dia) > POR_CELDA else "")
        clase = "cal-d" + (" otro" if d.month != fecha.month else "") + (" es-hoy" if d == hoy else "")
        celdas.append(f'<div class="{clase}" data-dia="{d}"><a class="cal-n" href="{enlace(est, vista="dia", fecha=d.isoformat())}"'
                      f' aria-label="{d.day} de {MESES[d.month - 1]}">{d.day}</a>'
                      + "".join(tarjeta(x) for x in del_dia[:POR_CELDA]) + mas + "</div>")
        d += timedelta(days=1)
    return f'<div class="cal-mes">{"".join(celdas)}</div>'


def vista_horas(items: list[Item], dias: list[date], hoy: date, est: dict) -> str:
    """Semana o día: una fila por hora, de 8:00 a 20:00 (o más si hay algo fuera)."""
    con_hora = [x for x in items if x.cuando]
    desde = min([HORAS[0], *(x.cuando.hour for x in con_hora)])
    hasta = max([HORAS[1], *(x.cuando.hour for x in con_hora)])
    cab = '<div class="cal-hc">Hora</div>' + "".join(
        f'<a class="cal-hc dia{" es-hoy" if d == hoy else ""}" href="{enlace(est, vista="dia", fecha=d.isoformat())}">'
        f'{campanas.DIAS[d.weekday()].lower()}<b>{d.day}</b></a>' for d in dias)
    filas = ""
    for h in range(desde, hasta + 1):
        filas += f'<div class="cal-h">{h:02d}:00</div>'
        for d in dias:
            aqui = [x for x in con_hora if x.cuando.date() == d and x.cuando.hour == h]
            filas += (f'<div class="cal-c{" es-hoy" if d == hoy else ""}" data-dia="{d}" data-h="{h}">'
                      f'{"".join(tarjeta(x) for x in aqui)}</div>')
    return (f'<div class="cal-scroll"><div class="cal-horas" style="--dias:{len(dias)}">'
            f"{cab}{filas}</div></div>")


GRUPOS = (("programado", "Próximos posts", "Ya aprobados: salen solos el día y la hora que dicen."),
          ("borrador", "Borradores", "No salen hasta que los apruebes."),
          ("publicado", "Ya publicados", "Lo de tu Instagram, también lo que subiste desde la app."))


def fila(x: Item) -> str:
    nombre, clase = ESTADOS[x.estado]
    imagen = (f'<img src="{escape(x.imagen)}" alt="" referrerpolicy="no-referrer">' if x.imagen
              else '<span class="pieza" style="width:3.6rem"></span>')
    cuando = contenido.cuando(x.cuando.timestamp()) if x.cuando else "Sin fecha"
    accion = (f'<a class="boton secundario chico" href="{escape(x.href)}" target="_blank" rel="noopener">Ver</a>'
              + contenido.boton_insights(x.ig_id) if x.externo
              else f'<a class="boton secundario chico" href="{escape(x.href)}">Editar</a>')
    return (f'<div class="fila cal-fila">{imagen}<div class="datos"><b>{escape(x.titulo)}</b>'
            f'<span>{escape(cuando)} · {medio(x)} · {escape(x.sub)}</span></div>'
            f'<span class="chip {clase}">{nombre}</span>{accion}</div>')


def vista_lista(items: list[Item], est: dict) -> str:
    if not items:
        return '<div class="cal-vacio"><b>Nada en estas fechas.</b><span>Cambia las fechas o los filtros.</span></div>'
    out = ""
    for llave, titulo, ayuda in GRUPOS:
        grupo = [x for x in items if x.estado == llave or (llave == "programado" and x.estado == "fallo")]
        if llave == "publicado":
            grupo.reverse()  # lo más nuevo primero
        if grupo:
            out += (f'<section class="cal-grupo"><h3>{titulo} <span>{len(grupo)}</span></h3>'
                    f'<p class="ayuda">{ayuda}</p>{"".join(fila(x) for x in grupo)}</section>')
    return out


def filtros(est: dict, nombres: list[str], n: int, descarga: str) -> str:
    def opcion(valor, texto, actual):
        return f'<option value="{escape(valor)}"{" selected" if valor == actual else ""}>{escape(texto)}</option>'
    ocultos = "".join(f'<input type="hidden" name="{k}" value="{escape(est[k])}">' for k in ("vista", "fecha", "ver")
                      if est[k])
    return (f'<form class="cal-filtros" method="get" action="{BASE}#cal">{ocultos}'
            f'<input type="search" name="q" value="{escape(est["q"])}" placeholder="Buscar post o campaña…"'
            ' aria-label="Buscar post o campaña">'
            '<select name="campana" aria-label="Campaña">' + opcion("", "Todas las campañas", est["campana"])
            + "".join(opcion(x, x, est["campana"]) for x in nombres) + "</select>"
            '<select name="estado" aria-label="Estado">' + opcion("", "Todos los estados", est["estado"])
            + "".join(opcion(k, ESTADOS[k][0], est["estado"]) for k in ("borrador", "programado", "publicado"))
            + '</select><button class="boton secundario chico cal-aplicar">Filtrar</button>'
            f'<span class="cal-total">{n} post{"" if n == 1 else "s"}</span>'
            f'<a class="boton secundario chico cal-bajar" href="{escape(descarga)}" title="Lo que ves, en PDF">'
            '↓ Descargar</a></form>')


def solo_el_periodo(vista: str, fecha: date) -> tuple[date, date]:
    """Como rango(), pero el mes sin los días de relleno de otros meses (la lista y la descarga)."""
    if vista == "mes":
        return fecha.replace(day=1), campanas.otro_mes(fecha.replace(day=1), 1) - timedelta(days=1)
    return rango(vista, fecha)


def enlace_descarga(est: dict, fecha: date) -> str:
    datos = {"vista": est["vista"], "fecha": fecha.isoformat(), "q": est["q"], "estado": est["estado"],
             "campana": est["campana"]}
    return f"{BASE}/descargar?" + urlencode({k: v for k, v in datos.items() if v})


async def descarga(c: Cuenta, vista: str, fecha: date | None, q: str, estado: str, campana: str) -> tuple[bytes, str]:
    """El periodo que se ve (mes, semana o día, con sus filtros) en PDF, y el nombre del archivo."""
    from . import informe
    vista = vista if vista in VISTAS else "mes"
    with usar(c):
        fecha = fecha or tools.now().date()
    ini, fin = solo_el_periodo(vista, fecha)
    items = filtrar(await items_de(c, ini, fin), q, estado if estado in ESTADOS else "", campana)
    with usar(c):
        pdf = await informe.calendario_pdf(c, titulo_rango(vista, fecha), ini, fin, items, await contenido.marca_de(c),
                                           await panel.usuario(c))
    nombre = f"calendario-{ini.isoformat()}" + (f"-al-{fin.isoformat()}" if fin != ini else "")
    return pdf, nombre


async def seccion(c: Cuenta, vista: str, fecha: date | None, ver: str, q: str, estado: str, campana: str) -> str:
    vista = vista if vista in VISTAS else "mes"
    ver = "lista" if ver == "lista" else "cal"
    estado = estado if estado in ESTADOS else ""
    with usar(c):
        hoy = tools.now().date()
    fecha = fecha or hoy
    est = {"vista": vista, "fecha": "" if fecha == hoy else fecha.isoformat(), "ver": ver, "q": q,
           "estado": estado, "campana": campana}
    ini, fin = solo_el_periodo(vista, fecha) if ver == "lista" else rango(vista, fecha)
    todos = await items_de(c, ini, fin, con_sin_fecha=ver == "lista")
    nombres = sorted({x["nombre"] for x in await campanas.todas_las(c)})
    items = filtrar(todos, q, estado, campana)
    if ver == "lista":
        cuerpo = vista_lista(items, est)
    elif vista == "mes":
        cuerpo = vista_mes(items, fecha, hoy, est)
    else:
        cuerpo = vista_horas(items, [ini + timedelta(days=i) for i in range((fin - ini).days + 1)], hoy, est)
    vistas = "".join(f'<a href="{enlace(est, vista=k)}"{" class=on" if k == vista else ""}>{v}</a>'
                     for k, v in VISTAS.items())
    modos = "".join(f'<a href="{enlace(est, ver=k)}"{" class=on" if k == ver else ""}>{v}</a>'
                    for k, v in (("cal", "Calendario"), ("lista", "Lista")))
    leyenda = "".join(f'<span><i class="{ESTADOS[k][1]}"></i>{ESTADOS[k][0]}</span>'
                      for k in ("borrador", "programado", "publicado"))
    return (f'<div class="tarjeta cal" id="cal" data-volver="{escape(enlace(est))}"><div class="cal-cab">'
            f'<h2>{titulo_rango(vista, fecha)}</h2><div class="cal-nav">'
            f'<a href="{enlace(est, fecha=mover(vista, fecha, -1).isoformat())}" aria-label="Anterior">‹</a>'
            f'<a href="{enlace(est, fecha=mover(vista, fecha, 1).isoformat())}" aria-label="Siguiente">›</a>'
            f'<a class="hoy-b" href="{enlace(est, fecha="")}">Hoy</a></div>'
            f'<div class="cal-sw modo">{modos}</div><div class="cal-sw">{vistas}</div></div>'
            + filtros(est, nombres, len([x for x in items if x.cuando or ver == "lista"]), enlace_descarga(est, fecha))
            + cuerpo
            + f'<div class="cal-leyenda"><span class="t">Estados:</span>{leyenda}'
            '<span class="t">Toca un post para abrirlo, o arrástralo a otro día u hora.</span></div></div>')


# --- arrastrar un post a otro día u hora ---

ERRORES = {  # ?error= al volver: un código y no el texto, para que nadie arme un aviso con su propio mensaje
    "publicado": "Ese post ya se publicó: ya no se puede mover.",
    "pasado": "Elige un día y una hora que no hayan pasado.",
    "lleno": "Ese día ya tiene todos los posts que caben de esa campaña.",
    "largo": f"Una campaña puede durar hasta {campanas.MAX_DIAS // 7} semanas.",
    "no": "No encontramos ese post. Vuelve a intentarlo.",
}


async def mover_item(c: Cuenta, ref: str, dia: str, h: str) -> str:
    """Lo que manda el arrastre: el post pasa a ese día y, en Semana y Día, a esa hora (con sus mismos minutos).
    Si es de una campaña, se mueve en la campaña (que se estira si el día queda fuera). '' o un código de ERRORES."""
    try:
        nuevo_dia = date.fromisoformat(dia)
        hora = int(h) if h else None
    except ValueError:
        return "no"
    if hora is not None and not 0 <= hora <= 23:
        return "no"
    partes = ref.split(":")
    camp = p = post = None
    if partes[0] == "p" and len(partes) == 2 and partes[1].isdigit():
        post = await c.store.post(int(partes[1]))
        if not post or post["user_id"] != PANEL:
            return "no"
        camp, p = next(((x, y) for x in await campanas.todas_las(c) for y in x.get("posts", [])
                        if y.get("post_id") == post["id"]), (None, None))
    elif partes[0] == "c" and len(partes) == 3:
        camp = next((x for x in await campanas.todas_las(c) if str(x["id"]) == partes[1]), None)
        p = campanas.buscar_post(camp, partes[2]) if camp else None
        if not p:
            return "no"
        post = await campanas.estado_post(c, p)
    else:
        return "no"
    if post and post["status"] not in contenido.EDITABLES:
        return "publicado"
    with usar(c):
        zona = tools.tz()
        antes = (datetime.fromisoformat(f"{p['dia']}T{p['hora']}") if p
                 else datetime.fromtimestamp(post["publish_at"], zona).replace(tzinfo=None))
        nueva = datetime.combine(nuevo_dia, antes.time()).replace(hour=antes.hour if hora is None else hora)
        if nueva.replace(tzinfo=zona) <= tools.now():
            return "pasado"
        if not p:
            await c.store.update_post(post["id"], publish_at=nueva.replace(tzinfo=zona).timestamp())
            return ""
        ahi = [x for x in camp["posts"] if x["dia"] == dia and x is not p and campanas.medio_de(x) == campanas.medio_de(p)]
        tope = campanas.MAX_HISTORIAS_DIA if campanas.es_historia(p) else campanas.MAX_POSTS_DIA
        if p["dia"] != dia and len(ahi) >= tope:
            return "lleno"
        if campanas.estirar(camp, dia):
            return "largo"
        p["dia"], p["hora"] = dia, f"{nueva:%H:%M}"
        await campanas.sincronizar(c, p)
        camp["posts"].sort(key=lambda x: (x["dia"], x["hora"]))
        await campanas.guardar(c, camp)
    return ""
