"""Conecta tu cuenta de anuncios: los números de Meta por anuncio, junto a los chats y ventas de cada uno."""
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402

import test_contenido  # noqa: E402
from igbot import anuncios_meta, config  # noqa: E402
from test_contenido import ORIGEN  # noqa: E402

cliente = test_contenido.cliente

REAL = httpx.AsyncClient  # las pruebas de conectar reemplazan el cliente de httpx
TOKEN = {"token": "tok-ads", "vence": time.time() + 60 * 86400}


@pytest.fixture
def con_facebook(cliente, monkeypatch):
    monkeypatch.setattr(config, "FB_APP_ID", "app-fb")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    monkeypatch.setattr(anuncios_meta, "_cache", {})
    return cliente


def volver_de_meta(client, monkeypatch, cuentas):
    async def del_codigo(code):
        assert code == "cod1"
        return TOKEN, cuentas

    monkeypatch.setattr(anuncios_meta, "cuentas_del_codigo", del_codigo)
    r = client.post("/panel/anuncios/conectar", headers=ORIGEN, follow_redirects=False)
    destino = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(destino.query).items()}
    assert destino.hostname == "www.facebook.com" and q["scope"] == "ads_read,business_management"
    assert q["redirect_uri"] == "https://console.skandar.online/panel/anuncios/callback"
    return client.get(f"/panel/anuncios/callback?code=cod1&state={q['state']}", follow_redirects=False)


def test_conectar_una_cuenta_y_desconectarla(con_facebook, monkeypatch):
    client, c, *_ = con_facebook
    assert "Cuenta de anuncios</b><span>Sin conectar" in client.get("/panel/conexion").text
    r = volver_de_meta(client, monkeypatch, [{"id": "act_9", "name": "Café Lunar Ads", "currency": "MXN"}])
    assert r.headers["location"] == "/panel/anuncios?aviso=conectada"
    datos = client.portal.call(anuncios_meta.conexion, c)
    assert datos["cuenta"] == "act_9" and datos["token"] == "tok-ads" and datos["moneda"] == "MXN"
    assert "Café Lunar Ads · gasto, clics y compras por anuncio" in client.get("/panel/conexion").text
    assert "Desconectamos tu cuenta" in client.post("/panel/anuncios/desconectar", headers=ORIGEN).text
    assert client.portal.call(anuncios_meta.conexion, c) == {}


def test_varias_cuentas_se_elige(con_facebook, monkeypatch):
    client, c, *_ = con_facebook
    cuentas = [{"id": "act_1", "name": "Uno"}, {"id": "act_2", "name": "Dos", "currency": "USD"}]
    r = volver_de_meta(client, monkeypatch, cuentas)
    assert "¿Qué cuenta de anuncios conectamos?" in r.text and "Dos" in r.text
    client.cookies.set(anuncios_meta.ELEGIR_COOKIE, r.cookies[anuncios_meta.ELEGIR_COOKIE], path="/panel/anuncios")
    r = client.post("/panel/anuncios/elegir", headers=ORIGEN, data={"cuenta": "act_2"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/anuncios?aviso=conectada"
    assert client.portal.call(anuncios_meta.conexion, c)["cuenta"] == "act_2"


def test_con_configuracion_de_meta_se_manda_config_id(con_facebook, monkeypatch):
    client, *_ = con_facebook
    monkeypatch.setattr(config, "FB_ADS_CONFIG_ID", "cfg-ads")
    r = client.post("/panel/anuncios/conectar", headers=ORIGEN, follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["config_id"] == ["cfg-ads"] and "scope" not in q


def test_las_compras_no_se_cuentan_dos_veces():
    x = anuncios_meta.fila_de({
        "ad_id": "7", "ad_name": "Otoño", "spend": "120.5", "clicks": "40", "impressions": "900",
        "actions": [{"action_type": "omni_purchase", "value": "3"},
                    {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
                    {"action_type": anuncios_meta.CHATS_META, "value": "5"}],
        "action_values": [{"action_type": "omni_purchase", "value": "1350"}]})
    assert (x["gasto"], x["clics"], x["compras"], x["valor_compras"], x["chats_meta"]) == (120.5, 40, 3, 1350.0, 5)
    solo_pixel = anuncios_meta.fila_de({"actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"}]})
    assert solo_pixel["compras"] == 2


def test_resultados_junta_meta_con_los_chats(con_facebook, monkeypatch):
    client, c, *_ = con_facebook
    hoy = time.time()
    client.portal.call(c.store.guardar_anuncio, "u1", "7", "Otoño", "")
    client.portal.call(c.store.guardar_anuncio, "u2", "7", "Otoño", "")
    client.portal.call(c.store.guardar_anuncio, "u3", "8", "Solo chats", "")
    client.portal.call(c.store.add_venta, "stripe", "pi_1", "u1", "7", 450.0, "MXN", {}, hoy)
    # Sin cuenta de anuncios: salen los chats y se ofrece conectarla.
    texto = client.get("/panel/resultados?ver=anuncios").text
    assert "Conectar tu cuenta de anuncios" in texto and "Solo chats" in texto and "Gasto" not in texto
    client.portal.call(anuncios_meta.guardar, c, {**TOKEN, "cuenta": "act_9", "nombre": "Ads", "moneda": "MXN"})
    pedidos = []

    def manejar(request):
        pedidos.append(request)
        return httpx.Response(200, json={"data": [
            {"ad_id": "7", "ad_name": "Otoño 2x1", "spend": "300", "clicks": "80",
             "actions": [{"action_type": "omni_purchase", "value": "4"}],
             "action_values": [{"action_type": "omni_purchase", "value": "1800"}]},
            {"ad_id": "9", "ad_name": "Sin nada", "spend": "0", "clicks": "0"}]})

    monkeypatch.setattr(anuncios_meta.httpx, "AsyncClient", lambda **k: REAL(transport=httpx.MockTransport(manejar)))
    texto = client.get("/panel/resultados?ver=anuncios").text
    assert "/act_9/insights" in str(pedidos[0].url) and pedidos[0].url.params["level"] == "ad"
    assert set(json.loads(pedidos[0].url.params["time_range"])) == {"since", "until"}
    assert "Otoño 2x1" in texto and "$300 MXN" in texto and "4 · $1,800 MXN" in texto
    assert "1 · $450 MXN" in texto  # la venta por chat del anuncio 7
    assert "Solo chats" in texto and "Sin nada" not in texto  # sin gasto ni chats no se muestra
    assert "Descargar" not in texto
