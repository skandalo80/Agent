"""Importar el catálogo de una tienda Shopify a Tu empresa (paso Productos y servicios).

Casi todas las tiendas Shopify publican su catálogo en /products.json sin pedir permisos, así que el
cliente no conecta nada: escribe su sitio en el paso 1, pulsa "Traer mis productos de Shopify", elige
cuáles quiere y se agregan a su catálogo (kv "productos") con nombre, precio, opciones, enlace y hasta
dos fotos. Todo corre en segundo plano (marco.trabajo_largo) y con los mismos cuidados de sitio.py.
"""
import json
import logging
import re
import time
from html import unescape
from urllib.parse import urlsplit

import httpx

from . import contenido, sitio
from .cuenta import Cuenta

log = logging.getLogger("igbot.shopify")

CLAVE = "shopify_lista"  # lo que se leyó de la tienda, mientras el cliente elige
POR_PAGINA, MAX_PAGINAS = 250, 2  # hasta 500 productos para elegir
MAX_JSON = 8_000_000
FOTOS_POR_PRODUCTO = 2
MAX_FOTO = 10_000_000


def origen(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _texto(html: str, n: int) -> str:
    sin = re.sub(r"<(script|style)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    sin = re.sub(r"<br\s*/?>|</p>|</li>", "\n", sin, flags=re.I)
    sin = unescape(re.sub(r"<[^>]+>", " ", sin))
    lineas = (re.sub(r"[ \t\xa0]+", " ", x).strip() for x in sin.split("\n"))
    return "\n".join(x for x in lineas if x)[:n]


def _precio(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _numero(v: float) -> str:
    return f"{v:,.2f}".replace(".00", "")


def producto(base: str, p: dict) -> dict | None:
    """Lo que nos sirve de un producto de /products.json (None si no tiene nombre)."""
    if not isinstance(p, dict) or not str(p.get("title", "")).strip():
        return None
    variantes = [v for v in p.get("variants") or [] if isinstance(v, dict)]
    precios = [x for x in (_precio(v.get("price")) for v in variantes) if x is not None]
    opciones = [str(v.get("title", "")).strip() for v in variantes]
    opciones = [x for x in opciones if x and x.lower() != "default title"]
    fotos = [i.get("src") for i in p.get("images") or [] if isinstance(i, dict) and
             str(i.get("src", "")).startswith(("https://", "//"))]
    handle = re.sub(r"[^a-z0-9\-_]", "", str(p.get("handle", "")).lower())
    return {
        "sid": str(p.get("id", ""))[:30] or handle,
        "nombre": str(p["title"]).strip()[:80],
        "descripcion": _texto(p.get("body_html", ""), 800),
        "precio": _numero(min(precios)) if precios else "",
        "desde": len(set(precios)) > 1,
        "opciones": opciones[:20],
        "fotos": [("https:" + f if f.startswith("//") else f) for f in fotos][:FOTOS_POR_PRODUCTO],
        "url": f"{base}/products/{handle}" if handle else "",
        "disponible": any(v.get("available", True) for v in variantes) if variantes else True,
    }


async def leer(url: str) -> list[dict]:
    """Los productos de la tienda; ErrorSitio si no es Shopify o no se pudo leer."""
    base = origen(url)
    out = []
    async with sitio.cliente() as http:
        for pagina in range(1, MAX_PAGINAS + 1):
            try:
                _, texto, _ = await sitio.bajar(http, f"{base}/products.json?limit={POR_PAGINA}&page={pagina}",
                                                tipos=("application/json",), max_bytes=MAX_JSON)
                datos = json.loads(texto)
            except sitio.ErrorSitio:
                if pagina == 1:
                    raise sitio.ErrorSitio("Tu sitio no parece una tienda Shopify, o no deja leer su catálogo.") from None
                break
            except (ValueError, httpx.HTTPError):
                if pagina == 1:
                    raise sitio.ErrorSitio("No pudimos leer el catálogo de tu tienda. Intenta de nuevo en un rato.") from None
                break
            lista = datos.get("products") if isinstance(datos, dict) else None
            if not isinstance(lista, list):
                if pagina == 1:
                    raise sitio.ErrorSitio("Tu sitio no parece una tienda Shopify, o no deja leer su catálogo.")
                break
            out += [x for x in (producto(base, p) for p in lista) if x]
            if len(lista) < POR_PAGINA:
                break
    if not out:
        raise sitio.ErrorSitio("Tu tienda no tiene productos publicados.")
    return out


async def moneda(url: str, monedas: list[str]) -> str:
    """La moneda de la tienda (/meta.json) si es una que manejamos; si no, la primera."""
    try:
        async with sitio.cliente() as http:
            _, texto, _ = await sitio.bajar(http, f"{origen(url)}/meta.json", tipos=("application/json",))
        m = str(json.loads(texto).get("currency", "")).upper()
        return m if m in monedas else monedas[0]
    except Exception:
        return monedas[0]


async def lista(c: Cuenta) -> dict:
    """{"url", "fecha", "productos", "moneda"} o {"error"}; {} si no hay."""
    return json.loads(await c.store.get(CLAVE) or "{}")


async def borrar(c: Cuenta) -> None:
    await c.store.set(CLAVE, "")


async def preparar(c: Cuenta, url: str, monedas: list[str]) -> None:
    """Lee la tienda y deja la lista para que el cliente elija (o el error, para la página)."""
    try:
        if await sitio.quedan(c) <= 0:
            raise sitio.ErrorSitio(f"Ya leíste tu sitio {sitio.USOS_POR_DIA} veces hoy. Intenta de nuevo mañana.")
        prods = await leer(url)
        await sitio._contar(c)
        datos = {"url": origen(url), "fecha": int(time.time()), "productos": prods,
                 "moneda": await moneda(url, monedas)}
        log.info("Tienda Shopify de %s leída: %d productos", c.slug or "la principal", len(prods))
    except sitio.ErrorSitio as e:
        datos = {"url": url, "fecha": int(time.time()), "error": str(e)}
    except Exception:
        log.exception("No se pudo leer la tienda Shopify de %s", c.slug or "la principal")
        datos = {"url": url, "fecha": int(time.time()),
                 "error": "No pudimos leer tu tienda esta vez. Intenta de nuevo en un momento."}
    await c.store.set(CLAVE, json.dumps(datos, ensure_ascii=False))


class _Foto:
    """Lo que espera contenido.guardar_fotos (como una foto subida)."""

    def __init__(self, data: bytes):
        self.data = data

    async def read(self, n: int = -1) -> bytes:
        return self.data if n < 0 else self.data[:n]


async def fotos(c: Cuenta, urls: list[str]) -> list[str]:
    """Baja las fotos del producto (más chicas, del CDN de Shopify) y las guarda como las subidas."""
    bajadas = []
    async with sitio.cliente() as http:
        for u in urls[:FOTOS_POR_PRODUCTO]:
            u = u + ("&" if "?" in u else "?") + "width=1200"
            try:
                _, data, _, _ = await sitio.bajar_bytes(http, u, tipos=("image/",), max_bytes=MAX_FOTO)
                bajadas.append(_Foto(data))
            except (sitio.ErrorSitio, httpx.HTTPError):
                continue
    tokens, _ = await contenido.guardar_fotos(c, bajadas, FOTOS_POR_PRODUCTO)
    return tokens
