"""Catálogo del negocio, cálculo de cotizaciones y PDF.

Los precios salen de negocio/catalogo.yaml y las cuentas se hacen aquí, en
código: Claude solo elige paquete y opciones, nunca escribe un número.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from fpdf import FPDF

from . import config


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

    def texto(self) -> str:
        m = lambda x: money(x, self.moneda)  # noqa: E731
        periodo = "al año" if self.pago == "anual" else "al mes"
        out = [f"{c}: {m(v)}" for c, v in self.lineas]
        out.append(f"Primer pago: {m(self.primer_pago)}")
        out.append(f"Después: {m(self.recurrente)} {periodo}")
        return "\n".join(out)


def quote(paquete: str, pago: str = "mensual", bloques_extra: int = 0) -> Quote:
    """Calcula una cotización. Lanza ValueError si las opciones no existen."""
    cat = load()
    key = paquete.strip().lower().replace("á", "a")
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
    lineas = [
        (f"Instalación paquete {p['nombre']} (pago único)", p["instalacion"]),
        (
            f"Paquete {p['nombre']}, pago anual (12 meses por {meses})"
            if pago == "anual"
            else f"Paquete {p['nombre']}, primera mensualidad",
            servicio,
        ),
    ]
    if bloques_extra:
        total_extra = bloques_extra * extra["bloque"]
        lineas.append((f"{total_extra:,} respuestas extra al mes ({bloques_extra} x {extra['bloque']})", extras))
    return Quote(
        paquete=key, pago=pago, bloques_extra=bloques_extra, lineas=lineas,
        primer_pago=sum(v for _, v in lineas), recurrente=servicio + extras, moneda=cat["moneda"],
    )


def prompt_text() -> str:
    """El catálogo en texto, para que el agente sepa qué ofrece (sin hacer cuentas)."""
    cat = load()
    m = lambda x: money(x, cat["moneda"])  # noqa: E731
    neg = cat["negocio"]
    out = [f"Negocio: {neg['nombre']} ({neg['instagram']}). {neg['descripcion']}", "", "Paquetes:"]
    for key, p in cat["paquetes"].items():
        out.append(
            f"- {p['nombre']} (clave: {key}): instalación {m(p['instalacion'])}, {m(p['mensualidad'])} al mes,"
            f" {p['respuestas_mes']:,} respuestas al mes. Incluye: {'; '.join(p['incluye'])}."
        )
    e = cat["extras"]["respuestas"]
    out += [
        f"- Extra: {e['descripcion']}, {m(e['precio'])} al mes cada uno.",
        f"- Pago anual: se pagan {cat['meses_pagados_en_anual']} mensualidades por 12 meses.",
        f"- Las cotizaciones valen {cat['vigencia_dias']} días.",
        "Condiciones: " + " ".join(cat["condiciones"]),
    ]
    if demo := neg.get("demo_instagram"):
        out.append(
            f"Demo: el negocio de demostración {demo} tiene funcionando las funciones avanzadas."
            f" Si alguien quiere verlas, invítalo a escribirle a {demo}."
        )
    else:
        out.append(
            "Demo: aún no hay cuenta de demostración; si quieren ver funciones avanzadas en vivo,"
            " ofrece mostrarlas en la llamada con Skandar."
        )
    return "\n".join(out)


def _latin1(text: str) -> str:
    # Las fuentes base del PDF solo tienen latin-1 (incluye acentos y ñ).
    return text.encode("latin-1", "replace").decode("latin-1")


def pdf(q: Quote, folio: str, cliente: str, empresa: str, email: str, today: date) -> bytes:
    cat = load()
    neg = cat["negocio"]
    p = cat["paquetes"][q.paquete]
    m = lambda x: money(x, q.moneda)  # noqa: E731
    doc = FPDF(format="Letter")
    doc.set_margins(20, 20, 20)
    doc.add_page()
    w = doc.epw
    t = lambda s: _latin1(str(s))  # noqa: E731

    doc.set_font("Helvetica", "B", 18)
    doc.cell(w, 10, t(neg["nombre"]), new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 10)
    doc.set_text_color(90)
    doc.cell(w, 5, t(f"{neg['instagram']}  ·  {neg['correo']}"), new_x="LMARGIN", new_y="NEXT")
    doc.ln(8)

    doc.set_text_color(0)
    doc.set_font("Helvetica", "B", 14)
    doc.cell(w / 2, 8, t(f"Cotización {folio}"))
    doc.set_font("Helvetica", "", 10)
    vence = today + timedelta(days=cat["vigencia_dias"])
    doc.cell(w / 2, 8, t(f"Fecha: {today:%d/%m/%Y}   Válida hasta: {vence:%d/%m/%Y}"), align="R",
             new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)
    para = ", ".join(x for x in (cliente, empresa) if x) or "Cliente"
    doc.cell(w, 6, t(f"Para: {para}  ({email})"), new_x="LMARGIN", new_y="NEXT")
    doc.ln(6)

    doc.set_fill_color(235)
    doc.set_font("Helvetica", "B", 10)
    doc.cell(w * 0.75, 8, t("Concepto"), fill=True)
    doc.cell(w * 0.25, 8, t("Importe"), fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 10)
    for concepto, importe in q.lineas:
        doc.cell(w * 0.75, 8, t(concepto), border="B")
        doc.cell(w * 0.25, 8, t(m(importe)), border="B", align="R", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "B", 11)
    doc.cell(w * 0.75, 9, t("Primer pago"))
    doc.cell(w * 0.25, 9, t(m(q.primer_pago)), align="R", new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 10)
    periodo = "cada año" if q.pago == "anual" else "cada mes"
    doc.cell(w * 0.75, 7, t(f"Después, {periodo}"))
    doc.cell(w * 0.25, 7, t(m(q.recurrente)), align="R", new_x="LMARGIN", new_y="NEXT")
    doc.ln(6)

    extra = q.bloques_extra * cat["extras"]["respuestas"]["bloque"]
    doc.set_font("Helvetica", "B", 11)
    doc.cell(w, 7, t(f"Qué incluye el paquete {p['nombre']}"), new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 10)
    for item in p["incluye"] + [f"{p['respuestas_mes'] + extra:,} respuestas del bot al mes"]:
        doc.multi_cell(w, 6, t(f"-  {item}"), new_x="LMARGIN", new_y="NEXT")
    doc.ln(4)

    doc.set_font("Helvetica", "B", 11)
    doc.cell(w, 7, t("Condiciones"), new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 9)
    doc.set_text_color(70)
    for c in cat["condiciones"] + [f"Precios en {q.moneda}."]:
        doc.multi_cell(w, 5, t(f"-  {c}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())
