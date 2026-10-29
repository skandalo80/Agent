"""Skandi: el botón de ayuda del panel, su límite diario y el aviso a soporte."""
import json
import os
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import conectar, panel, skandi  # noqa: E402
from test_conectar import setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402
from test_panel import ORIGEN, campos_con_nombre  # noqa: E402

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "skandi.md"


class FakeGoogle:
    def __init__(self):
        self.correos = []

    async def send_email(self, sender, sender_name, to, cc, subject, body, attachments=()):
        self.correos.append((to, subject, body))


def test_skandi_contesta_y_pasa_a_soporte(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    shutil.copy(PROMPT, "prompts/skandi.md")
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(skandi, "_chats", {})
    monkeypatch.setattr(skandi, "_actual", {})
    monkeypatch.setattr(skandi, "_avisos", {})
    monkeypatch.setattr(skandi, "MAX_PREGUNTAS_DIA", 3)
    respuestas = ["Ve a Campañas y toca Crear campaña.",
                  "Claro, el equipo de soporte se comunicará contigo pronto.\n[SOPORTE]"]
    vistos = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        vistos.append((prompt, system, model))
        return respuestas[len(vistos) - 1]

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)

    with TestClient(bot.app, base_url="https://testserver") as client:
        google = FakeGoogle()
        monkeypatch.setattr(conectar, "google", google)
        client.portal.call(bot.principal.store.set, "empresa", json.dumps(
            {"nombre": "Skandar", "descripcion": "Agentes de IA para Instagram <b>"}))
        assert client.post("/panel/ayuda", headers=ORIGEN, data={"mensaje": "hola"},
                           follow_redirects=False).headers["location"] == "/panel"  # sin sesión
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)

        # El botón sale en las páginas con menú, con la ventana cerrada (abre con #skandi).
        r = client.get("/panel/resultados")
        assert "Hola, soy Skandi ¿Cómo te puedo ayudar?" in r.text
        assert 'class="sk-boton" href="#skandi"' in r.text and 'id="skandi"' in r.text
        campos_con_nombre(r.text)

        assert client.post("/panel/ayuda", data={"mensaje": "hola"}).status_code == 403  # sin Origin
        r = client.post("/panel/ayuda", headers=ORIGEN, follow_redirects=False,
                        data={"mensaje": "¿Cómo creo una <campaña>?", "volver": "/panel/resultados?rango=30"})
        assert r.status_code == 303 and r.headers["location"] == "/panel/resultados?rango=30#skandi"
        prompt, system, model = vistos[0]
        assert model == skandi.MODELO and "Eres Skandi" in system
        # La información de empresa de la cuenta principal y los datos de quien pregunta, escapados.
        assert "Agentes de IA para Instagram &lt;b&gt;" in system and "@cafe.lunar" in system
        assert "¿Cómo creo una &lt;campaña&gt;?" in prompt
        html = client.get("/panel/resultados").text
        assert "¿Cómo creo una &lt;campaña&gt;?" in html and "Ve a Campañas y toca Crear campaña." in html
        assert google.correos == []

        # Pide una persona: se le dice que soporte se comunica y llega el correo con la conversación.
        r = client.post("/panel/ayuda", headers=ORIGEN, follow_redirects=False,
                        data={"mensaje": "Quiero hablar con alguien", "volver": "https://otro.sitio/panel"})
        assert r.headers["location"] == "/panel#skandi"  # solo regresa a páginas del panel
        assert "Cliente: ¿Cómo creo una &lt;campaña&gt;?" in vistos[1][0]
        html = client.get("/panel/resultados").text
        assert "soporte se comunicará contigo pronto" in html and "[SOPORTE]" not in html
        (para, asunto, cuerpo), = google.correos
        assert para == "skandar.carmach@gmail.com" and "@cafe.lunar" in asunto
        assert "Cliente: Quiero hablar con alguien" in cuerpo and "Plan:" in cuerpo

        # Una página que no abre con GET regresa al inicio del panel.
        assert skandi.volver_a(type("R", (), {"headers": {}, "app": bot.app})(), "/panel/salir") == "/panel"

        # Límite diario: la tercera pasa, la cuarta ya no llama a Claude.
        respuestas.append("Listo.")
        client.post("/panel/ayuda", headers=ORIGEN, data={"mensaje": "otra"})
        r = client.post("/panel/ayuda", headers=ORIGEN, data={"mensaje": "una más"})
        assert len(vistos) == 3
        assert "límite de preguntas de hoy" in r.text  # el aviso sale en la página a la que regresa

        # Historial: el chat quedó guardado; uno nuevo empieza vacío y el anterior se vuelve a abrir.
        chats, total = client.portal.call(bot.principal.store.chats_skandi, "panel", "cafe_lunar")
        assert total == 1 and chats[0]["usuario"] == "cafe.lunar" and chats[0]["soporte"]
        assert len(chats[0]["mensajes"]) == 6
        html = client.get("/panel/resultados").text
        assert "Conversaciones anteriores" in html and "¿Cómo creo una &lt;campaña&gt;?</b>" in html
        r = client.post("/panel/ayuda", headers=ORIGEN, data={"accion": "nueva", "volver": "/panel/resultados"})
        assert "Ve a Campañas" not in r.text.split('id="sk-mensajes"')[1]
        r = client.post("/panel/ayuda", headers=ORIGEN,
                        data={"accion": "abrir", "chat": str(chats[0]["id"]), "volver": "/panel/resultados"})
        assert "Ve a Campañas" in r.text.split('id="sk-mensajes"')[1]
        # Al reiniciar, el historial se vuelve a leer de la base.
        skandi._chats.clear()
        skandi._actual.clear()
        client.portal.call(skandi.cargar)
        html = client.get("/panel/resultados").text
        assert "Ve a Campañas" in html.split('id="sk-mensajes"')[1]

        # Minimizar solo la esconde; al cerrar (×), la siguiente pregunta empieza otra conversación.
        assert "data-minimizar-skandi" in html and "sk-ultimo" in html and 'name="nueva" value=""' in html
        monkeypatch.setattr(skandi, "MAX_PREGUNTAS_DIA", 10)
        respuestas.append("Claro, dime.")
        r = client.post("/panel/ayuda", headers=ORIGEN, data={"mensaje": "Otra duda", "nueva": "1"})
        assert "Ve a Campañas" not in r.text.split('id="sk-mensajes"')[1] and "Claro, dime." in r.text
        assert client.portal.call(bot.principal.store.chats_skandi, "panel", "cafe_lunar")[1] == 2

        # Los chats de todos solo los ve la cuenta principal.
        assert client.get("/panel/skandi").status_code == 404
        assert "Chats de Skandi" not in html


def test_skandi_en_la_web(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    shutil.copy(PROMPT.parent / "skandi-web.md", "prompts/skandi-web.md")
    setup(monkeypatch)
    monkeypatch.setattr(skandi, "_visitantes", {})
    monkeypatch.setattr(skandi, "MAX_WEB_VISITANTE", 2)
    respuestas = ["El Asistente virtual cuesta US$29 al mes.",
                  "Gracias, Ana. El equipo se comunicará contigo pronto.\n[SOPORTE]"]
    vistos = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        vistos.append((prompt, system, model))
        return respuestas[len(vistos) - 1]

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    web = {"Origin": "https://skandar.online"}
    with TestClient(bot.app, base_url="https://testserver") as client:
        google = FakeGoogle()
        monkeypatch.setattr(conectar, "google", google)
        r = client.get("/ayuda")
        # Se abre en el iframe de la web (mismo sitio) y no en otros.
        assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
        assert "Hola, soy Skandi ¿Cómo te puedo ayudar?" in r.text
        assert client.get("/ayuda/ayuda.js").status_code == 200

        assert client.post("/ayuda", data={"mensaje": "hola"}).status_code == 403  # sin Origin
        r = client.post("/ayuda", headers=web, data={"mensaje": "¿Cuánto <cuesta>?"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/ayuda#fin"
        prompt, system, model = vistos[0]
        assert model == skandi.MODELO and "página skandar.online" in system
        assert "¿Cuánto &lt;cuesta&gt;?" in prompt
        assert "US$29 al mes" in client.get("/ayuda").text and google.correos == []

        client.post("/ayuda", headers=web, data={"mensaje": "Soy Ana, ana@cafe.mx, quiero que me llamen"})
        html = client.get("/ayuda").text
        assert "se comunicará contigo pronto" in html and "[SOPORTE]" not in html
        (para, asunto, cuerpo), = google.correos
        assert para == skandi.SOPORTE_EMAIL and "Visitante: Soy Ana, ana@cafe.mx" in cuerpo

        # Límite por visitante (con su cookie); otro visitante empieza de cero.
        r = client.post("/ayuda", headers=web, data={"mensaje": "otra"})  # sigue la redirección
        assert len(vistos) == 2 and "ya no puedo contestar" in r.text
        # Cerrar (×) en la web: el iframe vuelve a cargar con ?nueva=1 y empieza otra conversación.
        assert "US$29" in client.get("/ayuda").text
        assert "US$29" not in client.get("/ayuda?nueva=1").text
        assert client.portal.call(bot.principal.store.chats_skandi, "web")[1] == 1  # la anterior sigue guardada
        client.cookies.clear()
        assert "US$29" not in client.get("/ayuda").text


def test_chats_de_skandi_para_la_cuenta_principal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    with TestClient(bot.app, base_url="https://testserver") as client:
        client.portal.call(bot.principal.store.set, "account_username", "skandar.online")
        guardar = bot.principal.store.guardar_skandi
        client.portal.call(guardar, None, "panel", "cafe_lunar", "cafe.lunar",
                           [["user", "¿Dónde cambio mi <logo>?"], ["assistant", "En Tu marca."]], False)
        web = client.portal.call(guardar, None, "web", "a" * 32, "",
                                 [["user", "Quiero una demo"], ["assistant", "Claro."]], True)
        vence = 9_999_999_999
        client.cookies.set(panel.COOKIE, f"negocio.{vence}.{panel._firma('negocio', vence)}")
        html = client.get("/panel/skandi").text
        assert "Chats de Skandi" in html  # en su menú de perfil
        assert "¿Dónde cambio mi &lt;logo&gt;?" in html and "@cafe.lunar" in html and "Visitante de la web" in html
        html = client.get("/panel/skandi?ver=soporte").text
        assert "Quiero una demo" in html and "cafe.lunar" not in html
        html = client.get(f"/panel/skandi?chat={web}").text
        assert '<div class="sk-msj skandi-dice">Claro.</div>' in html
