"""Contenido > Campañas > Tus campañas: el historial y el reporte en PDF.

Cuando el cliente arma una campaña nueva, la anterior se guarda en el kv "campanas_pasadas"
con lo que se probó en cada post que llegó a tener imagen (tema, formato, hora). Desde
"Tus campañas" marca una o varias y descarga un PDF con sus posts de mayor a menor interacción,
para que un community manager se lo mande a la marca.

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
    return date.fromisoformat(camp["inicio"]) + timedelta(days=7 * camp.get("semanas", 1) - 1)


# --- el historial ---

def resumen_de(camp: dict) -> dict:
    """Lo que hace falta de una campaña para su reporte: solo los posts que tuvieron imagen."""
    return {"id": int(camp.get("creada", 0) * 1000), "nombre": camp.get("nombre", ""), "inicio": camp["inicio"],
            "semanas": camp.get("semanas", 1), "modo": camp.get("modo", ""),
            "posts": [{k: p.get(k) for k in ("post_id", "tipo", "formato", "hora", "titulo", "responde")}
                      for p in camp.get("posts", []) if p.get("post_id")]}


async def historial(c: Cuenta) -> list[dict]:
    return json.loads(await c.store.get("campanas_pasadas") or "[]")


async def archivar(c: Cuenta, camp: dict) -> None:
    """Guarda la campaña que se va (si llegó a tener algún post con imagen)."""
    if not camp.get("posts") or not any(p.get("post_id") for p in camp["posts"]):
        return
    nueva = resumen_de(camp)
    lista = [nueva] + [x for x in await historial(c) if x["id"] != nueva["id"]]
    await c.store.set("campanas_pasadas", json.dumps(lista[:MAX_HISTORIAL], ensure_ascii=False))


async def todas(c: Cuenta, actual: dict) -> list[dict]:
    """La campaña de ahora (si tiene posts) y las anteriores, de la más nueva a la más vieja."""
    pasadas = await historial(c)
    if not actual.get("posts"):
        return pasadas
    hoy = resumen_de(actual)
    return [{**hoy, "actual": True}] + [x for x in pasadas if x["id"] != hoy["id"]]


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


async def pdf(c: Cuenta, camps: list[dict], marca: dict, cuenta: str) -> bytes:
    from .campanas import FORMATOS, HORARIOS, TEMAS, tema_de

    renglones = await filas(c, camps)
    nombres = {"tema": {k: tema_de(k)[0] for k in {*TEMAS, *(f["tema"] for f in renglones)}},
               "formato": {k: v[0] for k, v in FORMATOS.items()},
               "franja": {k: f"{v[0]} ({v[1]})" for k, v in HORARIOS.items()}}
    inicio = min(date.fromisoformat(x["inicio"]) for x in camps)
    fin = max(fin_de(x) for x in camps)
    hoy = tools.now().date()
    doc = Reporte(f"@{cuenta.lstrip('@')}", f"{fecha(inicio)} – {fecha(fin)}", fecha(hoy), marca.get("acento", ""))
    doc.alias_nb_pages()

    logo = Path(c.media_dir) / f"{marca.get('logo', '')}.jpg"
    if marca.get("logo") and logo.is_file():
        with Image.open(logo) as im:
            im.thumbnail((400, 400))
            doc.image(im.convert("RGB"), x=doc.l_margin, y=doc.get_y(), h=16)
        doc.ln(22)
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

    grupos = {k: por(renglones, k) for k in ("tema", "formato", "franja")}
    if con_datos:
        resumen = {"posts_con_resultados": sum(1 for f in renglones if f["maduro"] and f["tasa"] is not None),
                   **{f"tasa_por_{k}": [(nombres[k].get(g, g), v, n) for g, v, n in lista]
                      for k, lista in grupos.items()},
                   "mejores": [(f["titulo"], nombres["tema"].get(f["tema"]), nombres["formato"].get(f["formato"]),
                                f["tasa"], f["datos"]) for f in renglones[:3]]}
        if lista := await conclusiones(c, resumen):
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
