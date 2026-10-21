"""Diseño de las cotizaciones en PDF con el estilo de la marca.

Sobrio y limpio: tipografía Inter, negro y grafito sobre blanco, líneas finas grises
y un solo detalle en el color de acento del negocio (negocio.color_acento).
"""
from pathlib import Path

from fpdf import FPDF

FONTS = Path(__file__).resolve().parent.parent / "fonts"

NEGRO = (10, 10, 10)
GRAFITO = (29, 29, 31)
GRIS = (110, 110, 115)
LINEA = (210, 210, 215)
CLARO = (245, 245, 247)
ACENTO = "#C2410C"


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


class Cotizacion(FPDF):
    """Hoja carta con encabezado del negocio, pie de página y bloques de la cotización."""

    etiqueta = "COTIZACIÓN"

    def __init__(self, negocio: str, contacto: str, folio: str, acento: str = ""):
        super().__init__(format="Letter")
        self.negocio, self.contacto, self.folio = negocio, contacto, folio
        self.acento = _rgb(acento or ACENTO)
        self.add_font("Inter", "", FONTS / "Inter-Regular.ttf")
        self.add_font("Inter", "B", FONTS / "Inter-SemiBold.ttf")
        self.set_margins(22, 20, 22)
        self.set_auto_page_break(True, margin=19)
        self.add_page()

    def font(self, size: float, bold: bool = False, color=GRAFITO) -> None:
        self.set_font("Inter", "B" if bold else "", size)
        self.set_text_color(*color)

    def rule(self, color=LINEA, width: float = 0.2) -> None:
        self.set_draw_color(*color)
        self.set_line_width(width)
        y = self.get_y()
        self.line(self.l_margin, y, self.l_margin + self.epw, y)

    def header(self):
        self.font(11, bold=True, color=NEGRO)
        self.cell(self.epw / 2, 6, self.negocio)
        self.font(8, color=GRIS)
        self.set_char_spacing(0.6)
        self.cell(self.epw / 2, 6, self.etiqueta, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.font(9, color=GRIS)
        self.cell(self.epw / 2, 5, self.contacto)
        self.font(9, bold=True)
        self.cell(self.epw / 2, 5, self.folio, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.rule()
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.rule()
        self.ln(3)
        self.font(8, color=GRIS)
        self.cell(self.epw / 2, 4, f"{self.negocio}  ·  {self.contacto}")
        self.cell(self.epw / 2, 4, f"{self.folio}  ·  {self.page_no()}/{{nb}}", align="R")

    def titulo(self, antetitulo: str, titulo: str, bajada: str = "") -> None:
        # El único toque de color: una raya corta sobre el título.
        self.set_fill_color(*self.acento)
        self.rect(self.l_margin, self.get_y(), 12, 0.9, style="F")
        self.ln(5)
        self.font(8, bold=True, color=GRIS)
        self.set_char_spacing(0.6)
        self.cell(self.epw, 5, antetitulo.upper(), new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.font(24, bold=True, color=NEGRO)
        self.set_char_spacing(-0.4)
        self.multi_cell(self.epw, 11, titulo, new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        if bajada:
            self.ln(1)
            self.font(10.5, color=GRIS)
            self.multi_cell(self.epw, 5.5, bajada, new_x="LMARGIN", new_y="NEXT")
        self.ln(7)

    def datos(self, campos: list[tuple[str, str]]) -> None:
        """Fila de datos cortos (para, fecha, vigencia) con etiqueta gris arriba."""
        ancho = self.epw / len(campos)
        y = self.get_y()
        for i, (etiqueta, valor) in enumerate(campos):
            self.set_xy(self.l_margin + i * ancho, y)
            self.font(7.5, bold=True, color=GRIS)
            self.set_char_spacing(0.5)
            self.cell(ancho - 4, 4, etiqueta.upper(), new_x="LEFT", new_y="NEXT")
            self.set_char_spacing(0)
            self.font(10)
            self.multi_cell(ancho - 4, 5, valor or "-", align="L", new_x="LEFT", new_y="NEXT")
        self.set_xy(self.l_margin, max(self.get_y(), y + 9) + 7)

    def tabla(self, filas: list[tuple[str, str]], encabezado=("Concepto", "Importe")) -> None:
        self.font(7.5, bold=True, color=GRIS)
        self.set_char_spacing(0.5)
        self.cell(self.epw * 0.72, 6, encabezado[0].upper())
        self.cell(self.epw * 0.28, 6, encabezado[1].upper(), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.rule(GRAFITO, 0.3)
        for concepto, importe in filas:
            self.ln(2.2)
            y = self.get_y()
            self.font(10)
            self.multi_cell(self.epw * 0.72, 5.5, concepto, new_x="RIGHT", new_y="TOP")
            alto = self.get_y() - y if self.get_y() > y else 5.5
            self.set_xy(self.l_margin + self.epw * 0.72, y)
            self.cell(self.epw * 0.28, 5.5, importe, align="R")
            self.set_xy(self.l_margin, y + max(alto, 5.5) + 2.2)
            self.rule()

    def totales(self, filas: list[tuple[str, str]], principal: tuple[str, str], nota: str = "") -> None:
        """Sumas secundarias alineadas a la derecha y el total en un panel claro."""
        self.ln(3)
        for etiqueta, valor in filas:
            self.font(10, color=GRIS)
            self.cell(self.epw * 0.72, 6.5, etiqueta, align="R")
            self.font(10)
            self.cell(self.epw * 0.28, 6.5, valor, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)
        alto = 20 if nota else 16
        y = self.get_y()
        self.set_fill_color(*CLARO)
        self.rect(self.l_margin, y, self.epw, alto, style="F", round_corners=True, corner_radius=2.5)
        self.set_xy(self.l_margin + 6, y + (4 if nota else 5))
        self.font(10, bold=True)
        self.cell(self.epw * 0.5, 6, principal[0])
        self.set_xy(self.l_margin + self.epw * 0.4, y + (3 if nota else 3.5))
        self.font(18, bold=True, color=NEGRO)
        self.set_char_spacing(-0.3)
        self.cell(self.epw * 0.6 - 6, 9, principal[1], align="R")
        self.set_char_spacing(0)
        if nota:
            self.set_xy(self.l_margin + 6, y + 12)
            self.font(9, color=GRIS)
            self.cell(self.epw - 12, 5, nota, align="R")
        self.set_xy(self.l_margin, y + alto + 8)

    def seccion(self, titulo: str, puntos: list[str], chica: bool = False) -> None:
        if self.get_y() > self.page_break_trigger - 15:
            self.add_page()
        self.font(11, bold=True, color=NEGRO)
        self.cell(self.epw, 7, titulo, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)
        size, alto = (8.5, 4.6) if chica else (10, 5.2)
        for punto in puntos:
            self.font(size, color=GRIS)
            self.cell(5, alto, "—")
            self.font(size, color=GRIS if chica else GRAFITO)
            self.multi_cell(self.epw - 5, alto, punto, new_x="LMARGIN", new_y="NEXT")
            self.ln(0.6)
        self.ln(5)

    def bytes(self) -> bytes:
        return bytes(self.output())
