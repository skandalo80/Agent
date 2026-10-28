"""Contenido > Campañas > Pasadas: el historial y el reporte en PDF.

Cuando ya no caben en la lista de campañas, las más viejas se guardan en el kv "campanas_pasadas"
con lo que se probó en cada post que llegó a tener imagen (tema, formato, hora, y las fotos de sus
collages, que otro collage puede volver a usar). Desde "Tus campañas" marca una o varias y descarga
un PDF con sus posts de mayor a menor interacción, para que un community manager se lo mande a la marca.

Los números se leen de Instagram al momento de abrir el reporte (con un caché corto para no
pedirlos de nuevo en cada descarga). Cada post se mide con la tasa de interacción por alcance,
la que usan Metricool, Hootsuite y Later para los posts propios, sin pesos inventados:

    (likes + comentarios + guardados + compartidos) / alcance × 100

Para decir qué funcionó solo cuentan los posts con 3 días o más publicados: uno de ayer
sigue juntando interacciones.
"""
import asyncio
import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

from . import config, tools
from .comentarios import shortcode
from .cuenta import Cuenta
from .pdf_marca import GRIS, LINEA, NEGRO, Cotizacion

log = logging.getLogger("igbot.informe")

ACCIONES = ("likes", "comments", "saved", "shares")
MADURO = 3 * 86400  # publicado hace al menos esto para contar en "qué funcionó"
MAX_HISTORIAL = 30
MAX_REPORTE = 6  # campañas por PDF
CACHE_SEG = 3600
PAGINAS_BUSQUEDA = 4  # páginas de 50 de "me/media" para hallar posts viejos sin id guardado
_insights: dict[tuple[str, str], tuple[float, dict | None]] = {}

CONCLUSIONES = """Eres analista de redes sociales. Lees los resultados de unas campañas de Instagram y escribes 3 conclusiones cortas para el dueño del negocio, en español y de tú.
- Usa solo los números de <datos> (la tasa es la tasa de interacción por alcance, en %); no inventes cifras ni causas que no se vean ahí.
- Cada conclusión compara algo concreto (un tema, un formato o un horario contra otro) y dice qué conviene hacer.
- Si hay menos de 12 posts con resultados, una de las conclusiones dice que todavía es una señal, no una certeza.
- Máximo 25 palabras cada una. Sin emojis.
Responde solo un JSON: {"conclusiones": ["...", "...", "..."]}"""


def tasa(d: dict | None) -> float | None:
    """Tasa de interacción por alcance, en %."""
    if not d or not d.get("reach"):
        return None
    return round(interacciones(d) / d["reach"] * 100, 1)


def interacciones(d: dict | None) -> int:
    return sum(d.get(k, 0) for k in ACCIONES) if d else 0


def franja(hora: str) -> str:
    h = int(hora[:2]) if hora[:2].isdigit() else 20
    return "manana" if h < 12 else "mediodia" if h < 17 else "noche"


def fin_de(camp: dict) -> date:
    """El último día: el que eligió al editarla (o al estirarla arrastrando un post), o el de su duración."""
    if camp.get("fin"):
        return date.fromisoformat(camp["fin"])
    return date.fromisoformat(camp["inicio"]) + timedelta(days=7 * camp.get("semanas", 1) - 1)


# --- el historial ---

def resumen_de(camp: dict) -> dict:
    """Lo que hace falta de una campaña para su reporte: solo los posts que tuvieron imagen."""
    return {"id": camp.get("id") or int(camp.get("creada", 0) * 1000), "nombre": camp.get("nombre", ""),
            "inicio": camp["inicio"], "fin": fin_de(camp).isoformat(), "semanas": camp.get("semanas", 1),
            "modo": camp.get("modo", ""), "objetivo": str(camp.get("objetivo") or "").split(":")[0][:60],
            "posts": [{k: p.get(k) for k in ("post_id", "tipo", "formato", "hora", "titulo", "responde", "collage")}
                      for p in camp.get("posts", []) if p.get("post_id")],
            **({"brief": camp["brief"]} if camp.get("brief") else {})}  # el brief se queda para descargarlo


async def historial(c: Cuenta) -> list[dict]:
    return json.loads(await c.store.get("campanas_pasadas") or "[]")


async def archivar(c: Cuenta, camp: dict) -> None:
    """Guarda en el historial una campaña vieja que ya no cabe en la lista (si llegó a tener algún post con
    imagen)."""
    if not camp.get("posts") or not any(p.get("post_id") for p in camp["posts"]):
        return
    nueva = resumen_de(camp)
    lista = [nueva] + [x for x in await historial(c) if x["id"] != nueva["id"]]
    await c.store.set("campanas_pasadas", json.dumps(lista[:MAX_HISTORIAL], ensure_ascii=False))


async def todas(c: Cuenta, camps: list[dict]) -> list[dict]:
    """Sus campañas (las que tienen posts) y las del historial, de la que empieza más tarde a la más vieja."""
    ahora = [resumen_de(x) for x in camps if x.get("posts")]
    ids = {x["id"] for x in ahora}
    return sorted(ahora + [x for x in await historial(c) if x["id"] not in ids], key=lambda x: x["inicio"],
                  reverse=True)


# --- los números de Instagram ---

async def ids_de_instagram(c: Cuenta, posts: list[dict]) -> dict[int, str]:
    """El id de Instagram de cada post publicado (se guardó al publicar; los viejos se buscan)."""
    ids, faltan = {}, {}
    for post in posts:
        if not post["permalink"]:
            continue
        if m := await c.store.get(f"media_de:{post['permalink']}"):
            ids[post["id"]] = m
        elif code := shortcode(post["permalink"]):
            faltan[code] = post
    after = ""
    for _ in range(PAGINAS_BUSQUEDA if faltan else 0):
        try:
            pagina, after = await c.instagram.media_page(50, after, fields="id,permalink")
        except Exception as e:
            log.warning("No se pudieron buscar los posts de %s: %s", c.slug, e)
            break
        for m in pagina:
            if post := faltan.pop(shortcode(m.get("permalink", "")), None):
                ids[post["id"]] = m["id"]
                await c.store.set(f"media_de:{post['permalink']}", m["id"])
        if not faltan or not after:
            break
    return ids


async def numeros(c: Cuenta, media_id: str) -> dict | None:
    """Los Insights de un post, o None si Instagram no los dio (p. ej. falta el permiso)."""
    clave = (c.slug, media_id)
    if (guardado := _insights.get(clave)) and guardado[0] > time.time() - CACHE_SEG:
        return guardado[1]
    try:
        datos = await c.instagram.media_insights(media_id)
    except Exception as e:
        log.warning("Sin insights de %s para %s: %s", media_id, c.slug, e)
        datos = None
    _insights[clave] = (time.time(), datos)
    return datos


async def filas(c: Cuenta, camps: list[dict]) -> list[dict]:
    """Un renglón por post publicado de esas campañas, con sus números y su tasa de interacción."""
    todos = [(camp, p) for camp in camps for p in camp["posts"]]
    posts = await c.store.posts_by_id([p["post_id"] for _, p in todos])
    publicados = [(camp, p, posts[p["post_id"]]) for camp, p in todos
                  if p["post_id"] in posts and posts[p["post_id"]]["status"] == "publicada"]
    ids = await ids_de_instagram(c, [post for *_, post in publicados])
    turno = asyncio.Semaphore(4)

    async def leer(post):
        if post["id"] not in ids:
            return None
        async with turno:
            return await numeros(c, ids[post["id"]])

    datos = await asyncio.gather(*(leer(post) for *_, post in publicados))
    ahora = time.time()
    salida = []
    for (camp, p, post), d in zip(publicados, datos):
        media = await c.store.media(post["media_ids"][0]) if post["media_ids"] else None
        salida.append({"campana": camp["nombre"], "titulo": p.get("titulo") or "", "tema": p.get("tipo") or "",
                       "formato": p.get("formato") or "", "franja": franja(p.get("hora") or ""),
                       "publish_at": post["publish_at"], "token": media[0] if media else "", "datos": d,
                       "tasa": tasa(d), "maduro": ahora - (post["publish_at"] or ahora) >= MADURO})
    salida.sort(key=lambda f: (f["tasa"] is None, -(f["tasa"] or 0), -(f["publish_at"] or 0)))
    return salida


def por(renglones: list[dict], clave: str) -> list[tuple[str, float, int]]:
    """Tasa de interacción promedio por tema, formato u horario (solo los posts con 3 días o más)."""
    grupos: dict[str, list[float]] = {}
    for f in renglones:
        if f["maduro"] and f["tasa"] is not None:
            grupos.setdefault(f[clave], []).append(f["tasa"])
    return sorted(((k, round(sum(v) / len(v), 1), len(v)) for k, v in grupos.items()), key=lambda x: -x[1])


async def ganadores(c: Cuenta, camps: list[dict]) -> dict:
    """Tasa promedio por tema, formato y horario de todas las campañas (para Repetir lo que funcionó)."""
    renglones = await filas(c, camps)
    return {"posts": sum(1 for f in renglones if f["maduro"] and f["tasa"] is not None),
            **{k: por(renglones, k) for k in ("tema", "formato", "franja")}}


def ya_hecho(camps: list[dict]) -> tuple[list[str], list[str]]:
    """Los títulos que ya salieron en sus campañas y las preguntas de clientes que ya respondieron."""
    titulos, respondidas = [], []
    for x in camps:
        for p in x["posts"]:
            if p.get("titulo") and p["titulo"] not in titulos:
                titulos.append(p["titulo"])
            if p.get("responde") and p["responde"] not in respondidas:
                respondidas.append(p["responde"])
    return titulos, respondidas


def senal(posts: int) -> str:
    return "Señal débil" if posts < 12 else "Señal media" if posts <= 24 else "Señal fuerte"


async def conclusiones(c: Cuenta, datos: dict) -> list[str]:
    """Tres conclusiones de Claude a partir de los números (se guardan: los mismos números, el mismo texto)."""
    from . import agent  # import tardío: agent importa el SDK

    texto = json.dumps(datos, ensure_ascii=False, sort_keys=True)
    clave = "conclusiones:" + hashlib.sha256(texto.encode()).hexdigest()[:16]
    if guardadas := await c.store.get(clave):
        return json.loads(guardadas)
    try:
        respuesta = await agent.run_claude(f"<datos>\n{texto}\n</datos>", CONCLUSIONES, max_turns=1,
                                           model=config.CONTENT_MODEL)
        inicio, fin = respuesta.find("{"), respuesta.rfind("}")
        lista = json.loads(respuesta[inicio:fin + 1]).get("conclusiones", [])
        lista = [str(x).strip()[:220] for x in lista if str(x).strip()][:3]
    except Exception:
        log.exception("Conclusiones del reporte de %s", c.slug)
        return []
    await c.store.set(clave, json.dumps(lista, ensure_ascii=False))
    return lista


# --- el PDF ---

def cifra(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{n:,.1f}" if isinstance(n, float) else f"{n:,}"


def pct(n: float | None) -> str:
    return "—" if n is None else f"{n:.1f}%"


def fecha(d: date) -> str:
    from .campanas import MESES
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def miniatura(foto: Path) -> Image.Image:
    """Chiquita, para que el PDF no pese lo que pesan todas las fotos completas."""
    with Image.open(foto) as im:
        return ImageOps.fit(im.convert("RGB"), (160, 200))


class Reporte(Cotizacion):
    etiqueta = "REPORTE DE CAMPAÑAS"

    def tabla_posts(self, renglones: list[dict], nombres: dict, media_dir: Path, varias: bool) -> None:
        anchos = (13, 58, 17, 15, 15, 16, 17, self.epw - 151)

        def encabezado():
            self.font(7, bold=True, color=GRIS)
            self.set_char_spacing(0.4)
            for ancho, t in zip(anchos, ("", "Post", "Alcance", "Likes", "Coment.", "Guard.", "Compart.", "Interacción")):
                self.cell(ancho, 6, t.upper(), align="L" if t in ("", "Post") else "R")
            self.ln(6)
            self.set_char_spacing(0)
            self.rule(NEGRO, 0.3)

        encabezado()
        for i, f in enumerate(renglones, 1):
            if self.get_y() > self.page_break_trigger - 18:
                self.add_page()
                encabezado()
            y = self.get_y() + 2
            x = self.l_margin
            foto = media_dir / f"{f['token']}.jpg"
            if f["token"] and foto.is_file():
                self.image(miniatura(foto), x=x, y=y, w=10.4, h=13)
            else:
                self.set_fill_color(235, 235, 238)
                self.rect(x, y, 10.4, 13, style="F")
            x += anchos[0]
            self.set_xy(x, y)
            self.font(8.5, bold=True)
            titulo = f"{i}. {f['titulo']}" + ("*" if not f["maduro"] else "")
            self.cell(anchos[1] - 2, 4.4, titulo[:44] + ("…" if len(titulo) > 44 else ""))
            self.set_xy(x, y + 4.6)
            self.font(7.2, color=GRIS)
            cuando = datetime.fromtimestamp(f["publish_at"], tools.tz()) if f["publish_at"] else None
            linea = (f"{cuando.day} {fecha(cuando.date()).split()[1]} · {cuando:%H:%M}" if cuando else "")
            self.cell(anchos[1] - 2, 3.8, linea + (f" · {f['campana']}"[:30] if varias else ""))
            self.set_xy(x, y + 8.6)
            self.cell(anchos[1] - 2, 3.8, f"{nombres['tema'].get(f['tema'], f['tema'])} · "
                                          f"{nombres['formato'].get(f['formato'], f['formato'])}")
            x += anchos[1]
            d = f["datos"] or {}
            valores = [d.get("reach"), d.get("likes"), d.get("comments"), d.get("saved"), d.get("shares")]
            self.font(9)
            for ancho, v in zip(anchos[2:7], valores):
                self.set_xy(x, y + 3.5)
                self.cell(ancho, 5, cifra(v) if f["datos"] else "—", align="R")
                x += ancho
            self.set_xy(x, y + 3.5)
            self.font(10, bold=True, color=NEGRO)
            self.cell(anchos[7], 5, pct(f["tasa"]), align="R")
            self.set_xy(self.l_margin, y + 15)
            self.rule()
        self.ln(3)

    def barras(self, titulo: str, grupos: list[tuple[str, float, int]], nombres: dict) -> None:
        if not grupos:
            return
        if self.get_y() > self.page_break_trigger - 12 - 7 * len(grupos):
            self.add_page()
        self.font(9.5, bold=True, color=NEGRO)
        self.cell(self.epw, 6, titulo, new_x="LMARGIN", new_y="NEXT")
        tope = max(g[1] for g in grupos) or 1
        for i, (k, valor, n) in enumerate(grupos):
            y = self.get_y() + 1
            self.font(8.5)
            self.set_xy(self.l_margin, y)
            self.cell(48, 5, nombres.get(k, k))
            ancho = max(1.5, 92 * valor / tope)
            self.set_fill_color(*(self.acento if i == 0 else LINEA))
            self.rect(self.l_margin + 50, y + 0.8, ancho, 3.6, style="F", round_corners=True, corner_radius=0.8)
            self.set_xy(self.l_margin + 50 + ancho + 2, y)
            self.font(8.5, bold=True)
            self.cell(18, 5, pct(valor))
            self.set_xy(self.l_margin + self.epw - 22, y)
            self.font(7.5, color=GRIS)
            self.cell(22, 5, f"{n} post{'s' if n != 1 else ''}", align="R")
            self.set_xy(self.l_margin, y + 6)
        self.ln(4)


def poner_logo(doc: Cotizacion, c: Cuenta, marca: dict) -> None:
    logo = Path(c.media_dir) / f"{marca.get('logo', '')}.jpg"
    if marca.get("logo") and logo.is_file():
        with Image.open(logo) as im:
            im.thumbnail((400, 400))
            doc.image(im.convert("RGB"), x=doc.l_margin, y=doc.get_y(), h=16)
        doc.ln(22)


class Propuesta(Cotizacion):
    etiqueta = "PROPUESTA DE CAMPAÑA"

    def post(self, n: int, cuando: str, titulo: str, etiquetas: str, texto: str, foto: Path | None, idea: str,
             sin_foto: str = "IMAGEN PENDIENTE") -> None:
        """Un post: la imagen (o el aviso de que falta) a la izquierda y su texto a la derecha."""
        ancho_foto, alto_foto = 30, 37.5
        if self.get_y() > self.page_break_trigger - alto_foto - 6:
            self.add_page()
        y = self.get_y() + 3
        if foto:
            self.image(miniatura(foto), x=self.l_margin, y=y, w=ancho_foto, h=alto_foto)
        else:
            self.set_fill_color(240, 240, 243)
            self.rect(self.l_margin, y, ancho_foto, alto_foto, style="F", round_corners=True, corner_radius=1.5)
            self.set_xy(self.l_margin + 2.5, y + 3)
            self.font(6.8, bold=True, color=GRIS)
            self.multi_cell(ancho_foto - 5, 3.4, sin_foto, new_x="LEFT", new_y="NEXT")
            self.font(6.8, color=GRIS)
            self.multi_cell(ancho_foto - 5, 3.2, idea[:140] + ("…" if len(idea) > 140 else ""), new_x="LEFT",
                            new_y="NEXT")
        x, ancho = self.l_margin + ancho_foto + 6, self.epw - ancho_foto - 6
        self.set_xy(x, y)
        self.font(7.5, bold=True, color=GRIS)
        self.set_char_spacing(0.4)
        self.cell(ancho, 4.5, f"{n}. {cuando}".upper(), new_x="LEFT", new_y="NEXT")
        self.set_char_spacing(0)
        self.font(11.5, bold=True, color=NEGRO)
        self.multi_cell(ancho, 5.6, titulo, new_x="LEFT", new_y="NEXT")
        self.font(8, color=GRIS)
        self.cell(ancho, 5, etiquetas, new_x="LEFT", new_y="NEXT")
        self.set_xy(x, self.get_y() + 0.8)  # ln() regresaría al margen, encima de la foto
        self.font(9)
        self.multi_cell(ancho, 4.6, texto[:600] + ("…" if len(texto) > 600 else ""), new_x="LEFT", new_y="NEXT")
        self.set_xy(self.l_margin, max(self.get_y(), y + alto_foto) + 4)
        self.rule()


async def propuesta(c: Cuenta, camp: dict, marca: dict, cuenta: str) -> bytes:
    """La campaña que se está armando, para mandarla a la marca antes de publicar: qué sale cada día,
    con su imagen si ya la tiene, y su texto."""
    posts = camp.get("posts", [])
    publicados = await c.store.posts_by_id([p["post_id"] for p in posts if p.get("post_id")])
    medias = {k: await c.store.media(post["media_ids"][0]) for k, post in publicados.items() if post["media_ids"]}
    return await asyncio.to_thread(_propuesta, c, camp, marca, cuenta, publicados, medias)


def _propuesta(c: Cuenta, camp: dict, marca: dict, cuenta: str, publicados: dict, medias: dict) -> bytes:
    from .campanas import FORMATOS, MESES, PUBLICAR, fecha_larga, formato_de, tema_de

    inicio, fin = date.fromisoformat(camp["inicio"]), fin_de(camp)
    doc = Propuesta(f"@{cuenta.lstrip('@')}", f"{fecha(inicio)} – {fecha(fin)}", fecha(tools.now().date()),
                    marca.get("acento", ""))
    doc.alias_nb_pages()
    poner_logo(doc, c, marca)
    posts = camp.get("posts", [])
    doc.titulo("Propuesta de campaña", camp["nombre"],
               (camp.get("tema") or "") + ("\n" if camp.get("tema") else "")
               + f"Del {fecha(inicio)} al {fecha(fin)}.")
    temas = {p["tipo"] for p in posts}
    formatos = {formato_de(p) for p in posts}
    doc.datos([("Posts", cifra(len(posts))), ("Publica", PUBLICAR.get(camp.get("publicar"), ("",))[0] or "—"),
               ("Temas", cifra(len(temas))), ("Formatos", cifra(len(formatos)))])
    semana_actual = None
    for n, p in enumerate(sorted(posts, key=lambda p: (p["dia"], p["hora"])), 1):
        dia = date.fromisoformat(p["dia"])
        semana = (dia - inicio).days // 7 + 1
        if semana != semana_actual:
            semana_actual = semana
            lunes = inicio + timedelta(days=7 * (semana - 1))
            hasta = min(lunes + timedelta(days=6), fin)
            if doc.get_y() > doc.page_break_trigger - 60:
                doc.add_page()
            doc.ln(4)
            doc.font(12, bold=True, color=NEGRO)
            doc.cell(doc.epw * .5, 7, f"Semana {semana}")
            doc.font(9, color=GRIS)
            doc.cell(doc.epw * .5, 7, f"{lunes.day} {MESES[lunes.month - 1]} – {hasta.day} {MESES[hasta.month - 1]}",
                     align="R", new_x="LMARGIN", new_y="NEXT")
            doc.rule(NEGRO, 0.3)
        post = publicados.get(p.get("post_id"))
        media = medias.get(p.get("post_id")) if post else None
        foto = Path(c.media_dir) / f"{media[0]}.jpg" if media else None
        etiquetas = f"{tema_de(p['tipo'])[0]} · {FORMATOS[formato_de(p)][0]}" + (
            f" · Responde: {p['responde']}" if p.get("responde") else "")
        doc.post(n, f"{fecha_larga(p['dia'])} · {p['hora']}", p.get("titulo") or "", etiquetas,
                 (post or {}).get("caption") or p.get("caption", ""), foto if foto and foto.is_file() else None,
                 p.get("idea", ""))
    doc.ln(6)
    doc.seccion("¿Te gusta? ¿Cambiamos algo?", [
        "Podemos mover cualquier post a otro día u hora, cambiar su formato o su texto, o pedir otra idea.",
        "Las imágenes que dicen «Imagen pendiente» se generan cuando el calendario quede aprobado."])
    return doc.bytes()


class PlanCalendario(Propuesta):
    etiqueta = "CALENDARIO DE CONTENIDO"


async def calendario_pdf(c: Cuenta, titulo: str, ini: date, fin: date, items: list, marca: dict, cuenta: str) -> bytes:
    """Lo que se ve en Calendario (mes, semana o día, con sus filtros): cada post e historia por día, con su
    imagen, hora, estado y texto. `items` son calendario.Item."""
    return await asyncio.to_thread(_calendario_pdf, c, titulo, ini, fin, items, marca, cuenta)


def _calendario_pdf(c: Cuenta, titulo: str, ini: date, fin: date, items: list, marca: dict, cuenta: str) -> bytes:
    from .calendario import DIAS_LARGOS, ESTADOS
    from .campanas import MEDIOS

    periodo = fecha(ini) if ini == fin else f"{fecha(ini)} – {fecha(fin)}"
    doc = PlanCalendario(f"@{cuenta.lstrip('@')}", periodo, fecha(tools.now().date()), marca.get("acento", ""))
    doc.alias_nb_pages()
    poner_logo(doc, c, marca)
    doc.titulo("Calendario de contenido", titulo)
    con_fecha = [x for x in items if x.cuando]
    doc.datos([("Posts", cifra(sum(x.tipo != "historia" for x in con_fecha))),
               ("Historias", cifra(sum(x.tipo == "historia" for x in con_fecha))),
               ("Programados", cifra(sum(x.estado == "programado" for x in con_fecha))),
               ("Publicados", cifra(sum(x.estado == "publicado" for x in con_fecha)))])
    if not con_fecha:
        doc.seccion("Nada en estas fechas", ["No hay posts ni historias en este periodo."])
    dia_actual = None
    for n, x in enumerate(con_fecha, 1):
        if x.cuando.date() != dia_actual:
            dia_actual = x.cuando.date()
            if doc.get_y() > doc.page_break_trigger - 60:
                doc.add_page()
            doc.ln(4)
            doc.font(12, bold=True, color=NEGRO)
            doc.cell(doc.epw, 7, f"{DIAS_LARGOS[dia_actual.weekday()].capitalize()} {fecha(dia_actual)}",
                     new_x="LMARGIN", new_y="NEXT")
            doc.rule(NEGRO, 0.3)
        foto = Path(c.media_dir) / f"{x.foto}.jpg" if x.foto else None
        etiquetas = " · ".join([MEDIOS.get(x.tipo, MEDIOS["post"])[0], ESTADOS[x.estado][0], x.sub])
        doc.post(n, f"{x.cuando:%H:%M}", x.titulo, etiquetas, x.texto, foto if foto and foto.is_file() else None,
                 "Publicado desde la app de Instagram." if x.externo and not x.foto else x.idea,
                 sin_foto="EN INSTAGRAM" if x.externo else "IMAGEN PENDIENTE")
    return doc.bytes()


class Brief(Cotizacion):
    etiqueta = "BRIEF DE CAMPAÑA"


async def brief_pdf(c: Cuenta, camp: dict, marca: dict, cuenta: str) -> bytes:
    """El brief de la campaña (la idea que eligió y ajustó), para guardarlo o compartirlo con su equipo."""
    return await asyncio.to_thread(_brief_pdf, c, camp, marca, cuenta)


def _brief_pdf(c: Cuenta, camp: dict, marca: dict, cuenta: str) -> bytes:
    from . import brief  # import tardío: brief importa contenido

    b = camp["brief"]
    inicio, fin = date.fromisoformat(camp["inicio"]), fin_de(camp)
    doc = Brief(f"@{cuenta.lstrip('@')}", f"{fecha(inicio)} – {fecha(fin)}", fecha(tools.now().date()),
                marca.get("acento", ""))
    doc.alias_nb_pages()
    poner_logo(doc, c, marca)
    doc.titulo(f"Brief · {camp['nombre']}", b.get("titulo", ""), b.get("resumen", ""))
    objetivo = str(camp.get("objetivo") or "").split(":")[0]
    producto = str(camp.get("producto") or "").split(":")[0].split(". Qué quiere")[0]
    doc.datos([("Objetivo", objetivo or "—"), ("Posts", cifra(camp.get("n_posts") or len(camp.get("posts", [])))),
               ("Fechas", f"{fecha(inicio)} – {fecha(fin)}"), ("Producto", producto or "—")])
    for titulo, clave in (("Insight", "insight"), ("Mensaje clave", "mensaje_clave")):
        if b.get(clave):
            doc.seccion(titulo, [b[clave]])
    if b.get("pilares"):
        doc.seccion("Pilares", [f"{p['nombre']}: {p.get('detalle', '')}".strip(": ") for p in b["pilares"]])
    for clave, titulo, _ in brief.CAMPOS_IDEA[2:]:
        if b.get(clave):
            doc.seccion(titulo, [b[clave]])
    visual = [f"{t}: {b[k]}" for k, t, _ in brief.CAMPOS_VISUAL if b.get(k)]
    if visual:
        doc.seccion("Dirección visual", visual)
    if camp.get("visual") or camp.get("estilo"):
        doc.seccion("Cómo se ve", [x for x in (f"Qué se ve: {camp['visual']}" if camp.get("visual") else "",
                                               camp.get("estilo", "")) if x], chica=True)
    return doc.bytes()


async def pdf(c: Cuenta, camps: list[dict], marca: dict, cuenta: str) -> bytes:
    from .campanas import FORMATOS, HORARIOS, TEMAS, tema_de

    renglones = await filas(c, camps)
    nombres = {"tema": {k: tema_de(k)[0] for k in {*TEMAS, *(f["tema"] for f in renglones)}},
               "formato": {k: v[0] for k, v in FORMATOS.items()},
               "franja": {k: f"{v[0]} ({v[1]})" for k, v in HORARIOS.items()}}
    con_datos = [f for f in renglones if f["datos"]]
    grupos = {k: por(renglones, k) for k in ("tema", "formato", "franja")}
    lista = []
    if con_datos:
        resumen = {"posts_con_resultados": sum(1 for f in renglones if f["maduro"] and f["tasa"] is not None),
                   **{f"tasa_por_{k}": [(nombres[k].get(g, g), v, n) for g, v, n in lista_]
                      for k, lista_ in grupos.items()},
                   "mejores": [(f["titulo"], nombres["tema"].get(f["tema"]), nombres["formato"].get(f["formato"]),
                                f["tasa"], f["datos"]) for f in renglones[:3]]}
        lista = await conclusiones(c, resumen)
    return await asyncio.to_thread(_pdf, c, camps, marca, cuenta, renglones, nombres, grupos, lista)


def _pdf(c: Cuenta, camps: list[dict], marca: dict, cuenta: str, renglones: list[dict], nombres: dict, grupos: dict,
         lista: list[str]) -> bytes:
    inicio = min(date.fromisoformat(x["inicio"]) for x in camps)
    fin = max(fin_de(x) for x in camps)
    hoy = tools.now().date()
    doc = Reporte(f"@{cuenta.lstrip('@')}", f"{fecha(inicio)} – {fecha(fin)}", fecha(hoy), marca.get("acento", ""))
    doc.alias_nb_pages()

    poner_logo(doc, c, marca)
    nombre = camps[0]["nombre"] if len(camps) == 1 else f"{len(camps)} campañas"
    doc.titulo("Resultados", nombre,
               " · ".join(x["nombre"] for x in camps) if len(camps) > 1 else
               f"Del {fecha(inicio)} al {fecha(fin)}.")

    con_datos = [f for f in renglones if f["datos"]]
    tasas = [f["tasa"] for f in renglones if f["tasa"] is not None]
    doc.datos([("Posts publicados", cifra(len(renglones))),
               ("Alcance total", cifra(sum(f["datos"].get("reach", 0) for f in con_datos))),
               ("Interacciones", cifra(sum(interacciones(f["datos"]) for f in con_datos))),
               ("Interacción promedio", pct(round(sum(tasas) / len(tasas), 1)) if tasas else "—")])

    if not renglones:
        doc.seccion("Todavía no hay resultados", ["Estas campañas aún no tienen posts publicados."])
        return doc.bytes()
    if not con_datos:
        doc.seccion("Instagram no nos dio las estadísticas", [
            "Casi siempre es porque la cuenta se conectó antes de que pidiéramos ese permiso. Vuelve a conectarla"
            " en Conectar Instagram y acepta el permiso de estadísticas."])

    if lista:
        doc.seccion("Lo más importante", lista)

    doc.font(11, bold=True, color=NEGRO)
    doc.cell(doc.epw, 7, "Posts de mayor a menor interacción", new_x="LMARGIN", new_y="NEXT")
    doc.ln(1)
    doc.tabla_posts(renglones, nombres, Path(c.media_dir), len(camps) > 1)
    doc.font(7.5, color=GRIS)
    doc.multi_cell(doc.epw, 4, "Interacción: likes, comentarios, guardados y compartidos entre el alcance (las cuentas"
                   " que vieron el post), en %. Es la tasa de interacción por alcance que usan Metricool, Hootsuite y"
                   " Later; compara bien posts que llegaron a distinta cantidad de gente."
                   + (" * Publicado hace menos de 3 días: sigue juntando interacciones y no cuenta en «Qué"
                      " funcionó»." if any(not f["maduro"] for f in renglones) else ""),
                   new_x="LMARGIN", new_y="NEXT")
    doc.ln(6)

    if any(grupos.values()):
        if doc.get_y() > doc.page_break_trigger - 40:
            doc.add_page()
        doc.font(11, bold=True, color=NEGRO)
        doc.cell(doc.epw, 7, "Qué funcionó", new_x="LMARGIN", new_y="NEXT")
        doc.font(8.5, color=GRIS)
        doc.cell(doc.epw, 5, "Tasa de interacción promedio de los posts con 3 días o más publicados.", new_x="LMARGIN",
                 new_y="NEXT")
        doc.ln(3)
        doc.barras("Por tema", grupos["tema"], nombres["tema"])
        doc.barras("Por formato", grupos["formato"], nombres["formato"])
        doc.barras("Por horario", grupos["franja"], nombres["franja"])
    return doc.bytes()


class ReporteResultados(Cotizacion):
    etiqueta = "RESULTADOS"

    def por_dia(self, filas: list[tuple[str, ...]], columnas: tuple[str, ...]) -> None:
        """Tabla de una línea por día: la fecha a la izquierda y los números a la derecha."""
        anchos = [self.epw * 0.34] + [self.epw * 0.66 / (len(columnas) - 1)] * (len(columnas) - 1)

        def encabezado():
            self.font(7.5, bold=True, color=GRIS)
            self.set_char_spacing(0.5)
            for i, (col, ancho) in enumerate(zip(columnas, anchos)):
                self.cell(ancho, 6, col.upper(), align="L" if i == 0 else "R")
            self.set_char_spacing(0)
            self.ln(6)
            self.rule(NEGRO, 0.3)

        encabezado()
        for fila in filas:
            if self.get_y() > self.page_break_trigger - 9:
                self.add_page()
                encabezado()
            self.ln(1.6)
            self.font(9.5)
            for i, (valor, ancho) in enumerate(zip(fila, anchos)):
                self.cell(ancho, 5, valor, align="L" if i == 0 else "R")
            self.ln(6.6)
            self.rule()
        self.ln(8)


async def resultados_pdf(c: Cuenta, titulo: str, d: dict, m: dict, cuenta: str, vista: str = "instagram") -> bytes:
    """Lo que se ve en Resultados para un periodo: en Instagram, sus números, día por día y el público; en
    Conversaciones, los mensajes, los comentarios y las ciudades. `d` sale de resultados.numeros_de y `m` de
    metricas.numeros."""
    from . import contenido

    marca = await contenido.marca_de(c)
    return await asyncio.to_thread(_resultados_pdf, c, titulo, d, m, cuenta, vista, marca)


def _resultados_pdf(c: Cuenta, titulo: str, d: dict, m: dict, cuenta: str, vista: str, marca: dict) -> bytes:
    from . import metricas
    from .resultados import duracion

    def signo(n: float | None) -> str:
        return "-" if n is None else f"{'+' if n > 0 else ''}{n:.1f}%".replace(".0%", "%")

    def top(grupo: dict, nombres: dict | None = None, n: int = 5) -> str:
        total = sum(grupo.values()) or 1
        return ", ".join(f"{(nombres or {}).get(k, k.split(',')[0])} {v / total:.0%}"
                         for k, v in sorted(grupo.items(), key=lambda x: -x[1])[:n])

    doc = ReporteResultados(f"@{cuenta.lstrip('@')}", titulo, fecha(tools.now().date()), marca.get("acento", ""))
    doc.alias_nb_pages()
    poner_logo(doc, c, marca)
    p = m["publico"]
    if vista == "conversaciones":
        doc.titulo("Resultados · Conversaciones", titulo)
        pct_solas = f"{round(d['resueltas'] / d['escribieron'] * 100)}%" if d.get("escribieron") else "-"
        doc.datos([("Conversaciones", cifra(d["personas"])), ("Mensajes recibidos", cifra(d["recibidos"])),
                   ("Resueltas solas", pct_solas), ("Respuesta", duracion(d["respuesta"])),
                   ("Fuera de horario", cifra(d["fuera"]))])
        if "comentarios" in d:
            doc.seccion("De comentario a mensaje", [])
            doc.datos([("Comentarios", cifra(d["comentarios"])), ("DMs por comentario", cifra(d["dms"])),
                       ("Respuestas públicas", cifra(d["publicas"])), ("Spam ocultado", cifra(d["spam"]))])
        if p.get("ciudad"):
            doc.seccion("De dónde es tu público", [f"Ciudades: {top(p['ciudad'])}"])
        return doc.bytes()
    doc.titulo("Resultados · Instagram", titulo)
    inter = m["interacciones"]
    alcance = [v for v in m["alcance_dia"] if v is not None]
    doc.datos([("Seguidores", cifra(m["seguidores"]) if m["seguidores"] is not None else "-"),
               ("vs. semana pasada", signo(m["semana"])), ("vs. mes pasado", signo(m["mes"])),
               ("Nuevos", cifra(m["nuevos"])), ("Dejaron de seguir", cifra(m["perdidos"]))])
    doc.datos([("Alcance al día", cifra(round(sum(alcance) / len(alcance))) if alcance else "-"),
               ("Vistas", cifra(m["vistas"])), ("Interacciones", cifra(sum(inter.values()))),
               ("Likes", cifra(inter["likes"])), ("Clics al enlace", cifra(m["clics"]))])
    dias = [(f, x) for f, x in zip(m["fechas"], m["serie"]) if x]
    if dias:
        doc.por_dia([(fecha(f), cifra(x.get("seguidores")) if "seguidores" in x else "-",
                      f"+{x['nuevos']}" if "nuevos" in x else "-", cifra(x.get("alcance")), cifra(x.get("vistas")))
                     for f, x in dias], ("Día", "Seguidores", "Nuevos", "Alcance", "Vistas"))
    if p.get("edad") or p.get("genero") or p.get("ciudad"):
        doc.seccion("Tu público", [x for x in (
            f"Edad: {top(p['edad'])}" if p.get("edad") else "",
            f"Género: {top(p['genero'], metricas.GENEROS)}" if p.get("genero") else "",
            f"Ciudades: {top(p['ciudad'])}" if p.get("ciudad") else "") if x])
    if "publicadas" in d:
        doc.seccion("Publicaciones", [f"{len(d['publicadas'])} publicadas y {d['programadas']} programadas en el periodo."])
    return doc.bytes()
