"""Catálogo dentro del chat: tarjetas de productos que se deslizan de lado en el DM.

El asistente tiene la herramienta `mostrar_productos` cuando la cuenta tiene productos en Tu empresa (los suyos o
los que importó de Shopify). Claude solo elige cuáles (por nombre) y, si quiere, hasta 3 respuestas rápidas; el
código busca cada producto en el catálogo y arma las tarjetas con lo que escribió el cliente: foto, nombre,
precio, opciones y si hay (en vivo con su Shopify conectado). Nunca un dato que no esté en la plataforma.

Instagram manda la plantilla genérica: hasta 10 tarjetas por mensaje, cada una con hasta 3 botones.
- "Ver en la tienda": el enlace del producto, con el código del chat (?sk) si es de su Shopify conectado.
- "Lo quiero": un botón de aviso (postback). Llega como el mensaje "Lo quiero: <producto>" y el asistente sigue
  la compra (con su Mercado Pago o Stripe conectado manda el link de pago).
- "Ver más fotos": manda las demás fotos del producto al momento, sin llamar a Claude (con tope por persona).

Las tarjetas se mandan después del texto de la respuesta; si la respuesta se rehace (llegaron más mensajes), las
de la versión descartada se tiran. En el historial queda una línea con los productos que se mostraron.
"""
import asyncio
import json
import logging
import re

from claude_agent_sdk import tool

from . import config, contenido, empresa, marco, tienda_shopify, unavez
from .cuenta import Cuenta
from .tools import ok

log = logging.getLogger("igbot.catalogo_chat")

MAX_TARJETAS = 10  # límite de Instagram por mensaje
MAX_TITULO = 80  # título y subtítulo de cada tarjeta
MAX_RAPIDAS = 3
MAX_RAPIDA = 20  # largo del texto de una respuesta rápida
MAX_FOTOS_EXTRA = 4
FOTOS_POR_HORA = 10  # veces por hora que una persona puede pedir "Ver más fotos"
TOPE_TIENDA_SEG = 8  # lo más que esperan las tarjetas a su Shopify; después salen sin existencias
POCAS = 3  # con esto o menos en existencia se dice "Quedan pocas"
QUIERO, FOTOS = "SK_QUIERO", "SK_FOTOS"
PAYLOAD = re.compile(rf"^({QUIERO}|{FOTOS}):([0-9a-f]{{1,16}})$")
# Lo que el dueño prende o apaga en Configura tu asistente > Reglas y límites (kv "perfil", "catalogo_chat").
# (clave, título, para qué sirve); todas encendidas al empezar. "Ver en la tienda" sigue a la regla "links".
OPCIONES = [
    ("tarjetas", "Muestra tus productos en tarjetas",
     "Manda tus productos de Tu empresa con foto y precio, para deslizar de lado."),
    ("quiero", "Botón Lo quiero", "Al tocarlo, tu asistente sigue la compra y manda el link de pago si lo tienes."),
    ("fotos", "Botón Ver más fotos", "Manda las demás fotos del producto."),
    ("existencias", "Dice si hay", "Con tu Shopify conectado: disponible, quedan pocas o agotado."),
    ("rapidas", "Respuestas rápidas", "Hasta tres botones cortos debajo, como Envíos o Pagar aquí."),
]


def opciones_de(perfil: dict) -> dict[str, bool]:
    guardadas = perfil.get("catalogo_chat") or {}
    out = {k: bool(guardadas.get(k, True)) for k, *_ in OPCIONES}
    out["tienda"] = bool((perfil.get("reglas") or {}).get("links", True))
    return out


async def opciones(c: Cuenta) -> dict[str, bool]:
    return opciones_de(await marco.perfil(c))


# Lo elegido en esta respuesta, por (cuenta, persona): una sola tarea por persona a la vez, así que no se cruzan.
_pendientes: dict[tuple[str, str], dict] = {}


def disponible() -> bool:
    """Las fotos se mandan por su dirección pública: sin PUBLIC_URL, Instagram no las puede bajar."""
    return bool(config.PUBLIC_URL)


def _norma(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto).casefold()).strip()


def buscar(productos: list[dict], nombre: str) -> dict | None:
    """El producto con ese nombre (o el único que lo contiene); None si no hay o es ambiguo."""
    buscado = _norma(nombre)
    if not buscado:
        return None
    if exacto := [p for p in productos if _norma(p.get("nombre", "")) == buscado]:
        return exacto[0]
    parecidos = [p for p in productos if buscado in _norma(p.get("nombre", ""))]
    return parecidos[0] if len(parecidos) == 1 else None


def _fotos(c: Cuenta, p: dict) -> list[str]:
    """Las direcciones públicas de las fotos del producto que existen."""
    return [config.PUBLIC_URL + contenido.url_imagen(c, t) for t in p.get("imagenes", [])
            if contenido.archivo(c, t).is_file()]


def _url(texto: str) -> str:
    texto = str(texto or "").strip()
    return texto if re.match(r"^https?://[^\s<>\"']+$", texto) else ""


def _corta(texto: str, n: int) -> str:
    texto = re.sub(r"\s+", " ", str(texto)).strip()
    return texto if len(texto) <= n else texto[:n - 1].rstrip() + "…"


def rapidas(textos) -> list[str]:
    """Las respuestas rápidas válidas: texto corto, sin repetir, a lo más 3."""
    out: list[str] = []
    for t in textos if isinstance(textos, list) else []:
        t = _corta(t, MAX_RAPIDA)
        if t and t.casefold() not in {x.casefold() for x in out}:
            out.append(t)
    return out[:MAX_RAPIDAS]


# --- existencias en vivo (solo con su Shopify conectado) ---

EXISTENCIAS = """{ %s }"""
UNO = 'p%d: products(first: 1, query: %s) { nodes { title status tracksInventory totalInventory } }'


async def existencias(c: Cuenta, nombres: list[str]) -> dict[str, str]:
    """{nombre: "Disponible" | "Quedan pocas" | "Agotado"} de su Shopify conectado, en una sola consulta.
    {} sin tienda o si la tienda falla: las tarjetas salen igual, sin ese dato."""
    con = await tienda_shopify.conexion(c)
    if not con.get("token") or not nombres:
        return {}
    # Una sola consulta con un alias por producto; el nombre va como cadena de GraphQL (mismas reglas que JSON).
    limpios = [re.sub(r'["\\]', " ", n) for n in nombres]  # sin comillas ni diagonales que rompan la búsqueda
    partes = [UNO % (i, json.dumps(f'title:"{n}"', ensure_ascii=False)) for i, n in enumerate(limpios)]
    try:
        datos = await asyncio.wait_for(tienda_shopify.consultar(con, EXISTENCIAS % " ".join(partes)), TOPE_TIENDA_SEG)
    except Exception:
        log.warning("No se pudieron revisar las existencias para las tarjetas")
        return {}
    out = {}
    for i, n in enumerate(nombres):
        nodos = ((datos.get(f"p{i}") or {}).get("nodes") or [])
        p = next((x for x in nodos if _norma(x.get("title", "")) == _norma(n)), None)
        if not p or p.get("status") not in (None, "ACTIVE"):
            continue
        if not p.get("tracksInventory", True):
            out[n] = "Disponible"
            continue
        hay = p.get("totalInventory")
        out[n] = "Agotado" if hay is not None and hay <= 0 else "Quedan pocas" if hay and hay <= POCAS else "Disponible"
    return out


# --- tarjetas ---

def estado_de(p: dict, en_vivo: str = "") -> str:
    """Si hay: lo de su Shopify en este momento o, si no, lo que se guardó al importar el producto."""
    return en_vivo or ("Agotado" if p.get("disponible") is False else "")


def subtitulo(p: dict, estado: str = "") -> str:
    """Precio · opciones · si hay, solo con lo que tiene el producto."""
    partes = [empresa.precio_texto(p), ", ".join(str(o) for o in (p.get("opciones") or [])[:3]), estado]
    return _corta(" · ".join(x for x in partes if x), MAX_TITULO)


async def tarjeta(c: Cuenta, user_id: str, p: dict, en_vivo: str = "", opc: dict | None = None) -> dict:
    opc = opc or opciones_de({})
    estado = estado_de(p, en_vivo)  # agotado: sin "Lo quiero", aunque no se diga en la tarjeta
    fotos = _fotos(c, p)
    botones = []
    if (url := _url(p.get("url"))) and opc["tienda"]:
        url = await tienda_shopify.etiquetar(c, user_id, url)
        botones.append({"type": "web_url", "url": url, "title": "Ver en la tienda"})
    if opc["quiero"] and estado != "Agotado":
        botones.append({"type": "postback", "title": "Lo quiero", "payload": f"{QUIERO}:{p['id']}"})
    if opc["fotos"] and len(fotos) > 1:
        botones.append({"type": "postback", "title": "Ver más fotos", "payload": f"{FOTOS}:{p['id']}"})
    t = {"title": _corta(p.get("nombre", ""), MAX_TITULO)}
    if sub := subtitulo(p, estado if opc["existencias"] else ""):
        t["subtitle"] = sub
    if fotos:
        t["image_url"] = fotos[0]
    if url and opc["tienda"]:
        t["default_action"] = {"type": "web_url", "url": url}
    if botones:
        t["buttons"] = botones
    return t


async def elegir(c: Cuenta, user_id: str, nombres: list, respuestas: list) -> str:
    """Lo que hace la herramienta: valida los productos contra el catálogo y los deja listos para mandarse
    después del texto. Devuelve lo que ve Claude."""
    productos = [p for p in await empresa.productos(c) if p.get("id")]
    elegidos, faltan = [], []
    for n in (nombres if isinstance(nombres, list) else [])[:MAX_TARJETAS * 2]:
        p = buscar(productos, str(n)[:120])
        if p is None:
            faltan.append(str(n)[:60])
        elif p not in elegidos:
            elegidos.append(p)
    elegidos = elegidos[:MAX_TARJETAS]
    if not elegidos:
        _pendientes.pop((c.slug, user_id), None)
        return ("No encontré esos productos en el catálogo; no se mandan tarjetas. Usa los nombres tal cual"
                " vienen en el catálogo o contesta solo con texto.")
    _pendientes[(c.slug, user_id)] = {"ids": [p["id"] for p in elegidos], "rapidas": rapidas(respuestas)}
    texto = (f"Listo: después de tu mensaje se mandan {len(elegidos)} tarjeta(s) con foto, precio y botones:"
             f" {', '.join(p['nombre'] for p in elegidos)}. En tu texto presenta las opciones en una línea;"
             " no repitas precios ni enlaces, ya van en las tarjetas.")
    if faltan:
        texto += f" No están en el catálogo (no se mandan): {', '.join(faltan)}."
    return texto


def descartar(c: Cuenta, user_id: str) -> None:
    _pendientes.pop((c.slug, user_id), None)


def tomar(c: Cuenta, user_id: str) -> dict | None:
    return _pendientes.pop((c.slug, user_id), None)


async def enviar(c: Cuenta, instagram, user_id: str, recipient_id: str, elegido: dict) -> str:
    """Manda las tarjetas elegidas (y sus respuestas rápidas). Devuelve la línea para el historial, o ""."""
    productos = {p.get("id"): p for p in await empresa.productos(c)}
    prods = [productos[i] for i in elegido.get("ids", []) if i in productos][:MAX_TARJETAS]
    if not prods:
        return ""
    opc = await opciones(c)
    estados = await existencias(c, [p["nombre"] for p in prods]) if opc["existencias"] else {}
    tarjetas = [await tarjeta(c, user_id, p, estados.get(p["nombre"], ""), opc) for p in prods]
    await instagram.send_cards(recipient_id, tarjetas, (elegido.get("rapidas") or []) if opc["rapidas"] else [])
    log.info("Tarjetas de %d producto(s) a %s", len(tarjetas), user_id)
    return "[Tarjetas: " + ", ".join(p["nombre"] for p in prods) + "]"


def herramientas(c: Cuenta, user_id: str) -> list:
    """La herramienta `mostrar_productos` para el asistente de una cuenta con productos en Tu empresa."""
    @tool(
        "mostrar_productos",
        "Manda tarjetas de productos del catálogo que se deslizan de lado, con su foto, precio y botones (Ver en"
        " la tienda, Lo quiero, Ver más fotos). Úsala cuando la persona pregunte qué tienen, pida opciones,"
        " fotos o precios de productos, o cuando recomiendes alguno. Hasta 10, con el nombre tal cual viene en el"
        " catálogo. Las tarjetas van después de tu texto: escribe una línea que las presente. `respuestas` son"
        " hasta 3 botones cortos (máximo 20 letras) con lo que probablemente pregunte después.",
        {"type": "object", "properties": {
            "productos": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TARJETAS},
            "respuestas": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RAPIDAS}},
         "required": ["productos"]},
    )
    async def mostrar_productos(args):
        return ok(await elegir(c, user_id, args.get("productos") or [], args.get("respuestas") or []))

    return [mostrar_productos]


async def con_productos(c: Cuenta) -> bool:
    """Si el asistente tiene la herramienta: hay productos, dirección pública y el dueño no apagó las tarjetas."""
    return (disponible() and (await opciones(c))["tarjetas"]
            and any(p.get("id") for p in await empresa.productos(c)))


# --- botones de las tarjetas ---

def de_boton(payload: str) -> tuple[str, str] | None:
    """(acción, id del producto) de un botón de las tarjetas; None si es otro botón (p. ej. de inicio)."""
    m = PAYLOAD.match(str(payload or ""))
    return (m.group(1), m.group(2)) if m else None


async def texto_quiero(c: Cuenta, id_: str) -> str:
    """Lo que entra a la conversación al tocar "Lo quiero"."""
    p = await empresa.producto(c, id_)
    return f"Lo quiero: {p['nombre']}" if p else "Lo quiero"


async def mas_fotos(c: Cuenta, instagram, user_id: str, recipient_id: str, id_: str) -> str:
    """Manda las demás fotos del producto (sin Claude). Devuelve la línea para el historial, o "" si no se mandó
    nada (no existe, no tiene más fotos o ya pasó el tope)."""
    p = await empresa.producto(c, id_)
    fotos = _fotos(c, p)[1:1 + MAX_FOTOS_EXTRA] if p else []
    if not fotos or not (await opciones(c))["fotos"] or not await unavez.cabe(c.store, f"fotos_chat:{user_id}", FOTOS_POR_HORA, config.HORA):
        return ""
    for url in fotos:
        await instagram.send_image(recipient_id, url)
    return f"[Fotos de {p['nombre']}: {len(fotos)}]"


async def texto_fotos(c: Cuenta, id_: str) -> str:
    """Lo que entra a la conversación al tocar "Ver más fotos" cuando no se pudieron mandar solas."""
    p = await empresa.producto(c, id_)
    return f"Ver más fotos: {p['nombre']}" if p else "Ver más fotos"
