"""Cotización en PDF con los productos de Tu empresa, encendida desde Configura tu asistente (sin red)."""
import json
import sys
from pathlib import Path

import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import config, cotizacion, tools  # noqa: E402
from test_tools import NOW, FakeGoogle, text  # noqa: E402

PRODUCTOS = [
    {"id": "a1", "nombre": "Pastel de chocolate", "precio": "450", "moneda": "MXN"},
    {"id": "a2", "nombre": "Cupcake", "precio": "$1,250.50", "moneda": "MXN"},
    {"id": "a3", "nombre": "Pastel de bodas", "precio": "3000", "moneda": "MXN", "desde": True},
    {"id": "a4", "nombre": "Mesa de postres", "precio": "a cotizar", "moneda": "MXN"},
    {"id": "a5", "nombre": "Curso en línea", "precio": "20", "moneda": "USD"},
]


class FakeInstagram:
    def __init__(self):
        self.files = []

    async def send_file(self, user_id, url):
        self.files.append((user_id, url))


def test_solo_cotiza_precios_fijos_y_hace_las_cuentas():
    assert [cotizacion.precio_fijo(p) for p in PRODUCTOS] == [450.0, 1250.5, None, None, 20.0]
    pedido = cotizacion.calcular(PRODUCTOS, [{"producto": "pastel de chocolate", "cantidad": 2},
                                             {"producto": "Cupcake", "cantidad": 3}])
    assert pedido.total == 900 + 3751.5 and pedido.moneda == "MXN"
    assert "Total: $4,651.50 MXN" in pedido.texto()
    for items, motivo in (([{"producto": "Pastel de bodas", "cantidad": 1}], "precio fijo"),
                          ([{"producto": "Mesa de postres", "cantidad": 1}], "precio fijo"),
                          ([{"producto": "Cupcake", "cantidad": 0}], "entre 1"),
                          ([{"producto": "Cupcake", "cantidad": 1}, {"producto": "Curso", "cantidad": 1}], "monedas"),
                          ([], "Falta")):
        with pytest.raises(ValueError, match=motivo):
            cotizacion.calcular(PRODUCTOS, items)


def test_ajustes_del_formulario():
    assert cotizacion.del_form({"q_on": "1", "q_dias": "30", "q_condiciones": " IVA incluido. "}) == {
        "on": True, "dias": 30, "condiciones": "IVA incluido."}
    assert cotizacion.del_form({"q_dias": "99"}) == {"on": False, "dias": 15, "condiciones": ""}
    assert cotizacion.resumen({"cotizacion": {"on": True, "dias": 7}}) == "Manda cotizaciones en PDF (valen 7 días)"
    assert cotizacion.resumen({}) == ""


@pytest.fixture
def cuenta_con_productos(cuenta_de_prueba, monkeypatch, tmp_path):
    c, correr = cuenta_de_prueba
    monkeypatch.setattr(tools, "now", lambda: NOW)
    monkeypatch.setattr(cotizacion, "now", lambda: NOW)
    monkeypatch.setattr(config, "MAIL_FROM", "yo@gmail.com")
    monkeypatch.setattr(c, "quotes_dir", str(tmp_path))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")
    correr(c.store.set("productos", json.dumps(PRODUCTOS)))
    correr(c.store.set("empresa", json.dumps({"nombre": "Pastelería Lunar", "sitio_web": "lunar.mx"})))
    correr(c.store.set("marca", json.dumps({"principal": "#AA3366"})))
    correr(c.store.set("account_username", "pasteleria.lunar"))
    correr(c.store.add_incoming("ana", "m1", "mi correo es ana@mail.com"))
    return c, correr


def herramientas(c, google):
    ig, state = FakeInstagram(), {}
    return ig, state, {t.name: t.handler for t in cotizacion.herramientas(c, c.store, google, ig, "ana", state)}


def test_se_enciende_solo_con_el_interruptor_y_productos(cuenta_con_productos):
    c, correr = cuenta_con_productos
    assert not correr(cotizacion.encendida(c))
    correr(c.store.set("perfil", json.dumps({"cotizacion": {"on": True}})))
    assert correr(cotizacion.encendida(c))
    correr(c.store.set("productos", json.dumps([PRODUCTOS[2]])))  # solo "Desde": nada que cotizar
    assert not correr(cotizacion.encendida(c))


def test_sin_google_va_solo_por_el_chat(cuenta_con_productos, tmp_path):
    c, correr = cuenta_con_productos
    correr(c.store.set("perfil", json.dumps({"cotizacion": {"on": True, "dias": 7, "condiciones": "Anticipo 50 %."}})))
    ig, state, t = herramientas(c, None)
    args = {"items": [{"producto": "Cupcake", "cantidad": 2}], "nombre": "Ana", "empresa": "", "email": ""}
    assert "Total: $2,501.00 MXN" in text(correr(t["cotizar"]({"items": args["items"]})))
    r = correr(t["enviar_cotizacion"]({**args, "canal": "correo", "email": "ana@mail.com"}))
    assert r.get("is_error") and "por este chat" in text(r)
    r = correr(t["enviar_cotizacion"]({**args, "canal": "chat"}))
    assert "COT-0001" in text(r) and "por este chat" in text(r) and state["acted"]
    user, url = ig.files[0]
    token = url.rsplit("/", 1)[1].removesuffix(".pdf")
    assert user == "ana" and (tmp_path / f"{token}.pdf").read_bytes().startswith(b"%PDF")
    assert correr(c.store.etapas_de(["ana"])) == {"ana": "cotizo"}
    with TestClient(bot.app) as client:
        assert client.get(f"/cotizaciones/{token}.pdf").content.startswith(b"%PDF")
        assert client.get("/cotizaciones/..%2F..%2Fbot.pdf").status_code == 404


def test_con_google_tambien_por_correo(cuenta_con_productos):
    c, correr = cuenta_con_productos
    correr(c.store.set("perfil", json.dumps({"cotizacion": {"on": True}})))
    google = FakeGoogle()
    ig, state, t = herramientas(c, google)
    args = {"items": [{"producto": "Pastel de chocolate", "cantidad": 1}], "nombre": "", "empresa": "", "canal": "ambos"}
    r = correr(t["enviar_cotizacion"]({**args, "email": "otra@mail.com"}))
    assert r.get("is_error") and not google.sent  # un correo que no escribió
    r = correr(t["enviar_cotizacion"]({**args, "email": "ana@mail.com"}))
    assert "por correo a ana@mail.com y por este chat" in text(r)
    assert google.sent[0][3] == "Cotización COT-0001 · Pastelería Lunar"
    assert google.attachments[0][1].startswith(b"%PDF") and len(ig.files) == 1


def test_el_panel_la_configura_en_reglas_y_limites(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlparse

    from test_conectar import setup
    from test_cuenta import FakeInstagram as IGFalso
    from test_panel import ORIGEN, campos_con_nombre

    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: IGFalso())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")

    with TestClient(bot.app, base_url="https://testserver") as client:
        url = "/panel/asistente/configura?paso=reglas"
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        r = client.get(url)
        assert "Manda cotizaciones en PDF" in r.text and "Aún no tienes productos con precio fijo" in r.text
        campos_con_nombre(r.text)
        client.portal.call(c.store.set, "productos", json.dumps(PRODUCTOS))
        assert "Aún no tienes productos" not in client.get(url).text
        datos = {"paso": "reglas", "q": "1", "q_on": "1", "q_dias": "30", "q_condiciones": "IVA incluido.",
                 "accion": "aplicar"}
        assert client.post("/panel/asistente/paso", data=datos).status_code == 403  # sin Origin
        assert client.post("/panel/asistente/paso", headers=ORIGEN, data=datos, follow_redirects=False).status_code == 303
        guardado = json.loads(client.portal.call(c.store.get, "perfil"))["cotizacion"]
        assert guardado == {"on": True, "dias": 30, "condiciones": "IVA incluido."}
        assert 'name="q_dias" value="30" checked' in client.get(url).text
        assert "Manda cotizaciones en PDF (valen 30 días)" in client.get("/panel/asistente/configura?paso=resumen").text


def test_el_catalogo_de_antes_pasa_a_tu_empresa():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import catalogo_a_tu_empresa as script

    cat = yaml.safe_load("""
moneda: USD
vigencia_dias: 15
meses_pagados_en_anual: 10
paquetes:
  basico: {nombre: Asistente virtual, resumen: contesta tus DMs, mensualidad: 49, respuestas_mes: 1500,
           incluye: [Responde 24/7]}
extras:
  respuestas: {descripcion: Bloque de 500 respuestas, bloque: 500, precio: 15, max_bloques: 20}
condiciones: [Los precios no incluyen IVA., Sin costo de instalación.]
demo:
  giros: {gimnasio: horarios y membresías}
""")
    productos, ajustes, demo = script.convertir(cat)
    assert [(p["nombre"], p["precio"], p["moneda"]) for p in productos] == [
        ("Asistente virtual, pago mensual", "49", "USD"), ("Asistente virtual, pago anual", "490", "USD"),
        ("Bloque de 500 respuestas", "15", "USD")]
    assert all(cotizacion.precio_fijo(p) for p in productos)
    assert ajustes == {"on": True, "dias": 15, "condiciones": "Los precios no incluyen IVA. Sin costo de instalación."}
    assert "gimnasio: horarios y membresías" in demo
