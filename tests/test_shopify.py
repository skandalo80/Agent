"""Importar productos de una tienda Shopify: leer /products.json, elegir y agregar al catálogo."""
import json
import os
from pathlib import Path

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402

from igbot import empresa, shopify, sitio  # noqa: E402
import test_contenido  # noqa: E402
from test_contenido import ORIGEN, png  # noqa: E402
from test_empresa import PASO_1  # noqa: E402
from test_panel import campos_con_nombre  # noqa: E402

cliente = test_contenido.cliente


def producto(i: int, **extra) -> dict:
    return {"id": 1000 + i, "title": f"Taza {i}", "handle": f"taza-{i}", "body_html": "<p>Cerámica <b>hecha</b> a mano.</p>",
            "variants": [{"title": "Chica", "price": "250.00", "available": True},
                         {"title": "Grande", "price": "1,320.50", "available": True}],
            "images": [{"src": f"https://cdn.shopify.com/s/taza-{i}.png"}, {"src": f"//cdn.shopify.com/s/b-{i}.png"},
                       {"src": f"https://cdn.shopify.com/s/c-{i}.png"}], **extra}


@pytest.fixture
def tienda(monkeypatch):
    visitas = []
    total = [producto(i) for i in range(300)] + [{"title": ""}]

    def manejar(request: httpx.Request) -> httpx.Response:
        visitas.append(str(request.url))
        if request.url.path == "/products.json":
            pagina = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json={"products": total[(pagina - 1) * 250:pagina * 250]})
        if request.url.path == "/meta.json":
            return httpx.Response(200, json={"currency": "USD"})
        if request.url.host == "cdn.shopify.com":
            assert request.url.params["width"] == "1200"
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(sitio, "_transporte", httpx.MockTransport(manejar))

    async def resolver(host):
        return ["93.184.216.34"] if host in ("cafelunar.mx", "cdn.shopify.com") else ["10.0.0.1"]

    monkeypatch.setattr(sitio, "resolver", resolver)
    return visitas


def test_producto_de_shopify():
    p = shopify.producto("https://t.mx", producto(1, variants=[{"title": "Default Title", "price": "99.00"}]))
    assert p["nombre"] == "Taza 1" and p["descripcion"] == "Cerámica hecha a mano." and p["precio"] == "99"
    assert not p["desde"] and p["opciones"] == [] and p["url"] == "https://t.mx/products/taza-1"
    assert p["fotos"] == ["https://cdn.shopify.com/s/taza-1.png", "https://cdn.shopify.com/s/b-1.png"]
    assert shopify.producto("https://t.mx", {"title": " "}) is None


def test_elige_e_importa(cliente, tienda):
    client, c, *_ = cliente
    assert "Traer mis productos de Shopify" not in client.get("/panel/empresa?paso=3").text  # sin sitio web
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    r = client.get("/panel/empresa?paso=3")
    assert "Traer mis productos de Shopify" in r.text
    campos_con_nombre(r.text)
    client.post("/panel/empresa/producto", headers=ORIGEN, data={"nombre": "Ya la tengo",
                                                                 "url": "https://cafelunar.mx/products/taza-2"})

    r = client.post("/panel/empresa/shopify", headers=ORIGEN, data={"accion": "leer"})
    assert 'url=/panel/empresa/shopify"' in r.text
    assert sum("products.json" in v for v in tienda) == 2  # dos páginas
    r = client.get("/panel/empresa/shopify")
    assert "Encontramos 300 en tu tienda" in r.text and "hasta 59 más" in r.text and "Precios en USD" in r.text
    assert "Desde $250" in r.text and "2 opciones" in r.text and "Ya está en tu catálogo" in r.text
    campos_con_nombre(r.text)

    r = client.post("/panel/empresa/shopify/importar", headers=ORIGEN,
                    data={"accion": "importar", "sid": ["1000", "1002", "1003", "inventado"]})
    assert 'url=/panel/empresa?paso=3&amp;aviso=shopify"' in r.text
    prods = client.portal.call(empresa.productos, c)
    assert [p["nombre"] for p in prods] == ["Ya la tengo", "Taza 0", "Taza 3"]
    t = prods[1]
    assert t["precio"] == "250" and t["desde"] and t["moneda"] == "USD" and t["opciones"] == ["Chica", "Grande"]
    assert len(t["imagenes"]) == 2 and all((Path(c.media_dir) / f"{x}.jpg").exists() for x in t["imagenes"])
    assert client.portal.call(shopify.lista, c) == {}
    client.portal.call(empresa.esperar_revision, c)
    assert "Taza 0: Desde $250 USD. Cerámica hecha a mano. Opciones: Chica, Grande." in Path(c.prompt_path).read_text()
    assert "agregamos a tu catálogo" in client.get("/panel/empresa?paso=3&aviso=shopify").text


def test_si_no_es_shopify(cliente, tienda, monkeypatch):
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "sitio_web": "otra.mx"})
    client.post("/panel/empresa/shopify", headers=ORIGEN, data={"accion": "leer"})
    assert "No pudimos traer tus productos" in client.get("/panel/empresa/shopify").text
    client.post("/panel/empresa/shopify/importar", headers=ORIGEN, data={"accion": "descartar"})
    assert client.portal.call(shopify.lista, c) == {}
