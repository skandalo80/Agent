"""Conecta tu Shopify: el anuncio de cada chat, el inventario, los enlaces con código y las ventas."""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, urlencode, urlsplit

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402

import test_contenido  # noqa: E402
from igbot import config, tienda_shopify as ts  # noqa: E402
from test_contenido import ORIGEN, bot  # noqa: E402

cliente = test_contenido.cliente
SECRETO = "shpss_prueba"


def pedido(i, pagina="", monto="450.00", cancelado=None):
    """Un pedido como lo manda Shopify en el aviso orders/create."""
    return {"id": 5000 + i, "name": f"#10{i}", "created_at": "2026-10-28T12:00:00-06:00", "cancelled_at": cancelado,
            "landing_site": pagina, "total_price": monto, "currency": "MXN",
            "line_items": [{"title": "Bolsa Luna", "quantity": 1}], "customer": {"email": "no@se.guarda"}}


def firmado(client, tema, datos, tienda="lunar.myshopify.com", firma=None):
    cuerpo = json.dumps(datos).encode()
    firma = firma or base64.b64encode(hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).digest()).decode()
    return client.post(f"/shopify/webhooks/{tema}", content=cuerpo,
                       headers={"X-Shopify-Hmac-Sha256": firma, "X-Shopify-Shop-Domain": tienda})


@pytest.fixture
def shopify(cliente, monkeypatch):
    """La app de Shopify configurada y una tienda de mentira que contesta la API."""
    monkeypatch.setattr(config, "SHOPIFY_API_KEY", "llave")
    monkeypatch.setattr(config, "SHOPIFY_API_SECRET", SECRETO)
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    tienda = {"avisos": [], "consultas": []}

    def manejar(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "lunar.myshopify.com"
        if request.url.path == "/admin/oauth/access_token":
            assert json.loads(request.content)["code"] == "codigo1"
            return httpx.Response(200, json={"access_token": "shpat_1", "scope": ts.SCOPES})
        assert request.headers["x-shopify-access-token"] == "shpat_1"
        cuerpo = json.loads(request.content)
        tienda["consultas"].append(cuerpo)
        if "shop {" in cuerpo["query"]:
            return httpx.Response(200, json={"data": {"shop": {"name": "Café Lunar", "myshopifyDomain": "lunar.myshopify.com",
                                                                "primaryDomain": {"host": "www.cafelunar.mx"}}}})
        if "webhookSubscriptionCreate" in cuerpo["query"]:
            tienda["avisos"].append((cuerpo["variables"]["topic"], cuerpo["variables"]["uri"]))
            return httpx.Response(200, json={"data": {"webhookSubscriptionCreate": {"userErrors": []}}})
        return httpx.Response(200, json={"data": {"products": {"nodes": [
            {"title": "Bolsa Luna", "status": "ACTIVE", "onlineStoreUrl": "https://cafelunar.mx/products/bolsa",
             "tracksInventory": True, "variants": {"nodes": [
                 {"title": "Negra", "availableForSale": True, "inventoryQuantity": 5, "inventoryItem": {
                     "tracked": True, "inventoryLevels": {"nodes": [
                         {"location": {"name": "Centro"}, "quantities": [{"quantity": 3}]},
                         {"location": {"name": "Norte"}, "quantities": [{"quantity": 2}]}]}}},
                 {"title": "Café", "availableForSale": False, "inventoryQuantity": 0, "inventoryItem": {"tracked": True}}]}},
            {"title": "Bolsa vieja", "status": "ARCHIVED"}]}}})

    monkeypatch.setattr(ts, "_transporte", httpx.MockTransport(manejar))
    return (*cliente, tienda)


def conectar(client, hmac_falso=False):
    r = client.post("/panel/shopify/conectar", headers=ORIGEN, data={"tienda": "https://Lunar.myshopify.com/"},
                    follow_redirects=False)
    destino = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(destino.query).items()}
    assert destino.hostname == "lunar.myshopify.com" and destino.path == "/admin/oauth/authorize"
    assert q["scope"] == "read_products,read_inventory,read_locations,read_orders"
    assert q["redirect_uri"] == "https://console.skandar.online/panel/shopify/callback"
    vuelta = {"code": "codigo1", "shop": "lunar.myshopify.com", "state": q["state"], "timestamp": str(int(time.time()))}
    texto = "&".join(f"{k}={v}" for k, v in sorted(vuelta.items()))
    vuelta["hmac"] = "0" * 64 if hmac_falso else hmac.new(SECRETO.encode(), texto.encode(), hashlib.sha256).hexdigest()
    return client.get("/panel/shopify/callback?" + urlencode(vuelta), follow_redirects=False), vuelta


def test_sin_llaves_no_sale(cliente):
    client, c, *_ = cliente
    assert "Muy pronto" in client.get("/panel/shopify").text
    assert "Tienda Shopify" not in client.get("/panel/conexion").text


def test_conectar_y_desconectar(shopify):
    client, c, *_ = shopify
    assert "Tienda Shopify</b><span>Sin conectar" in client.get("/panel/conexion").text
    r = client.get("/panel/shopify")
    assert "<button class=\"boton\">Conectar tu Shopify</button>" in r.text and "Te llevamos a tu Shopify" in r.text
    assert client.post("/panel/shopify/conectar", data={"tienda": "lunar.myshopify.com"}).status_code == 403  # sin Origin
    assert "Escribe la dirección de tu tienda" in client.post("/panel/shopify/conectar", headers=ORIGEN,
                                                              data={"tienda": "lunar"}).text
    r, vuelta = conectar(client, hmac_falso=True)  # una vuelta con la firma cambiada no conecta nada
    assert r.headers["location"] == "/panel/shopify?aviso=fallo" and client.portal.call(ts.conexion, c) == {}
    r, vuelta = conectar(client)
    assert r.headers["location"] == "/panel/shopify?aviso=conectada"
    con = client.portal.call(ts.conexion, c)
    assert con["token"] == "shpat_1" and con["nombre"] == "Café Lunar"
    assert con["dominios"] == ["cafelunar.mx", "lunar.myshopify.com"]
    r = client.get("/panel/shopify?aviso=conectada")
    assert "Tu tienda Shopify está conectada" in r.text and "utm_content={{ad.id}}" in r.text
    assert "Café Lunar · inventario y ventas" in client.get("/panel/conexion").text
    # El mismo state no sirve dos veces (la cookie ya se borró).
    assert client.get("/panel/shopify/callback?" + urlencode(vuelta),
                      follow_redirects=False).headers["location"] == "/panel/shopify?aviso=fallo"
    assert "Desconectamos tu tienda" in client.post("/panel/shopify/desconectar", headers=ORIGEN).text
    assert client.portal.call(ts.conexion, c) == {}
    assert client.get("/panel/shopify/callback?shop=lunar.myshopify.com&error=access_denied",
                      follow_redirects=False).headers["location"] == "/panel/shopify?aviso=cancelada"


def test_state_es_de_la_cuenta(shopify):
    client, c, *_ = shopify
    state = ts.nuevo_state(c)
    assert ts.state_valido(state, state, c)
    assert not ts.state_valido(state, None, c) and not ts.state_valido(state, state + "x", c)
    otra = type("C", (), {"slug": "otra"})()
    assert not ts.state_valido(state, state, otra)


def test_inventario_para_el_asistente(shopify):
    client, c, _, _, tienda = shopify
    conectar(client)
    texto = client.portal.call(ts.existencias, c, "¿la bolsa luna?")
    assert "Producto: Bolsa Luna (https://cafelunar.mx/products/bolsa)" in texto
    assert "- Negra: 5 disponibles (por sucursal: Centro: 3, Norte: 2)" in texto
    assert "- Café: agotado" in texto and "Bolsa vieja" not in texto
    assert tienda["consultas"][-1]["variables"]["q"] == "title:la* title:bolsa* title:luna*"
    assert [t.name for t in ts.herramientas(c)] == ["existencias"]


def test_enlaces_con_codigo_del_chat(shopify):
    client, c, *_ = shopify
    texto = "Mírala aquí: https://cafelunar.mx/products/bolsa."
    assert client.portal.call(ts.etiquetar, c, "ana", texto) == texto  # sin tienda conectada no se toca
    conectar(client)
    nuevo = client.portal.call(ts.etiquetar, c, "ana",
                               "Mírala aquí: https://www.cafelunar.mx/products/bolsa?variant=2. Y https://otro.com/x")
    codigo = client.portal.call(c.store.codigo_chat, "ana")
    assert nuevo == f"Mírala aquí: https://www.cafelunar.mx/products/bolsa?variant=2&sk={codigo}. Y https://otro.com/x"
    assert client.portal.call(c.store.codigo_chat, "ana") == codigo != client.portal.call(c.store.codigo_chat, "beto")
    assert client.portal.call(c.store.usuario_de_codigo, codigo) == "ana"


def test_ventas_de_chats_y_anuncios(shopify):
    client, c, _, _, tienda = shopify
    conectar(client)
    # Al conectar, la tienda queda suscrita a sus pedidos: nada pregunta cada rato.
    assert tienda["avisos"] == [
        ("ORDERS_CREATE", "https://console.skandar.online/shopify/webhooks/orders-create"),
        ("ORDERS_CANCELLED", "https://console.skandar.online/shopify/webhooks/orders-cancelled")]
    assert not client.portal.call(ts.conexion, c)["sin_ventas"]
    ana = client.portal.call(c.store.codigo_chat, "ana")
    client.portal.call(c.store.guardar_anuncio, "ana", "120211", "Bolsas de otoño", "")
    assert firmado(client, "orders-create", pedido(1, f"/products/bolsa?sk={ana}")).status_code == 200
    firmado(client, "orders-create", pedido(1, f"/products/bolsa?sk={ana}"))  # reintento de Shopify
    firmado(client, "orders-create", pedido(2, "/?utm_source=meta&utm_content=120299", monto="99.50"))
    firmado(client, "orders-create", pedido(3, "/products/bolsa"))  # ni chat ni anuncio: no se guarda
    firmado(client, "orders-create", pedido(4, f"/?sk={ana}"), tienda="otra.myshopify.com")  # otra tienda
    assert firmado(client, "orders-create", pedido(5, f"/?sk={ana}"), firma="malo").status_code == 401
    ventas = client.portal.call(c.store.ventas_de, "ana")
    assert [(v["externo"], v["ad_id"], v["monto"]) for v in ventas] == [("5001", "120211", 450.0)]
    assert ventas[0]["detalle"] == {"folio": "#101", "productos": ["1 × Bolsa Luna"]}
    assert client.portal.call(c.store.ventas_de, "")[0]["ad_id"] == "120299"  # de un anuncio, sin chat
    firmado(client, "orders-cancelled", pedido(1, cancelado="2026-10-28T13:00:00-06:00"))
    assert client.portal.call(c.store.ventas_de, "ana") == []


def test_anuncio_y_compra_en_conversaciones(shopify):
    client, c, *_ = shopify
    client.portal.call(c.store.set, "bot_activo", "1")
    entrada = {"id": c.ig_account_id, "messaging": [{
        "sender": {"id": "ana"}, "recipient": {"id": c.ig_account_id}, "timestamp": 1,
        "message": {"mid": "m1", "text": "Hola, ¿tienen la bolsa?", "referral": {
            "ref": "", "ad_id": "120211", "source": "ADS", "type": "OPEN_THREAD",
            "ads_context_data": {"ad_title": "Bolsas de otoño", "photo_url": "https://x/foto.jpg"}}}}]}

    async def recibir():
        with bot.usar(c):
            await bot.receive_entry(c, entrada)

    client.portal.call(recibir)
    assert client.portal.call(c.store.anuncios_de, ["ana"])["ana"]["titulo"] == "Bolsas de otoño"
    client.portal.call(c.store.add_venta, "shopify", "o1", "ana", "120211", 1320.5, "MXN", {}, time.time())
    r = client.get("/panel/asistente?ver=bot")
    assert '<span class="razon anuncio">Anuncio</span>' in r.text
    r = client.get("/panel/asistente?ver=bot&chat=ana")
    assert "Llegó por el anuncio «Bolsas de otoño» · Compró $1,320.50 MXN en tu tienda" in r.text


def test_avisos_de_privacidad(shopify):
    client, c, *_ = shopify
    conectar(client)
    datos = {"shop_domain": "lunar.myshopify.com"}
    assert firmado(client, "shop-redact", datos, firma="malo").status_code == 401
    assert firmado(client, "customers-redact", datos).status_code == 200
    assert client.portal.call(ts.conexion, c)["token"]
    assert firmado(client, "shop-redact", datos).status_code == 200
    assert client.portal.call(ts.conexion, c) == {}
