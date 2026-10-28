"""Conecta tu Shopify: la tienda del cliente, con permiso, para el inventario y las ventas.

Importar el catálogo (shopify.py) no pide nada: lee el /products.json público. Esto es otra cosa: el
cliente autoriza a la app de Skandar en su tienda (OAuth de Shopify) y con ese permiso:

1. Inventario en el chat: el asistente tiene la herramienta `existencias`, que pregunta a la tienda en
   ese momento qué hay de un producto (variantes, cantidad y sucursales).
2. Enlaces con el código del chat: cada enlace a la tienda que manda el asistente lleva ?sk=<código>
   (etiquetar), así un pedido hecho desde ese enlace se sabe de qué chat vino.
3. Ventas: cada 15 minutos se leen los pedidos nuevos (sincronizar). La página con la que la persona
   entró a la tienda dice de qué chat (sk) y de qué anuncio (utm_content={{ad.id}} en Ads Manager)
   vino; el pedido se guarda en la tabla `ventas` con su monto y sale en Conversaciones.

Permisos: read_products, read_inventory, read_locations y read_orders. No se leen datos del comprador
(nombre, correo, dirección): solo montos, productos y de dónde llegó.

kv "shopify_conexion": {"tienda": "x.myshopify.com", "token", "nombre", "dominios", "fecha",
"pedidos_desde", "leido", "sin_permiso"}. Sin SHOPIFY_API_KEY en .env nada de esto sale en el panel.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import config, panel, sitio
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.tienda_shopify")

router = APIRouter(prefix="/panel/shopify")
webhooks = APIRouter(prefix="/shopify")
cuentas = lambda: []  # noqa: E731 — lo pone app.py: todas las cuentas, para los avisos de Shopify

CLAVE = "shopify_conexion"
SCOPES = "read_products,read_inventory,read_locations,read_orders"
TIENDA = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
STATE_COOKIE = "shopify_state"
STATE_TTL = 900
PARAMETRO = "sk"  # el del código del chat en los enlaces
CADA = 900  # segundos entre lecturas de pedidos
DIAS_ATRAS = 7  # al conectar, los pedidos de la última semana
MAX_PEDIDOS = 100  # por lectura
_transporte: httpx.AsyncBaseTransport | None = None  # las pruebas ponen uno falso


def enabled() -> bool:
    return bool(config.SHOPIFY_API_KEY and config.SHOPIFY_API_SECRET and config.PUBLIC_URL)


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/panel/shopify/callback"


async def conexion(c: Cuenta) -> dict:
    """La tienda conectada ({} si no hay)."""
    return json.loads(await c.store.get(CLAVE) or "{}")


async def guardar(c: Cuenta, datos: dict) -> None:
    await c.store.set(CLAVE, json.dumps(datos, ensure_ascii=False) if datos else "")


# --- conectar (OAuth de Shopify) ---

def _firmar(texto: str) -> str:
    return hmac.new(config.SHOPIFY_API_SECRET.encode(), texto.encode(), hashlib.sha256).hexdigest()


def hmac_valido(params: list[tuple[str, str]]) -> bool:
    """Lo que manda Shopify al volver va firmado: todos los parámetros menos hmac, en orden, con el secreto."""
    recibido = next((v for k, v in params if k == "hmac"), "")
    texto = "&".join(f"{k}={v}" for k, v in sorted((k, v) for k, v in params if k != "hmac"))
    return bool(recibido) and hmac.compare_digest(recibido, _firmar(texto))


def nuevo_state(c: Cuenta) -> str:
    base = f"{c.slug or '-'}.{int(time.time())}.{secrets.token_urlsafe(12)}"
    return f"{base}.{_firmar(base)[:32]}"


def state_valido(state: str, cookie: str | None, c: Cuenta) -> bool:
    partes = state.split(".")
    if len(partes) != 4 or not cookie or not secrets.compare_digest(cookie, state):
        return False
    slug, ts, nonce, firma = partes
    return (slug == (c.slug or "-") and ts.isdigit() and 0 <= time.time() - int(ts) < STATE_TTL
            and secrets.compare_digest(firma, _firmar(f"{slug}.{ts}.{nonce}")[:32]))


_Cliente = httpx.AsyncClient  # el de verdad, aunque las pruebas de otras partes cambien httpx.AsyncClient


def cliente() -> httpx.AsyncClient:
    return _Cliente(timeout=20, transport=_transporte)


async def tienda_de(texto: str) -> str:
    """El dominio x.myshopify.com de lo que escribió el cliente (su .myshopify.com o el dominio de su
    tienda, que dice cuál es en /meta.json). ErrorSitio si no se encuentra."""
    texto = texto.strip().lower()
    host = urlsplit(texto if "://" in texto else f"https://{texto}").hostname or ""
    if TIENDA.match(host):
        return host
    if not host or "." not in host:
        raise sitio.ErrorSitio("Escribe la dirección de tu tienda, por ejemplo mitienda.com o mitienda.myshopify.com.")
    try:
        async with sitio.cliente() as http:
            _, texto_meta, _ = await sitio.bajar(http, f"https://{host}/meta.json", tipos=("application/json",))
        tienda = str(json.loads(texto_meta).get("myshopify_domain", "")).lower()
    except (sitio.ErrorSitio, ValueError, httpx.HTTPError):
        tienda = ""
    if not TIENDA.match(tienda):
        raise sitio.ErrorSitio("No encontramos una tienda Shopify en esa dirección. Prueba con tu dirección"
                               " .myshopify.com (la ves en tu Shopify, en Configuración > Dominios).")
    return tienda


async def cambiar_codigo(tienda: str, code: str) -> tuple[str, str]:
    """(token, permisos) de la tienda a cambio del código de un solo uso."""
    async with cliente() as http:
        r = await http.post(f"https://{tienda}/admin/oauth/access_token", json={
            "client_id": config.SHOPIFY_API_KEY, "client_secret": config.SHOPIFY_API_SECRET, "code": code})
        if r.is_error:
            log.error("Shopify respondió %s al cambiar el código: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        datos = r.json()
        return datos["access_token"], datos.get("scope", "")


class SinPermiso(Exception):
    """La tienda quitó la app o le falta un permiso."""


async def consultar(con: dict, query: str, variables: dict | None = None) -> dict:
    """Una consulta GraphQL a la API de administración de la tienda."""
    async with cliente() as http:
        r = await http.post(
            f"https://{con['tienda']}/admin/api/{config.SHOPIFY_API_VERSION}/graphql.json",
            headers={"X-Shopify-Access-Token": con["token"]}, json={"query": query, "variables": variables or {}})
    if r.status_code in (401, 403):
        raise SinPermiso(r.text[:200])
    r.raise_for_status()
    datos = r.json()
    if datos.get("errors"):
        if "ACCESS_DENIED" in json.dumps(datos["errors"]):
            raise SinPermiso(json.dumps(datos["errors"])[:200])
        raise RuntimeError(f"Shopify: {json.dumps(datos['errors'])[:300]}")
    return datos.get("data") or {}


DATOS_TIENDA = "{ shop { name myshopifyDomain primaryDomain { host } } }"


@router.get("")
async def ver(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    return await pagina(c, request.query_params.get("aviso", ""))


AVISOS = {
    "conectada": "Listo: conectamos tu tienda. Tu asistente ya ve tu inventario y tus ventas salen en tus chats.",
    "cancelada": "No se conectó tu tienda. Puedes intentarlo de nuevo cuando quieras.",
    "fallo": "No pudimos conectar tu tienda esta vez. Intenta de nuevo en un momento.",
    "desconectada": "Desconectamos tu tienda. Para quitar del todo la app, bórrala también en tu Shopify (Apps).",
}


async def pagina(c: Cuenta, aviso: str = "", error: str = ""):
    con = await conexion(c)
    from . import empresa  # empresa importa contenido, que importa el panel
    sitio_web = (await empresa.de(c)).get("sitio_web", "")
    if not enabled():
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu Shopify</h2><p class="ayuda">Muy pronto vas a poder conectar tu'
                  " tienda desde aquí.</p></div>")
    elif con:
        desde = datetime.fromtimestamp(con.get("fecha", 0)).strftime("%d/%m/%Y")
        cuerpo = ('<div class="tarjeta"><h2>Tu tienda Shopify está conectada</h2>'
                  f'<p class="ayuda"><b>{escape(con.get("nombre") or con["tienda"])}</b> · {escape(con["tienda"])}'
                  f" · desde el {desde}</p>"
                  "<ul class=\"lista-shopify\"><li>Tu asistente revisa tu inventario cuando le preguntan si hay algo.</li>"
                  "<li>Los enlaces a tu tienda que manda tu asistente llevan el código de su chat.</li>"
                  "<li>Leemos tus pedidos cada 15 minutos: si vienen de un chat o de un anuncio, salen en"
                  " Conversaciones con su monto.</li></ul>"
                  '<p class="ayuda">Para saber de qué anuncio vino cada pedido, en Ads Manager, en cada anuncio que'
                  ' lleva a tu tienda, escribe en "Parámetros de URL": <code>utm_source=meta&amp;utm_content={{ad.id}}</code></p>'
                  '<form method="post" action="/panel/shopify/desconectar" class="acciones">'
                  '<button class="boton secundario chico">Desconectar tu tienda</button></form></div>')
    else:
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu Shopify</h2>'
                  '<p class="ayuda">Con tu permiso, tu asistente ve tu inventario al momento ("¿queda en talla M?") y'
                  " tus ventas de la tienda salen en el chat de quien compró, con el anuncio que lo trajo. Solo"
                  " leemos: no cambiamos nada de tu tienda ni vemos los datos de tus compradores.</p>"
                  + (f'<p class="aviso">{escape(error)}</p>' if error else "")
                  + '<form method="post" action="/panel/shopify/conectar">'
                  '<label for="tienda">La dirección de tu tienda</label>'
                  '<p class="ayuda">Tu dominio (mitienda.com) o tu dirección .myshopify.com.</p>'
                  f'<input id="tienda" name="tienda" required maxlength="200" value="{escape(sitio_web)}"'
                  ' placeholder="mitienda.com" autocomplete="off">'
                  '<div class="acciones"><button class="boton">Conectar tu Shopify</button></div></form></div>')
    aviso_html = f'<div class="aviso">{escape(AVISOS[aviso])}</div>' if aviso in AVISOS else ""
    return panel.pagina(
        await panel.menu_de(c, "cuenta", sub="conexion")
        + f'<main class="contenido">{panel.encabezado("Tu tienda")}{aviso_html}{cuerpo}</main>')


@router.post("/conectar")
async def conectar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if not enabled():
        return RedirectResponse("/panel/shopify", status_code=303)
    f = await panel.form(request)
    try:
        tienda = await tienda_de(f.get("tienda", "")[:200])
    except sitio.ErrorSitio as e:
        return await pagina(c, error=str(e))
    state = nuevo_state(c)
    url = f"https://{tienda}/admin/oauth/authorize?" + urlencode({
        "client_id": config.SHOPIFY_API_KEY, "scope": SCOPES, "redirect_uri": redirect_uri(), "state": state})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, path="/panel/shopify", secure=True, httponly=True,
                 samesite="lax")
    return r


@router.get("/callback")
async def callback(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    params = list(request.query_params.multi_items())
    p = dict(params)
    tienda = p.get("shop", "").lower()
    if not p.get("code"):
        return _volver("cancelada")
    if not (enabled() and TIENDA.match(tienda) and hmac_valido(params)
            and state_valido(p.get("state", ""), request.cookies.get(STATE_COOKIE), c)):
        log.warning("Vuelta de Shopify con firma o state inválidos (%s)", tienda)
        return _volver("fallo")
    try:
        token, permisos = await cambiar_codigo(tienda, p["code"])
        con = {"tienda": tienda, "token": token, "permisos": permisos, "fecha": int(time.time()),
               "pedidos_desde": time.time() - DIAS_ATRAS * 86400}
        shop = (await consultar(con, DATOS_TIENDA)).get("shop") or {}
    except Exception:
        log.exception("No se pudo conectar la tienda %s", tienda)
        return _volver("fallo")
    dominios = {tienda, str((shop.get("primaryDomain") or {}).get("host", "")).lower()}
    from . import empresa
    if host := urlsplit((await empresa.de(c)).get("sitio_web", "")).hostname:
        dominios.add(host.lower())
    con.update(nombre=str(shop.get("name", ""))[:80], dominios=sorted(d.removeprefix("www.") for d in dominios if d))
    await guardar(c, con)
    log.info("@%s conectó su tienda Shopify %s", await panel.usuario(c), tienda)
    return _volver("conectada")


def _volver(aviso: str) -> RedirectResponse:
    r = RedirectResponse(f"/panel/shopify?aviso={aviso}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/panel/shopify")
    return r


@router.post("/desconectar")
async def desconectar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    await guardar(c, {})
    return RedirectResponse("/panel/shopify?aviso=desconectada", status_code=303)


# --- avisos obligatorios de privacidad de Shopify ---

@webhooks.post("/webhooks/{tema}")
async def aviso(request: Request, tema: str):
    """customers-data-request, customers-redact y shop-redact. No guardamos datos de compradores; al
    borrar la tienda (shop-redact, 48 h después de quitar la app) se olvida su conexión."""
    cuerpo = await request.body()
    firma = request.headers.get("x-shopify-hmac-sha256", "")
    esperada = base64.b64encode(hmac.new(config.SHOPIFY_API_SECRET.encode(), cuerpo, hashlib.sha256).digest()).decode()
    if not enabled() or not hmac.compare_digest(firma, esperada):
        raise HTTPException(status_code=401)
    if tema == "shop-redact":
        tienda = str(json.loads(cuerpo or b"{}").get("shop_domain", "")).lower()
        for c in cuentas():
            with usar(c):
                if (await conexion(c)).get("tienda") == tienda:
                    await guardar(c, {})
                    log.info("Shopify pidió borrar la tienda %s: se olvidó su conexión", tienda)
    return Response(status_code=200)


# --- 1. inventario para el asistente ---

INVENTARIO = """query($q: String!) { products(first: 5, query: $q) { nodes {
  title handle status onlineStoreUrl tracksInventory totalInventory
  variants(first: 40) { nodes { title availableForSale inventoryQuantity
    inventoryItem { tracked inventoryLevels(first: 10) { nodes { location { name }
      quantities(names: ["available"]) { quantity } } } } } } } } }"""


async def existencias(c: Cuenta, buscar: str) -> str:
    """Lo que hay en la tienda de lo que buscan, en texto para el asistente."""
    con = await conexion(c)
    palabras = re.sub(r"[^\w\s-]", " ", buscar).split()[:6]
    if not con or not palabras:
        return "No hay datos de inventario para eso."
    q = " ".join(f"title:{p}*" for p in palabras)  # Shopify solo busca por el principio de cada palabra
    try:
        productos = ((await consultar(con, INVENTARIO, {"q": q})).get("products") or {}).get("nodes") or []
    except SinPermiso:
        return "No se pudo revisar el inventario ahora (la tienda quitó el permiso)."
    lineas = []
    for p in productos:
        if p.get("status") not in (None, "ACTIVE"):
            continue
        lineas.append(f"Producto: {p.get('title')}" + (f" ({p['onlineStoreUrl']})" if p.get("onlineStoreUrl") else ""))
        for v in (p.get("variants") or {}).get("nodes") or []:
            nombre = v.get("title") if v.get("title") != "Default Title" else "único"
            item = v.get("inventoryItem") or {}
            if not item.get("tracked", True) or not p.get("tracksInventory", True):
                lineas.append(f"- {nombre}: disponible (la tienda no lleva conteo)")
                continue
            hay = v.get("inventoryQuantity")
            sucursales = []
            for n in (item.get("inventoryLevels") or {}).get("nodes") or []:
                cant = sum(x.get("quantity") or 0 for x in n.get("quantities") or [])
                sucursales.append(f"{(n.get('location') or {}).get('name', '')}: {cant}")
            estado = "agotado" if not v.get("availableForSale") or (hay is not None and hay <= 0) else f"{hay} disponibles"
            lineas.append(f"- {nombre}: {estado}" + (f" (por sucursal: {', '.join(sucursales)})" if len(sucursales) > 1 else ""))
    if not lineas:
        return f"La tienda no tiene productos que coincidan con «{buscar}»."
    return "\n".join(lineas)[:3000]


def herramientas(c: Cuenta) -> list:
    """La herramienta `existencias` para el asistente de una cuenta con su tienda conectada."""
    from claude_agent_sdk import tool

    from .tools import ok

    @tool("existencias", "Revisa en la tienda Shopify, en este momento, si hay un producto y en qué variantes"
          " (talla, color) y sucursales. Úsala siempre que pregunten si hay, si queda o si está disponible algo."
          " No digas la cantidad exacta: di si hay, si está agotado o, con 3 o menos, que quedan pocas.",
          {"producto": str})
    async def existencias_tool(args):
        return ok(await existencias(c, str(args.get("producto", ""))[:100]))

    return [existencias_tool]


# --- 2. enlaces con el código del chat ---

URL = re.compile(r"https?://[^\s<>()\"']+")


async def etiquetar(c: Cuenta, user_id: str, texto: str) -> str:
    """El mensaje con ?sk=<código del chat> en cada enlace a la tienda conectada."""
    con = await conexion(c)
    dominios = set(con.get("dominios") or [])
    if not dominios or "http" not in texto:
        return texto
    codigo = None

    async def cambiar(url: str) -> str:
        nonlocal codigo
        final = url.rstrip(".,;:!?")
        resto = url[len(final):]
        partes = urlsplit(final)
        host = (partes.hostname or "").removeprefix("www.")
        if host not in dominios:
            return url
        query = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k != PARAMETRO]
        codigo = codigo or await c.store.codigo_chat(user_id)
        return urlunsplit(partes._replace(query=urlencode(query + [(PARAMETRO, codigo)]))) + resto

    piezas, ultimo = [], 0
    for m in URL.finditer(texto):
        piezas += [texto[ultimo:m.start()], await cambiar(m.group(0))]
        ultimo = m.end()
    return "".join(piezas) + texto[ultimo:]


# --- 3. ventas: los pedidos nuevos ---

PEDIDOS = """query($q: String!, $n: Int!) { orders(first: $n, query: $q, sortKey: CREATED_AT) { nodes {
  id name createdAt cancelledAt
  currentTotalPriceSet { shopMoney { amount currencyCode } }
  lineItems(first: 10) { nodes { title quantity } }
  customerJourneySummary { firstVisit { landingPage utmParameters { content } }
    lastVisit { landingPage utmParameters { content } } } } } }"""


def _de_pagina(pagina: str) -> tuple[str, str]:
    """(código del chat, id del anuncio) de la página con la que entró a la tienda."""
    q = dict(parse_qsl(urlsplit(pagina or "").query))
    return q.get(PARAMETRO, "")[:20], re.sub(r"\D", "", q.get("utm_content", ""))[:30]


async def sincronizar(c: Cuenta) -> int:
    """Guarda los pedidos nuevos que vienen de un chat o de un anuncio. Devuelve cuántos guardó."""
    con = await conexion(c)
    if not con or time.time() - con.get("leido", 0) < CADA - 5:
        return 0
    desde = datetime.fromtimestamp(con.get("pedidos_desde", time.time()), timezone.utc)
    try:
        pedidos = ((await consultar(con, PEDIDOS, {"q": f"created_at:>='{desde:%Y-%m-%dT%H:%M:%SZ}'",
                                                   "n": MAX_PEDIDOS})).get("orders") or {}).get("nodes") or []
    except SinPermiso:
        log.warning("La tienda %s ya no da permiso; se deja de leer", con.get("tienda"))
        await guardar(c, {**con, "leido": time.time(), "sin_permiso": True})
        return 0
    guardados, ultimo = 0, con.get("pedidos_desde", 0)
    for p in pedidos:
        creado = datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00")).timestamp()
        ultimo = max(ultimo, creado)
        if p.get("cancelledAt"):
            continue
        viaje = p.get("customerJourneySummary") or {}
        codigo, anuncio = "", ""
        for visita in (viaje.get("lastVisit"), viaje.get("firstVisit")):
            visita = visita or {}
            k, a = _de_pagina(visita.get("landingPage", ""))
            a = a or re.sub(r"\D", "", str((visita.get("utmParameters") or {}).get("content") or ""))[:30]
            codigo, anuncio = codigo or k, anuncio or a
        user_id = await c.store.usuario_de_codigo(codigo) if codigo else ""
        if not anuncio and user_id:
            anuncio = (await c.store.anuncios_de([user_id])).get(user_id, {}).get("ad_id", "")
        if not (user_id or anuncio):
            continue  # ni de un chat ni de un anuncio: no es nuestro
        dinero = (p.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
        productos = [f"{x.get('quantity', 1)} × {x.get('title', '')}" for x in (p.get("lineItems") or {}).get("nodes") or []]
        if await c.store.add_venta("shopify", p["id"], user_id, anuncio, float(dinero.get("amount") or 0),
                                   dinero.get("currencyCode", ""), {"folio": p.get("name", ""), "productos": productos},
                                   creado):
            guardados += 1
    # Lo de hace un minuto se vuelve a pedir la próxima vez (pedidos que aún se estaban creando).
    con.pop("sin_permiso", None)
    await guardar(c, {**con, "leido": time.time(), "pedidos_desde": max(con.get("pedidos_desde", 0), ultimo - 60)})
    if guardados:
        log.info("Tienda %s: %d ventas nuevas de chats o anuncios", con["tienda"], guardados)
    return guardados


async def tick(c: Cuenta) -> None:
    if enabled():
        await sincronizar(c)


def monto(v: float, moneda: str) -> str:
    return f"${v:,.2f}".replace(".00", "") + (f" {moneda}" if moneda else "")
