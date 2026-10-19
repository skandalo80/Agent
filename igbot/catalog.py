"""Catálogo del negocio, cálculo de cotizaciones y PDF.

Los precios salen de negocio/catalogo.yaml y las cuentas se hacen aquí, en
código: Claude solo elige paquete y opciones, nunca escribe un número.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from . import config
from .pdf_marca import Cotizacion


@lru_cache
def load(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or config.CATALOG_PATH).read_text(encoding="utf-8"))


def available() -> bool:
    return Path(config.CATALOG_PATH).exists()


def money(amount: float, currency: str) -> str:
    return f"US${amount:,.0f}" if currency == "USD" else f"${amount:,.2f} {currency}"


@dataclass
class Quote:
    paquete: str
    pago: str  # 'mensual' | 'anual'
    bloques_extra: int
    lineas: list[tuple[str, float]]  # (concepto, importe) del primer pago
    primer_pago: float
    recurrente: float  # lo que se paga cada mes o cada año después
    moneda: str
    desde_segundo_mes: bool = False  # el recurrente se empieza a cobrar en el segundo mes
    iva: str = ""  # " + IVA" si los precios no lo incluyen

    def texto(self) -> str:
        m = lambda x: money(x, self.moneda)  # noqa: E731
        periodo = "al año" if self.pago == "anual" else "al mes"
        out = [f"{c}: {m(v)}" for c, v in self.lineas]
        out.append(f"Primer pago: {m(self.primer_pago)}{self.iva}")
        cuando = "Desde el segundo mes" if self.desde_segundo_mes else "Después"
        out.append(f"{cuando}: {m(self.recurrente)}{self.iva} {periodo}")
        return "\n".join(out)


def _norm(text: str) -> str:
    return "_".join(text.strip().lower().replace("á", "a").replace("-", " ").split())


def quote(paquete: str, pago: str = "mensual", bloques_extra: int = 0) -> Quote:
    """Calcula una cotización. Lanza ValueError si las opciones no existen.

    paquete puede ser la clave ("pro_crm") o el nombre ("Pro CRM")."""
    cat = load()
    key = _norm(paquete)
    key = next((k for k, p in cat["paquetes"].items() if key in (k, _norm(p["nombre"]))), key)
    if key not in cat["paquetes"]:
        raise ValueError(f"No existe el paquete '{paquete}'. Opciones: {', '.join(cat['paquetes'])}.")
    pago = pago.strip().lower()
    if pago not in ("mensual", "anual"):
        raise ValueError("pago debe ser 'mensual' o 'anual'.")
    extra = cat["extras"]["respuestas"]
    if not 0 <= bloques_extra <= extra["max_bloques"]:
        raise ValueError(f"bloques_extra debe estar entre 0 y {extra['max_bloques']}.")

    p = cat["paquetes"][key]
    meses = cat["meses_pagados_en_anual"] if pago == "anual" else 1
    servicio = p["mensualidad"] * meses
    extras = bloques_extra * extra["precio"] * meses
    lineas = [(f"Instalación paquete {p['nombre']} (pago único)", p["instalacion"])]
    gratis = bool(cat.get("primer_mes_gratis"))
    if gratis:
        # El primer mes no se cobra; el servicio (y los extras) se pagan desde el segundo.
        lineas.append((f"Primer mes del paquete {p['nombre']} (gratis)", 0))
    else:
        lineas.append((
            f"Paquete {p['nombre']}, pago anual (12 meses por {meses})"
            if pago == "anual"
            else f"Paquete {p['nombre']}, primera mensualidad",
            servicio,
        ))
        if bloques_extra:
            total_extra = bloques_extra * extra["bloque"]
            lineas.append((f"{total_extra:,} respuestas extra al mes ({bloques_extra} x {extra['bloque']})", extras))
    return Quote(
        paquete=key, pago=pago, bloques_extra=bloques_extra, lineas=lineas,
        primer_pago=sum(v for _, v in lineas), recurrente=servicio + extras, moneda=cat["moneda"],
        desde_segundo_mes=gratis, iva=iva_suffix(cat),
    )


def iva_suffix(cat: dict) -> str:
    return " + IVA" if cat.get("precios_mas_iva") else ""


def prompt_text() -> str:
    """El catálogo en texto, para que el agente sepa qué ofrece (sin hacer cuentas)."""
    cat = load()
    m = lambda x: money(x, cat["moneda"]) + iva_suffix(cat)  # noqa: E731
    neg = cat["negocio"]
    out = [f"Negocio: {neg['nombre']} ({neg['instagram']}). {neg['descripcion']}", "", "Paquetes:"]
    for key, p in cat["paquetes"].items():
        resumen = f" ({p['resumen']})" if p.get("resumen") else ""
        out.append(
            f"- {p['nombre']} (clave: {key}){resumen}: instalación {m(p['instalacion'])}, {m(p['mensualidad'])} al mes,"
            f" {p['respuestas_mes']:,} respuestas al mes. Incluye: {'; '.join(p['incluye'])}."
        )
    e = cat["extras"]["respuestas"]
    out += [
        f"- Extra: {e['descripcion']}, {m(e['precio'])} al mes cada uno.",
        f"- Pago anual: se pagan {cat['meses_pagados_en_anual']} mensualidades por 12 meses.",
        *(["- Primer mes gratis: al contratar solo se paga la instalación; la mensualidad empieza el segundo mes."]
          if cat.get("primer_mes_gratis") else []),
        f"- Las cotizaciones valen {cat['vigencia_dias']} días.",
        "Condiciones: " + " ".join(cat["condiciones"]),
    ]
    if giros := cat.get("demo", {}).get("giros"):
        out.append(
            "Demo en vivo (giros de ejemplo y qué contestaría su agente): "
            + "; ".join(f"{giro}: {que}" for giro, que in giros.items()) + "."
        )
    return "\n".join(out)


def pdf(q: Quote, folio: str, cliente: str, empresa: str, email: str, today: date) -> bytes:
    cat = load()
    neg = cat["negocio"]
    p = cat["paquetes"][q.paquete]
    m = lambda x: money(x, q.moneda)  # noqa: E731
    doc = Cotizacion(neg["nombre"], f"{neg['instagram']}  ·  {neg['correo']}", folio, neg.get("color_acento", ""))

    resumen = p.get("resumen", "").strip()
    doc.titulo("Propuesta", f"Paquete {p['nombre']}", resumen[:1].upper() + resumen[1:] + "." if resumen else "")
    vence = today + timedelta(days=cat["vigencia_dias"])
    para = ", ".join(x for x in (cliente, empresa) if x) or "Cliente"
    doc.datos([("Para", "\n".join(x for x in (para, email) if x)),
               ("Fecha", f"{today:%d/%m/%Y}"), ("Válida hasta", f"{vence:%d/%m/%Y}")])

    doc.tabla([(concepto, m(importe)) for concepto, importe in q.lineas])
    periodo = "cada año" if q.pago == "anual" else "cada mes"
    cuando = "Desde el segundo mes" if q.desde_segundo_mes else "Después"
    doc.totales([], ("Primer pago", m(q.primer_pago) + q.iva), f"{cuando}, {m(q.recurrente)}{q.iva} {periodo}")

    extra = q.bloques_extra * cat["extras"]["respuestas"]["bloque"]
    doc.seccion("Qué incluye", p["incluye"] + [f"{p['respuestas_mes'] + extra:,} respuestas del bot al mes"])
    doc.seccion("Condiciones", cat["condiciones"] + [f"Precios en {q.moneda}."], chica=True)
    return doc.bytes()
