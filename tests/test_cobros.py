"""Conecta tu Mercado Pago y tu Stripe: links de pago con el código del chat y el aviso de cada pago."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx  # noqa: E402
import pytest  # noqa: E402

import test_contenido  # noqa: E402
from igbot import cobros, config  # noqa: E402
from test_contenido import ORIGEN  # noqa: E402

cliente = test_contenido.cliente
WHSEC = "whsec_prueba"
PRODUCTOS = [{"id": "p1", "nombre": "Bolsa Luna", "precio": "1,320.50", "moneda": "MXN"},
             {"id": "p2", "nombre": "Taller de café", "precio": "450", "desde": True, "moneda": "MXN"},
             {"id": "p3", "nombre": "Kit USD", "precio": "40", "moneda": "USD"}]


@pytest.fixture
def cobro(cliente, monkeypatch):
    """Las dos apps configuradas y Mercado Pago y Stripe de mentira."""
    for k, v in {"MP_CLIENT_ID": "mp1", "MP_CLIENT_SECRET": "mpsec", "STRIPE_CLIENT_ID": "ca_1",
                 "STRIPE_SECRET_KEY": "sk_test_1", "STRIPE_WEBHOOK_SECRET": WHSEC,
                 "PUBLIC_URL": "https://console.skandar.online"}.items():
        monkeypatch.setattr(config, k, v)
    falso = {"preferencias": [], "sesiones": [], "pagos": {}}

    def manejar(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://api.mercadopago.com/oauth/token":
            datos = json.loads(request.content)
            assert datos["client_secret"] == "mpsec" and datos["code"] == "c-mp"
            return httpx.Response(200, json={"access_token": "APP_USR-1", "refresh_token": "r1", "user_id": 77,
                                             "expires_in": 15552000})
        if url == "https://connect.stripe.com/oauth/token":
            assert request.headers["authorization"] == "Bearer sk_test_1"
            return httpx.Response(200, json={"stripe_user_id": "acct_9"})
        if url == "https://api.mercadopago.com/checkout/preferences":
            assert request.headers["authorization"] == "Bearer APP_USR-1"
            falso["preferencias"].append(json.loads(request.content))
            return httpx.Response(201, json={"init_point": "https://www.mercadopago.com.mx/checkout/v1/redirect?pref_id=1"})
        if url == "https://api.stripe.com/v1/checkout/sessions":
            assert request.headers["stripe-account"] == "acct_9"
            falso["sesiones"].append(dict(parse_qs(request.content.decode())))
            return httpx.Response(200, json={"url": "https://checkout.stripe.com/c/pay/cs_1"})
        if url.startswith("https://api.mercadopago.com/v1/payments/"):
            pago = falso["pagos"].get(url.rsplit("/", 1)[1])
            return httpx.Response(200, json=pago) if pago else httpx.Response(404)
        raise AssertionError(url)

    monkeypatch.setattr(cobros, "_transporte", httpx.MockTransport(manejar))
    client, c, *_ = cliente
    client.portal.call(c.store.set, "productos", json.dumps(PRODUCTOS))
    return client, c, falso


def conectar(client, prov, code):
    r = client.post(f"/panel/cobros/{prov}/conectar", headers=ORIGEN, follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    assert q["redirect_uri"] == f"https://console.skandar.online/panel/cobros/{prov}/callback"
    return client.get(f"/panel/cobros/{prov}/callback?code={code}&state={q['state']}", follow_redirects=False)


def test_sin_llaves_no_sale(cliente):
    client, c, *_ = cliente
    assert "Mercado Pago" not in client.get("/panel/conexion").text


def test_conectar_desde_conexion(cobro):
    client, c, _ = cobro
    r = client.get("/panel/conexion")
    assert "Conectar tu Mercado Pago</button>" in r.text and "Conectar tu Stripe</button>" in r.text
    assert client.post("/panel/cobros/mercadopago/conectar").status_code == 403  # sin Origin
    r = client.post("/panel/cobros/mercadopago/conectar", headers=ORIGEN, follow_redirects=False)
    assert r.headers["location"].startswith("https://auth.mercadopago.com/authorization?client_id=mp1")
    # Un state que no es el de la cookie no conecta.
    assert client.get("/panel/cobros/mercadopago/callback?code=c-mp&state=x",
                      follow_redirects=False).headers["location"] == "/panel/conexion?aviso=fallo"
    assert conectar(client, "mercadopago", "c-mp").headers["location"] == "/panel/conexion?aviso=conectado"
    assert client.portal.call(cobros.conexion, c, "mercadopago")["token"] == "APP_USR-1"
    assert conectar(client, "stripe", "c-st").headers["location"] == "/panel/conexion?aviso=conectado"
    assert client.portal.call(cobros.conexion, c, "stripe")["cuenta"] == "acct_9"
    r = client.get("/panel/conexion?aviso=conectado")
    assert "Tu asistente ya puede mandar links de pago" in r.text and "Desconectar</button>" in r.text
    assert client.get("/panel/cobros/stripe/callback?error=access_denied",
                      follow_redirects=False).headers["location"] == "/panel/conexion?aviso=cancelado"
    assert "Desconectamos tu cuenta de cobro" in client.post("/panel/cobros/stripe/desconectar", headers=ORIGEN).text
    assert client.portal.call(cobros.conexion, c, "stripe") == {}


def test_links_de_pago(cobro):
    client, c, falso = cobro
    assert client.portal.call(cobros.crear_link, c, "ana", "Bolsa Luna", 1) == "No hay una cuenta de cobro conectada."
    conectar(client, "mercadopago", "c-mp")
    conectar(client, "stripe", "c-st")
    codigo = client.portal.call(c.store.codigo_chat, "ana")
    texto = client.portal.call(cobros.crear_link, c, "ana", "bolsa luna", 2)
    assert texto == ("Link de pago de 2 × Bolsa Luna (total $2,641.00 MXN): "
                     "https://www.mercadopago.com.mx/checkout/v1/redirect?pref_id=1")
    pref = falso["preferencias"][0]
    assert pref["external_reference"] == codigo and pref["items"][0]["unit_price"] == 1320.5
    assert pref["notification_url"] == f"https://console.skandar.online/cobros/mercadopago/aviso?c={c.slug}"
    # En dólares va por Stripe, con el código del chat.
    assert client.portal.call(cobros.crear_link, c, "ana", "Kit", 1).endswith("https://checkout.stripe.com/c/pay/cs_1")
    s = falso["sesiones"][0]
    assert s["client_reference_id"] == [codigo] and s["line_items[0][price_data][unit_amount]"] == ["4000"]
    # Sin precio fijo o sin producto: no hay link.
    assert "no tiene un precio fijo" in client.portal.call(cobros.crear_link, c, "ana", "Taller de café", 1)
    assert "No encontré ese producto" in client.portal.call(cobros.crear_link, c, "ana", "Zapatos", 1)
    assert [t.name for t in cobros.herramientas(c, "ana")] == ["link_de_pago"]


def test_aviso_de_mercado_pago(cobro):
    client, c, falso = cobro
    conectar(client, "mercadopago", "c-mp")
    codigo = client.portal.call(c.store.codigo_chat, "ana")
    client.portal.call(c.store.add_incoming, "ana", "m1", "¿Cómo pago?")
    client.portal.call(c.store.guardar_anuncio, "ana", "120211", "Bolsas de otoño", "")
    falso["pagos"]["555"] = {"status": "approved", "external_reference": codigo, "transaction_amount": 2641,
                             "currency_id": "MXN", "additional_info": {"items": [{"title": "Bolsa Luna", "quantity": "2"}]}}
    falso["pagos"]["556"] = {"status": "approved", "external_reference": "otra-cosa", "transaction_amount": 10,
                             "currency_id": "MXN"}
    url = f"/cobros/mercadopago/aviso?c={c.slug}"
    assert client.post(url + "&type=payment&data.id=555").status_code == 200
    client.post(url, json={"type": "payment", "data": {"id": "555"}})  # el mismo, otra vez
    client.post(url + "&type=payment&data.id=556")  # un pago que no es del asistente
    client.post("/cobros/mercadopago/aviso?c=nadie&type=payment&data.id=555")
    ventas = client.portal.call(c.store.ventas_de, "ana")
    assert [(v["origen"], v["externo"], v["ad_id"], v["monto"]) for v in ventas] == [("mercadopago", "555", "120211", 2641)]
    r = client.get("/panel/asistente?ver=bot&chat=ana")
    assert "Compró $2,641 MXN" in r.text and "en tu tienda" not in r.text
    falso["pagos"]["555"]["status"] = "refunded"
    client.post(url + "&type=payment&data.id=555")
    assert client.portal.call(c.store.ventas_de, "ana") == []


def test_aviso_de_stripe(cobro):
    client, c, _ = cobro
    conectar(client, "stripe", "c-st")
    codigo = client.portal.call(c.store.codigo_chat, "ana")
    evento = {"type": "checkout.session.completed", "account": "acct_9", "data": {"object": {
        "id": "cs_1", "payment_status": "paid", "client_reference_id": codigo, "amount_total": 4000, "currency": "usd"}}}
    cuerpo = json.dumps(evento).encode()
    t = str(int(time.time()))
    firma = hmac.new(WHSEC.encode(), f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    assert client.post("/cobros/stripe/aviso", content=cuerpo,
                       headers={"Stripe-Signature": f"t={t},v1={'0' * 64}"}).status_code == 400
    viejo = str(int(time.time()) - 3600)
    assert client.post("/cobros/stripe/aviso", content=cuerpo, headers={"Stripe-Signature": f"t={viejo},v1={firma}"}
                       ).status_code == 400
    assert client.post("/cobros/stripe/aviso", content=cuerpo,
                       headers={"Stripe-Signature": f"t={t},v1={firma}"}).status_code == 200
    ventas = client.portal.call(c.store.ventas_de, "ana")
    assert [(v["origen"], v["externo"], v["monto"], v["moneda"]) for v in ventas] == [("stripe", "cs_1", 40.0, "USD")]
    assert "Gracias por tu pago" in client.get("/cobros/gracias").text
