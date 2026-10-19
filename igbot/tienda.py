"""Tienda de productos (Nortex): catálogo y existencias de Google Sheets, y cotización de pedidos.

Las hojas se leen con la cuenta de Google del bot y se guardan unos minutos en
memoria. Las cuentas se hacen aquí, en código, con las reglas de reglas.yaml:
Claude solo arma el pedido (SKU, talla, cantidad), nunca escribe un precio.
"""
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from . import config
from .google import Google
from .pdf_marca import Cotizacion

TECNICAS = ("ninguna", "bordado", "dtf")
# Tallas como se dicen en México -> como vienen en la hoja.
TALLAS = {
    "ch": "S", "chica": "S", "chico": "S", "small": "S",
    "m": "M", "med": "M", "mediana": "M", "mediano": "M", "medium": "M",
    "g": "L", "grande": "L", "large": "L",
    "xg": "XL", "extra grande": "XL",
    "unitalla": "Ajustable", "única": "Ajustable", "unica": "Ajustable",
}


@lru_cache
def rules(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or config.STORE_RULES_PATH).read_text(encoding="utf-8"))


def available() -> bool:
    return bool(config.SHEET_CATALOG and config.SHEET_INVENTORY) and Path(config.STORE_RULES_PATH).exists()


def parse_sheet(url: str) -> tuple[str, int | None]:
    """(id, gid) de un link de Google Sheets; también acepta solo el id."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    sheet_id = m.group(1) if m else url.strip()
    g = re.search(r"gid=(\d+)", url)
    return sheet_id, int(g.group(1)) if g else None


def number(text: str) -> float:
    """'$1,145.50' -> 1145.5"""
    clean = re.sub(r"[^\d.]", "", text or "")
    return float(clean) if clean else 0.0


def money(amount: float) -> str:
    return f"${amount:,.2f}"


_cache: dict[str, tuple[float, list[dict]]] = {}


async def _rows(google: Google, url: str) -> list[dict]:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < config.SHEETS_CACHE_SECONDS:
        return hit[1]
    rows = await google.sheet_rows(*parse_sheet(url))
    _cache[url] = (time.time(), rows)
    return rows


async def products(google: Google) -> dict[str, dict]:
    """Productos activos por SKU."""
    rows = await _rows(google, config.SHEET_CATALOG)
    return {
        r["SKU"].upper(): r for r in rows
        if r.get("SKU") and r.get("Active", "Y").strip().upper() != "N"
    }


async def stock(google: Google) -> dict[str, dict[str, int]]:
    """{SKU: {talla: disponibles}}."""
    out: dict[str, dict[str, int]] = {}
    for r in await _rows(google, config.SHEET_INVENTORY):
        if r.get("SKU"):
            out.setdefault(r["SKU"].upper(), {})[r.get("Variant", "")] = int(number(r.get("Available_Qty", "")))
    return out


def tier(qty: int, reglas: dict) -> int:
    """Índice del rango que toca a esa cantidad (-1 si no llega al primero)."""
    idx = -1
    for i, r in enumerate(reglas["rangos"]):
        if qty >= r["desde"]:
            idx = i
    return idx


@dataclass
class Order:
    lineas: list[tuple[str, float]] = field(default_factory=list)  # (concepto, importe)
    avisos: list[str] = field(default_factory=list)
    subtotal: float = 0.0
    iva: float = 0.0
    total: float = 0.0
    entrega: str = ""

    def texto(self) -> str:
        out = [f"{c}: {money(v)}" for c, v in self.lineas]
        out += [
            f"Subtotal: {money(self.subtotal)}",
            f"IVA: {money(self.iva)}",
            f"Total: {money(self.total)} MXN",
            f"Entrega: {self.entrega}",
        ]
        return "\n".join(out + self.avisos)


def quote(items: list[dict], logos: int, catalogo: dict, existencias: dict, reglas: dict) -> Order:
    """Cotiza un pedido. items: [{sku, talla, cantidad, personalizacion, ubicaciones_extra}].

    Lanza ValueError con un mensaje para la persona si algo no se puede.
    """
    if not items:
        raise ValueError("El pedido está vacío.")
    lines = []
    for it in items:
        sku = str(it.get("sku", "")).strip().upper()
        p = catalogo.get(sku)
        if not p:
            raise ValueError(f"No existe el producto {sku or '(sin SKU)'}; revisa el catálogo con ver_productos.")
        qty = int(it.get("cantidad", 0))
        if qty <= 0:
            raise ValueError(f"La cantidad de {sku} debe ser mayor que cero.")
        tallas = existencias.get(sku, {})
        talla = str(it.get("talla", "")).strip()
        buscada = TALLAS.get(talla.lower(), talla).lower()
        match = next((t for t in tallas if t.lower() == buscada), None)
        if match is None:
            raise ValueError(f"{p['Name_ES']} no se maneja en talla '{talla}'. Tallas: {', '.join(tallas) or 'ninguna'}.")
        tec = str(it.get("personalizacion", "ninguna")).strip().lower() or "ninguna"
        if tec not in TECNICAS:
            raise ValueError(f"personalizacion debe ser una de: {', '.join(TECNICAS)}.")
        if tec != "ninguna" and p.get("Customizable", "Y").strip().upper() == "N":
            raise ValueError(f"{p['Name_ES']} no se puede personalizar.")
        extra = max(0, int(it.get("ubicaciones_extra", 0))) if tec != "ninguna" else 0
        lines.append((sku, p, match, qty, tec, extra, tallas[match]))

    # Piezas por tipo de prenda: de ahí sale el rango de cada línea.
    por_tipo: dict[str, int] = {}
    moq: dict[str, int] = {}
    for sku, p, _, qty, *_ in lines:
        tipo = p.get("ProductType", sku)
        por_tipo[tipo] = por_tipo.get(tipo, 0) + qty
        moq[tipo] = max(moq.get(tipo, 0), int(number(p.get("MOQ", "0"))))
    for tipo, qty in por_tipo.items():
        minimo = max(moq[tipo], reglas["rangos"][0]["desde"])
        if qty < minimo:
            raise ValueError(f"El mínimo de {tipo} es {minimo} piezas (sumando colores y tallas); van {qty}.")
        if qty >= reglas["precio_especial_desde"]:
            raise ValueError(
                f"{qty} piezas de {tipo} lleva precio especial: usa pasar_a_humano para que lo cotice una persona."
            )

    order = Order()
    personalizadas = 0
    tecnicas_usadas = set()
    falta = False
    for sku, p, talla, qty, tec, extra, disponibles in lines:
        i = tier(por_tipo[p.get("ProductType", sku)], reglas)
        precio = number(p[reglas["rangos"][i]["columna"]])
        nombre = f"{p['Name_ES']} talla {talla}"
        order.lineas.append((f"{qty} x {nombre} a {money(precio)}", qty * precio))
        if tec != "ninguna":
            per = reglas["personalizacion"][tec]
            order.lineas.append((f"{per['nombre']}: {qty} x {money(per['precios'][i])}", qty * per["precios"][i]))
            personalizadas += qty
            tecnicas_usadas.add(tec)
        if extra:
            ub = reglas["ubicacion_extra"]
            order.lineas.append(
                (f"{ub['nombre']}: {qty} x {extra} x {money(ub['precios'][i])}", qty * extra * ub["precios"][i])
            )
        if qty > disponibles:
            falta = True
            order.avisos.append(f"Aviso: de {nombre} hay {disponibles} disponibles y se piden {qty}.")

    for tec in sorted(tecnicas_usadas):
        cargo = reglas["personalizacion"][tec].get("cargo_unico")
        if cargo:
            n = max(1, int(logos))
            gratis = personalizadas >= cargo.get("gratis_desde", float("inf"))
            importe = 0 if gratis else n * cargo["precio"]
            texto = f"{cargo['nombre']} ({n} logo{'s' if n > 1 else ''})" + (" gratis por volumen" if gratis else "")
            order.lineas.append((texto, importe))

    order.subtotal = round(sum(v for _, v in order.lineas), 2)
    order.iva = round(order.subtotal * reglas["iva"], 2)
    order.total = round(order.subtotal + order.iva, 2)
    e = reglas["entrega"]
    order.entrega = e["sin_existencias"] if falta else e["personalizado"] if personalizadas else e["con_existencias"]
    return order


def catalog_text(catalogo: dict, reglas: dict, tipo: str = "") -> str:
    """Productos (opcionalmente de un tipo) con sus precios por rango."""
    tipo = tipo.strip().lower().rstrip("s")
    elegidos = [
        (sku, p) for sku, p in catalogo.items()
        if tipo in f"{p.get('ProductType', '')} {p.get('Name_ES', '')}".lower()
    ] or list(catalogo.items())  # si el filtro no encuentra nada, se manda todo
    out = []
    for sku, p in elegidos:
        precios = ", ".join(f"{r['nombre']}: {p.get(r['columna'], '?')}" for r in reglas["rangos"])
        pers = "personalizable" if p.get("Customizable", "Y").upper() != "N" else "sin personalizar"
        out.append(
            f"- {sku}: {p['Name_ES']} ({p.get('ProductType', '')}). {p.get('Material', '')}. {precios}."
            f" Mínimo {p.get('MOQ', '?')} pzas. {pers}."
        )
    return "\n".join(out)


def prompt_text() -> str:
    """Reglas de precios para el agente (los productos los consulta con herramientas)."""
    r = rules()
    neg = r["negocio"]
    rangos = ", ".join(x["nombre"] for x in r["rangos"])
    pers = "; ".join(
        f"{v['nombre']}: " + " / ".join(money(p) for p in v["precios"])
        + (f" por pieza, más {v['cargo_unico']['nombre'].lower()} de {money(v['cargo_unico']['precio'])}"
           f" (gratis desde {v['cargo_unico']['gratis_desde']} piezas)" if v.get("cargo_unico") else " por pieza")
        for v in r["personalizacion"].values()
    )
    ub = r["ubicacion_extra"]
    return "\n".join([
        f"Negocio: {neg['nombre']} ({neg['instagram']}), {neg['ciudad']}. Precios en {r['moneda']} más IVA.",
        f"Precio por volumen: rangos {rangos}. Las piezas se cuentan por tipo de prenda, sumando colores y"
        f" tallas. Desde {r['precio_especial_desde']} piezas de un tipo, el precio lo da una persona.",
        f"Personalización (precio por rango, en el mismo orden): {pers}. {ub['nombre']}: "
        + " / ".join(money(p) for p in ub["precios"]) + " por pieza.",
        f"Entrega: con existencias {r['entrega']['con_existencias']}, personalizado {r['entrega']['personalizado']},"
        f" sin existencias {r['entrega']['sin_existencias']}.",
        "Condiciones: " + " ".join(r["condiciones"]),
        "Para productos usa ver_productos, para tallas y existencias ver_existencias, y para cualquier"
        " precio o total usa SIEMPRE cotizar_pedido; nunca hagas cuentas tú.",
    ])


def pdf(order: Order, folio: str, cliente: str, empresa: str, email: str, today: date) -> bytes:
    """Cotización formal del pedido en PDF (lo mismo que texto(), con folio y condiciones)."""
    r = rules()
    neg = r["negocio"]
    doc = Cotizacion(neg["nombre"], f"{neg['instagram']}  ·  {neg['ciudad']}", folio, neg.get("color_acento", ""))

    doc.titulo("Cotización", "Pedido de uniformes", f"Tiempo de entrega: {order.entrega}.")
    vence = today + timedelta(days=r["vigencia_dias"])
    para = ", ".join(x for x in (cliente, empresa) if x) or "Cliente"
    doc.datos([("Para", "\n".join(x for x in (para, email) if x)),
               ("Fecha", f"{today:%d/%m/%Y}"), ("Válida hasta", f"{vence:%d/%m/%Y}")])

    doc.tabla([(concepto, money(importe)) for concepto, importe in order.lineas])
    doc.totales([("Subtotal", money(order.subtotal)), (f"IVA ({r['iva']:.0%})", money(order.iva))],
                (f"Total {r['moneda']}", money(order.total)))

    if order.avisos:
        doc.seccion("Avisos", order.avisos)
    doc.seccion("Condiciones", r["condiciones"], chica=True)
    return doc.bytes()
