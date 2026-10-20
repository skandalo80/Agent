"""Panel del cliente: alta sola al conectarse, sesión, instrucciones y pausa."""
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import panel  # noqa: E402
from test_conectar import FakeHTTP, setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402

ORIGEN = {"Origin": "https://bot.skandar.online"}


def test_generar_arma_las_instrucciones_con_el_tono():
    texto = panel.generar("cafe.lunar", {"negocio": "Cafetería en Puebla", "precios": "Latte $55", "tono": "formal"})
    assert texto.startswith("Eres el asistente de Instagram de @cafe.lunar.")
    assert "Cafetería en Puebla" in texto and "Latte $55" in texto and "usted" in texto
    assert "HORARIOS" not in texto  # lo que no llenó no sale
    assert "pasar_a_humano" in texto and "No inventes precios" in texto
    assert "Tuteas" in panel.generar("x", {})  # sin tono: amigable


def test_preguntas_en_pares_y_politicas_por_tema():
    texto = panel.generar("cafe.lunar", {
        "faq": [{"p": "¿Hacen envíos?", "r": "Sí, a todo México."}, {"p": "¿Sin respuesta?", "r": ""}],
        "envios": "Gratis desde $500", "pagos": "Transferencia", "cambios": "",
    })
    assert "PREGUNTAS FRECUENTES\n- ¿Hacen envíos?\n  Sí, a todo México." in texto
    assert "Sin respuesta" not in texto  # una pregunta sin respuesta no le sirve al asistente
    assert "POLÍTICAS\n- Envíos: Gratis desde $500\n- Formas de pago: Transferencia" in texto
    assert "Cambios" not in texto
    # Perfiles de antes (texto libre) se siguen leyendo.
    viejo = panel.generar("x", {"faq": "¿Abren domingo? No.", "politicas": "Sin cambios."})
    assert "- ¿Abren domingo? No." in viejo and "- Otra política: Sin cambios." in viejo


def test_sesion_firmada_y_con_vencimiento(monkeypatch):
    class R:
        def __init__(self, valor):
            self.cookies = {panel.COOKIE: valor}

    vence = 9_999_999_999
    assert panel.sesion(R(f"1784.{vence}.{panel._firma('1784', vence)}")) == "1784"
    assert panel.sesion(R(f"9999.{vence}.{panel._firma('1784', vence)}")) is None  # otra cuenta
    assert panel.sesion(R(f"1784.100.{panel._firma('1784', 100)}")) is None  # vencida
    assert panel.sesion(R("basura")) is None


def test_cliente_se_conecta_configura_y_enciende(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    avisos = setup(monkeypatch)
    FakeHTTP.calls.clear()
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")

    with TestClient(bot.app, base_url="https://testserver") as client:
        assert "Entrar con Instagram" in client.get("/panel").text  # sin sesión

        client.post("/conectar/solicitud", data={"usuario": "cafe.lunar", "nombre": "Ana", "correo": "ana@cafe.mx"})
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert r.headers["location"] == "https://bot.skandar.online/panel"
        assert any("en pausa" in t for _, t in avisos)

        # Se dio de alta sola: Básico (Haiku, sin tu Google) y avisos al correo del formulario.
        env = Path("clientes/cafe_lunar/.env").read_text()
        assert "IG_ACCOUNT_ID=1784" in env and "CLAUDE_MODEL=claude-haiku-5-5" in env
        assert "GOOGLE_TOOLS=0" in env and "OWNER_EMAIL=ana@cafe.mx" in env
        c = bot.buscar("1784")
        assert c and not c.google_tools

        r = client.get("/panel")
        assert "@cafe.lunar" in r.text and "en pausa" in r.text

        # Mientras está en pausa, sus DMs no se contestan.
        body = json.dumps({"entry": [{"id": "1784", "messaging": [{
            "sender": {"id": "beto"}, "recipient": {"id": "1784"}, "message": {"mid": "m1", "text": "hola"},
        }]}]}).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        client.post("/webhook", content=body, headers={"X-Hub-Signature-256": f"sha256={sig}"})
        assert client.portal.call(c.store.pending_users) == []

        # Agregar y quitar preguntas solo redibuja (no guarda) y no pierde lo escrito.
        r = client.post("/panel/guardar", headers=ORIGEN, data={
            "faq_n": "3", "faq_p_0": "¿Envíos?", "faq_r_0": "Sí", "faq_p_1": "¿Factura?", "faq_r_1": "Sí",
            "accion": "agregar"})
        assert 'name="faq_p_3"' in r.text and "¿Factura?" in r.text and 'name="faq_p_4"' not in r.text
        r = client.post("/panel/guardar", headers=ORIGEN, data={
            "faq_n": "4", "faq_p_0": "¿Envíos?", "faq_r_0": "Sí", "faq_p_1": "¿Factura?", "faq_r_1": "Sí",
            "accion": "quitar_0"})
        assert "¿Envíos?" not in r.text and "¿Factura?" in r.text
        assert client.portal.call(panel.perfil, c) == {}

        datos = {"negocio": "Cafetería en Puebla", "horarios": "9 a 21", "tono": "profesional"}
        assert client.post("/panel/guardar", data={**datos, "accion": "generar"}).status_code == 403  # sin Origin
        assert client.post("/panel/guardar", headers={"Origin": "https://otro.com", "Sec-Fetch-Site": "cross-site"},
                           data={**datos, "accion": "generar"}).status_code == 403
        r = client.post("/panel/guardar", headers=ORIGEN, data={**datos, "accion": "generar"})
        assert "Cafetería en Puebla" in r.text and "Revisa las instrucciones" in r.text
        assert "Cafetería" not in Path(c.prompt_path).read_text()  # generar no guarda

        r = client.post("/panel/guardar", headers=ORIGEN,
                        data={**datos, "accion": "guardar", "prompt": "Eres el asistente de @cafe.lunar. <b>"})
        assert "Guardado" in r.text and "&lt;b&gt;" in r.text
        assert Path(c.prompt_path).read_text() == "Eres el asistente de @cafe.lunar. <b>\n"

        r = client.post("/panel/estado", headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
                        data={"activo": "1"})
        assert "está contestando" in r.text
        assert client.portal.call(panel.activo, c)

        # Otro navegador sin sesión no puede tocar nada.
        otro = TestClient(bot.app, base_url="https://testserver")
        r = otro.post("/panel/estado", headers=ORIGEN, data={"activo": "0"}, follow_redirects=False)
        assert r.status_code == 303 and client.portal.call(panel.activo, c)
