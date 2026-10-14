"""Tienda de Nortex: lectura de hojas y cotización por volumen (sin red)."""
import asyncio
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402

from igbot import tienda  # noqa: E402
from igbot.google import Google  # noqa: E402

REGLAS = tienda.rules("clientes/nortex/reglas.yaml")


def producto(sku, nombre, tipo, precios, moq="12", custom="Y"):
    t1, t2, t3 = precios
    return {
        "SKU": sku, "Name_ES": nombre, "ProductType": tipo, "Material": "x",
        "Price_Tier1_12_49": t1, "Price_Tier2_50_99": t2, "Price_Tier3_100plus": t3,
        "MOQ": moq, "Customizable": custom, "Active": "Y",
    }


CATALOGO = {
    "NX-POL-NVY": producto("NX-POL-NVY", "Playera Polo Azul Marino", "Polo", ("$185", "$165", "$145")),
    "NX-POL-BLK": producto("NX-POL-BLK", "Playera Polo Negro", "Polo", ("$185", "$165", "$145")),
    "NX-CAP-BLK": producto("NX-CAP-BLK", "Gorra Negro", "Cap", ("$95", "$85", "$75"), moq="24"),
    "NX-PNT-GRY": producto("NX-PNT-GRY", "Pants Deportivo Gris", "Track Pants", ("$280", "$255", "$230"), custom="N"),
}
EXISTENCIAS = {
    "NX-POL-NVY": {"S": 109, "M": 55, "L": 52},
    "NX-POL-BLK": {"S": 114, "M": 137, "L": 54},
    "NX-CAP-BLK": {"Ajustable": 16},
    "NX-PNT-GRY": {"S": 40, "M": 143, "L": 58},
}


def item(sku, talla, cantidad, pers="ninguna", extra=0):
    return {"sku": sku, "talla": talla, "cantidad": cantidad, "personalizacion": pers, "ubicaciones_extra": extra}


def cotiza(items, logos=1):
    return tienda.quote(items, logos, CATALOGO, EXISTENCIAS, REGLAS)


def test_ejemplo_de_la_propuesta_60_polos_bordadas():
    o = cotiza([item("NX-POL-NVY", "M", 30, "bordado"), item("NX-POL-NVY", "L", 30, "bordado")])
    assert o.subtotal == 12530  # 60 x 165 + 60 x 38 + ponchado 350
    assert o.iva == 2004.80 and o.total == 14534.80
    assert o.entrega == "8 a 10 días hábiles"


def test_tallas_como_se_dicen_en_mexico():
    o = cotiza([item("NX-POL-BLK", "G", 30), item("NX-POL-BLK", "chica", 30)])
    assert "talla L" in o.texto() and "talla S" in o.texto()


def test_el_rango_se_cuenta_por_tipo_mezclando_colores():
    o = cotiza([item("NX-POL-NVY", "S", 30), item("NX-POL-BLK", "S", 30)])
    assert o.subtotal == 60 * 165
    assert o.entrega == "5 a 7 días hábiles"


def test_ponchado_gratis_desde_100_y_ubicacion_extra():
    o = cotiza([item("NX-POL-BLK", "M", 100, "bordado", extra=1)], logos=2)
    assert o.subtotal == 100 * 145 + 100 * 30 + 100 * 15
    assert any("gratis" in c for c, _ in o.lineas)


def test_sin_existencias_avisa_y_alarga_la_entrega():
    o = cotiza([item("NX-CAP-BLK", "ajustable", 24)])
    assert o.entrega == "15 a 20 días hábiles"
    assert "hay 16 disponibles" in o.texto()


@pytest.mark.parametrize("items,msg", [
    ([item("NX-POL-NVY", "M", 10)], "mínimo de Polo es 12"),
    ([item("NX-CAP-BLK", "Ajustable", 12)], "mínimo de Cap es 24"),
    ([item("NX-POL-NVY", "M", 300)], "pasar_a_humano"),
    ([item("NX-POL-NVY", "XL", 20)], "talla 'XL'"),
    ([item("NX-PNT-GRY", "M", 20, "bordado")], "no se puede personalizar"),
    ([item("NX-XXX", "M", 20)], "No existe"),
])
def test_pedidos_que_no_se_pueden(items, msg):
    with pytest.raises(ValueError, match=msg):
        cotiza(items)


def test_link_de_la_hoja():
    url = "https://docs.google.com/spreadsheets/d/1nmI5mLVs2Or/edit?gid=2028608343#gid=2028608343"
    assert tienda.parse_sheet(url) == ("1nmI5mLVs2Or", 2028608343)
    assert tienda.parse_sheet("abc") == ("abc", None)
    assert tienda.number("$1,145.50") == 1145.5


def test_lee_la_pestania_por_gid():
    calls = []

    class FakeGoogle(Google):
        def __init__(self):
            pass

        async def _call(self, method, url, **kw):
            calls.append(url)
            if url.endswith("/hoja"):
                return {"sheets": [{"properties": {"sheetId": 1, "title": "Otra"}},
                                   {"properties": {"sheetId": 7, "title": "Mi Catálogo"}}]}
            return {"values": [["SKU", "Variant", "Available_Qty"], ["NX-1", "M", "5"], [], ["NX-1", "L"]]}

    rows = asyncio.run(FakeGoogle().sheet_rows("hoja", 7))
    assert rows == [{"SKU": "NX-1", "Variant": "M", "Available_Qty": "5"},
                    {"SKU": "NX-1", "Variant": "L", "Available_Qty": ""}]
    assert calls[1].endswith("/values/%27Mi%20Cat%C3%A1logo%27")


def test_prompt_de_la_tienda(monkeypatch):
    monkeypatch.setattr(tienda.config, "STORE_RULES_PATH", "clientes/nortex/reglas.yaml")
    text = tienda.prompt_text()
    assert "Nortex Uniformes" in text and "cotizar_pedido" in text and "$45.00" in text


def test_cotizacion_formal_en_pdf_por_correo_y_dm(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from test_tools import NOW, FakeGoogle

    import app as bot
    from igbot import config, tools
    from igbot.store import Store

    monkeypatch.setattr(config, "STORE_RULES_PATH", "clientes/nortex/reglas.yaml")
    monkeypatch.setattr(config, "CLIENTE", "nortex")
    monkeypatch.setattr(config, "QUOTES_DIR", str(tmp_path / "data/nortex/cotizaciones"))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")
    monkeypatch.setattr(config, "MAIL_CC", "copia@gmail.com")
    monkeypatch.setattr(tools, "now", lambda: NOW)

    async def catalogo(google):
        return CATALOGO

    async def existencias(google):
        return EXISTENCIAS

    monkeypatch.setattr(tienda, "products", catalogo)
    monkeypatch.setattr(tienda, "stock", existencias)

    class FakeIG:
        files = []

        async def send_file(self, user_id, url):
            self.files.append((user_id, url))

    async def main():
        store = Store(":memory:")
        await store.open()
        await store.add_incoming("ana", "m1", "ana@empresa.com")
        google, ig, state = FakeGoogle(), FakeIG(), {}
        t = {x.name: x.handler for x in tienda_tools(google, store, ig, state)}
        args = {"items": [item("NX-POL-NVY", "M", 30, "bordado"), item("NX-POL-NVY", "L", 30, "bordado")],
                "logos": 1, "nombre": "Ana", "empresa": "Taller Ana"}
        assert (await t["enviar_cotizacion_pedido"]({**args, "email": "otra@x.com"})).get("is_error")
        r = await t["enviar_cotizacion_pedido"]({**args, "email": "ana@empresa.com"})
        quotes = await store.events("cotizacion", 0, 2e9)
        await store.close()
        return r, google, ig, state, quotes

    def tienda_tools(google, store, ig, state):
        return tools.store_tools(google, store, "ana", ig, state)

    r, google, ig, state, quotes = asyncio.run(main())
    assert "COT-0001" in r["content"][0]["text"] and "$14,534.80" in r["content"][0]["text"]
    assert google.sent[0][1:3] == ("ana@empresa.com", "copia@gmail.com")
    name, pdf = google.attachments[0]
    assert name == "Cotizacion-COT-0001.pdf" and pdf.startswith(b"%PDF")
    assert state["acted"] and quotes[0][1]["total"] == 14534.8
    user, url = ig.files[0]
    assert user == "ana" and url.startswith("https://bot.ejemplo.com/cotizaciones/nortex/")

    # El dominio llega al proceso principal, que sirve el PDF de la carpeta de Nortex.
    monkeypatch.chdir(tmp_path)
    client = TestClient(bot.app)
    assert client.get(url.removeprefix("https://bot.ejemplo.com")).content == pdf
    assert client.get("/cotizaciones/..%2Fx/" + "a" * 22 + ".pdf").status_code == 404
