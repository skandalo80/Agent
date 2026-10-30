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
    assert destino.hostname == "www.facebook.com" and q["scope"] == anuncios_meta.SCOPES
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


def test_negocio_de_servicios_ve_cotizaciones_citas_y_ventas_a_mano(con_facebook, monkeypatch):
    """Un spa o una tienda de cortinas no vende en su página: ve prospectos de Meta, cotizaciones y citas."""
    from igbot import panel, tools as t
    client, c, *_ = con_facebook
    for u in ("u1", "u2", "u3"):
        client.portal.call(c.store.guardar_anuncio, u, "7", "Cortinas a la medida", "")
        client.portal.call(c.store.add_incoming, u, f"m-{u}", "hola")
    _, anotar, _ = t.human_tools(c.store, None, "u1")
    with panel.usar(c):
        client.portal.call(anotar.handler, {"tipo": "visita", "detalle": "Medir 3 ventanas"})
    client.portal.call(c.store.add_event, "u2", "interes", {"tipo": "cotizacion"})
    client.portal.call(c.store.add_event, "u2", "cita", {"id": "ev1"})
    client.portal.call(c.store.add_meeting, "ev1", "u2", "2026-11-02T10:00:00-06:00", "", "")
    client.portal.call(c.store.add_event, "u3", "cita", {"id": "ev2"})
    client.portal.call(c.store.add_meeting, "ev2", "u3", "2026-11-03T10:00:00-06:00", "", "")
    client.portal.call(c.store.cancel_meeting, "ev2")  # la cancelada no cuenta
    # El dueño marca la venta que cobró en persona.
    r = client.post("/panel/venta", headers=ORIGEN, data={"usuario": "u2", "monto": "8,500", "moneda": "MXN",
                                                          "accion": "marcar"})
    assert "Ya cuenta para el anuncio" in r.text and "Vendido $8,500 MXN" in r.text
    assert "Escribe por cuánto" in client.post("/panel/venta", headers=ORIGEN,
                                                data={"usuario": "u2", "monto": "abc"}).text
    client.portal.call(anuncios_meta.guardar, c, {**TOKEN, "cuenta": "act_9", "nombre": "Ads", "moneda": "MXN"})
    datos = {"data": [{"ad_id": "7", "ad_name": "Cortinas a la medida", "spend": "900", "clicks": "120",
                       "actions": [{"action_type": "lead", "value": "6"}]}]}
    monkeypatch.setattr(anuncios_meta.httpx, "AsyncClient",
                        lambda **k: REAL(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=datos))))
    texto = client.get("/panel/resultados?ver=anuncios").text
    for columna in ("Prospectos en Meta", "Pidieron cotización", "Agendaron cita", "Ventas por chat"):
        assert f"<th>{columna}</th>" in texto
    assert "Compras en tu página" not in texto and "Citas en tu página" not in texto  # sin datos, no salen
    assert "<td>6</td><td>3</td><td>2</td><td>1</td><td>1 · $8,500 MXN</td>" in texto
    # Se puede quitar la venta marcada a mano.
    externo = client.portal.call(c.store.ventas_de, "u2")[0]["externo"]
    client.post("/panel/venta", headers=ORIGEN, data={"usuario": "u2", "externo": externo, "accion": "quitar"})
    assert client.portal.call(c.store.ventas_de, "u2") == []


def test_avisa_a_meta_cotizacion_cita_y_venta_del_chat(con_facebook, monkeypatch):
    """Conversions API for Business Messaging: lo que pasa en el chat le llega a Meta, ligado a la persona."""
    from igbot import conversiones_meta as cm
    client, c, *_ = con_facebook
    assert not client.portal.call(cm.mandar, c, "u1", "interes")  # sin cuenta de anuncios no se manda nada
    client.portal.call(anuncios_meta.guardar, c, {**TOKEN, "cuenta": "act_9", "moneda": "MXN"})
    client.portal.call(c.store.set, "fb_page_id", "pag1")
    pedidos = []

    def manejar(request):
        pedidos.append(request)
        if request.url.path.endswith("/dataset"):
            return httpx.Response(200, json={"id": f"ds-{request.url.path.split('/')[-2]}"})
        return httpx.Response(200, json={"events_received": 1})

    monkeypatch.setattr(cm.httpx, "AsyncClient", lambda **k: REAL(transport=httpx.MockTransport(manejar)))
    assert client.portal.call(cm.mandar, c, "u1", "interes")
    assert not client.portal.call(cm.mandar, c, "u1", "interes")  # la segunda cotización no es otro prospecto
    assert client.portal.call(cm.mandar, c, "fb:psid9", "venta", 8500.0, "mxn", "manual:abc")
    ig_ds, ig_ev, fb_ds, fb_ev = pedidos
    assert ig_ds.url.path.endswith(f"/{c.ig_account_id}/dataset") and fb_ds.url.path.endswith("/pag1/dataset")
    evento = json.loads(ig_ev.content)["data"][0]
    assert ig_ev.url.path.endswith(f"/ds-{c.ig_account_id}/events")
    assert (evento["event_name"], evento["action_source"], evento["messaging_channel"]) == (
        "LeadSubmitted", "business_messaging", "instagram")
    assert evento["user_data"] == {"instagram_business_account_id": c.ig_account_id, "ig_sid": "u1"}
    evento = json.loads(fb_ev.content)["data"][0]
    assert evento["user_data"] == {"page_id": "pag1", "page_scoped_user_id": "psid9"}
    assert evento["custom_data"] == {"currency": "MXN", "value": 8500.0} and evento["event_id"] == "manual:abc"
    guardado = client.portal.call(anuncios_meta.conexion, c)
    assert guardado["dataset_instagram"] == f"ds-{c.ig_account_id}" and guardado["dataset_messenger"] == "ds-pag1"
    # Sin permiso: se anota y sale en Tu cuenta de anuncios.
    monkeypatch.setattr(cm.httpx, "AsyncClient", lambda **k: REAL(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"message": "Missing permission instagram_manage_events"}}))))
    assert not client.portal.call(cm.mandar, c, "u2", "cita")
    assert "Missing permission" in client.get("/panel/anuncios").text
