"""Panel del cliente: alta sola al conectarse, sesión, instrucciones y pausa."""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import panel  # noqa: E402
from test_conectar import FakeHTTP, FakeResponse, setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402

ORIGEN = {"Origin": "https://console.skandar.online"}
REVISAR = panel.revisar_instrucciones  # la de verdad (conftest la cambia por una que aprueba todo)


def campos_con_nombre(html: str) -> None:
    """Sin ids repetidos y cada campo con su etiqueta (si no, Chrome lo marca en Issues)."""
    ids = re.findall(r' id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    etiquetados = set(re.findall(r'<label for="([^"]+)"', html))
    for campo in re.findall(r"<(?:textarea|select|input)\b[^>]*>", html):
        if 'type="hidden"' in campo or "aria-label=" in campo or re.search(r'type="(radio|checkbox)"', campo):
            continue
        id_ = re.search(r' id="([^"]+)"', campo)
        assert id_ and id_.group(1) in etiquetados, campo


def test_404_sin_sesion():
    with TestClient(bot.app, base_url="https://testserver") as client:
        # El ícono de la pestaña (el de skandar.online) y la raíz, que lleva al panel.
        r = client.get("/favicon.ico")
        assert r.status_code == 200 and r.headers["content-type"] == "image/x-icon"
        assert client.get("/apple-touch-icon.png").headers["content-type"] == "image/png"
        assert client.get("/", follow_redirects=False).headers["location"] == "/panel"
        assert 'rel="icon" href="/favicon.ico"' in client.get("/panel").text
        assert client.get("/privacy", follow_redirects=False).headers["location"] == "https://skandar.online/privacidad"
        r = client.get("/panel/contenido/nada")
        assert r.status_code == 404 and "Esta página no existe" in r.text and "Entrar a mi panel" in r.text
        assert client.get("/otra-cosa").json() == {"detail": "Not Found"}  # fuera del panel, igual que antes


def test_generar_arma_las_instrucciones_con_el_tono():
    texto = panel.generar("cafe.lunar", {"negocio": "Cafetería en Puebla", "precios": "Latte $55", "tono": "formal"})
    assert texto.startswith("Eres el asistente de Instagram de @cafe.lunar.")
    assert "Cafetería en Puebla" in texto and "Latte $55" in texto and "usted" in texto
    assert "HORARIOS" not in texto  # lo que no llenó no sale
    assert "pasar_a_humano" in texto and "No inventes precios" in texto
    assert "Tuteas" in panel.generar("x", {})  # sin tono: amigable
    # El tono de antes sigue sirviendo hasta que elija personalidad.
    assert "Cercano y amable" in panel.generar("x", {"tono": "amigable"}) and "algún emoji" in panel.generar("x", {})


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
    claude = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        claude.append((prompt, system))
        return "- Saluda con calidez y contesta en pocas frases."

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)

    with TestClient(bot.app, base_url="https://testserver") as client:
        assert "Entrar con Instagram" in client.get("/panel").text  # sin sesión

        client.post("/conectar/solicitud", data={"usuario": "cafe.lunar", "nombre": "Ana", "correo": "ana@cafe.mx"})
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert r.headers["location"] == "https://console.skandar.online/panel"
        assert any("en pausa" in t for _, t in avisos)

        # Se dio de alta sola: Básico (Haiku, sin tu Google) y avisos al correo del formulario.
        env = Path("clientes/cafe_lunar/.env").read_text()
        assert "IG_ACCOUNT_ID=1784" in env and "CLAUDE_MODEL=claude-haiku-5-5" in env
        assert "GOOGLE_TOOLS=0" in env and "PLAN=basico" in env and "OWNER_EMAIL=ana@cafe.mx" in env
        c = bot.buscar("1784")
        assert c and not c.google_tools

        async def con_empresa(c):
            return True

        monkeypatch.setattr(panel, "empresa_lista", con_empresa)  # ya llenó su Perfil de empresa
        r = client.get("/panel")  # la primera vez: la bienvenida, ya en su paso 2
        assert "¿Por dónde seguimos?" in r.text and "Configurar tu asistente" not in r.text and "Empezar" in r.text
        r = client.get("/panel")
        assert "@cafe.lunar" in r.text and "en pausa" in r.text

        # Mientras está en pausa, sus DMs no se contestan.
        body = json.dumps({"entry": [{"id": "1784", "messaging": [{
            "sender": {"id": "beto"}, "recipient": {"id": "1784"}, "message": {"mid": "m1", "text": "hola"},
        }]}]}).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        client.post("/webhook", content=body, headers={"X-Hub-Signature-256": f"sha256={sig}"})
        assert client.portal.call(c.store.pending_users) == []

        # Sin nada guardado, Configura empieza por el primer paso y cada paso pasa la revisión de campos.
        r = client.get("/panel/asistente/configura")
        assert 'name="paso" value="personalidad"' in r.text and "0 de 4 listos" in r.text
        for paso in ("personalidad", "escribe", "reglas", "equipo", "resumen"):
            campos_con_nombre(client.get(f"/panel/asistente/configura?paso={paso}").text)
        # Lo del negocio ya no se llena aquí (Perfil de empresa); lo de antes en Configura se sigue usando.
        assert 'name="negocio"' not in r.text and 'href="/panel/empresa"' in client.get(
            "/panel/asistente/configura?paso=resumen").text
        client.portal.call(c.store.set, "perfil", json.dumps({"negocio": "Cafetería en Puebla"}))

        datos = {"paso": "personalidad", "personalidad": "cercano"}
        assert client.post("/panel/asistente/paso", data=datos).status_code == 403  # sin Origin
        assert client.post("/panel/asistente/paso", headers={"Origin": "https://otro.com", "Sec-Fetch-Site": "cross-site"},
                           data=datos).status_code == 403

        # Personalidad: hay que elegir una, y trae su forma de escribir.
        n = len(claude)
        r = client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad"})
        assert "Elige una personalidad" in r.text
        r = client.get("/panel/asistente/configura?paso=personalidad")
        assert r.text.count('name="personalidad"') == 6 and "Elegante y premium" in r.text and "Guardado." not in r.text
        r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False,
                        data={"paso": "personalidad", "personalidad": "cercano"})
        assert r.headers["location"] == "/panel/asistente/configura?paso=escribe&ok=1"
        assert "Cafetería en Puebla" in Path(c.prompt_path).read_text()
        # Aplicar guarda y se queda en el paso.
        r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False,
                        data={"paso": "personalidad", "personalidad": "cercano", "accion": "aplicar"})
        assert r.headers["location"] == "/panel/asistente/configura?paso=personalidad&ok=aplicado"
        r = client.get(r.headers["location"]).text
        assert "Cambios aplicados." in r and 'value="aplicar">Aplicar</button>' in r
        assert ">Siguiente: cómo escribe →</button>" in r
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "premium"})
        r = client.get("/panel/asistente/configura?paso=escribe")
        assert 'id="trato-usted" name="trato" value="usted" checked' in r.text and "Así sonaría" in r.text
        prompt = Path(c.prompt_path).read_text()
        assert "Elegante y premium" in prompt and "Hablas de usted" in prompt and "No usas emojis." in prompt
        client.post("/panel/asistente/paso", headers=ORIGEN,
                    data={"paso": "escribe", "trato": "tu", "largo": "breve", "emojis": "raro"})
        prompt = Path(c.prompt_path).read_text()
        assert "Tuteas" in prompt and "una o dos frases" in prompt and "No usas emojis." in prompt  # lo raro no cambia

        # Reglas y límites: interruptores y lo que escribe el dueño (escapado y entre etiquetas).
        r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False, data={
            "paso": "reglas", "r_preguntar": "1", "r_descuentos": "1", "no_hacer": "No hablar de <b>la competencia</b>",
        })
        assert r.headers["location"] == "/panel/asistente/configura?paso=equipo&ok=1"
        prompt = Path(c.prompt_path).read_text()
        assert "haces una o dos preguntas" in prompt and "invitas al siguiente paso" not in prompt
        assert "No empujas a comprar, cotizar" in prompt  # apagada también se dice: si no, vende por costumbre
        assert "unas 25 palabras como máximo" in prompt
        assert "descuentos y promociones que aparecen" in prompt
        assert "<lo_que_no_debes_hacer>\nNo hablar de &lt;b&gt;la competencia&lt;/b&gt;\n" in prompt
        assert "Cafetería en Puebla" in prompt
        assert len(claude) == n  # elegir y escribir no llama a Claude (la revisión de prueba aprueba todo)

        # Resumen con Editar a cada paso y el botón para encenderlo.
        r = client.get("/panel/asistente/configura?paso=resumen")
        assert "Elegante y premium" in r.text and "Pregunta antes de recomendar" in r.text
        assert 'href="/panel/asistente/configura?paso=reglas">Editar' in r.text and "Encender tu asistente" in r.text
        assert "3 de 4 listos" in r.text
        # La principal ve su formulario de siempre; aquí el de antes ya no guarda.
        assert client.post("/panel/guardar", headers=ORIGEN, data={"accion": "guardar"},
                           follow_redirects=False).headers["location"] == "/panel/asistente/configura"

        r = client.post("/panel/estado", headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
                        data={"activo": "1"})
        assert "está contestando" in r.text
        assert client.portal.call(panel.activo, c)
        # Encendido: Conversaciones ya no lleva estado ni Pausar; se pausa desde el Resumen y vuelve ahí.
        assert "está en pausa" not in client.get("/panel/asistente").text
        assert "Pausar tu asistente" in client.get("/panel/asistente/configura?paso=resumen").text
        r = client.post("/panel/estado", headers=ORIGEN, data={"activo": "0", "volver": "configura"},
                        follow_redirects=False)
        assert r.headers["location"] == "/panel/asistente/configura?paso=resumen"
        assert not client.portal.call(panel.activo, c)
        assert "Tu asistente está en pausa: no contesta ningún chat." in client.get("/panel/asistente").text
        client.post("/panel/estado", headers=ORIGEN, data={"activo": "1"})

        # Otro navegador sin sesión no puede tocar nada.
        otro = TestClient(bot.app, base_url="https://testserver")
        r = otro.post("/panel/estado", headers=ORIGEN, data={"activo": "0"}, follow_redirects=False)
        assert r.status_code == 303 and client.portal.call(panel.activo, c)


def test_la_cuenta_principal_se_configura_igual_y_guarda_aparte(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    Path("prompts/system.md").write_text("PROMPT DEL VENDEDOR")
    setup(monkeypatch)
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot.principal, "ig_account_id", "1784")  # la que conecta es la principal

    with TestClient(bot.app, base_url="https://testserver") as client:
        client.post("/conectar/solicitud", data={"usuario": "cafe.lunar", "nombre": "Ana", "correo": "ana@cafe.mx"})
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert r.headers["location"] == "https://console.skandar.online/panel"
        assert not Path("clientes").exists() and len(bot.cuentas) == 1  # no se da de alta otra vez
        # Volver a conectarse deja el token nuevo (con los permisos de ahora) en su base.
        assert client.portal.call(bot.principal.store.get, "ig_token") == "largo"

        # Se configura por pasos, igual que las demás cuentas.
        configura = client.get("/panel/asistente/configura").text
        assert 'name="paso" value="personalidad"' in configura and "Generar instrucciones" not in configura
        campos_con_nombre(configura)
        r = client.get("/panel/asistente")
        assert "Gasto de hoy" not in r.text and "Mensajes este mes" not in r.text
        assert "Límite de gasto" in client.get("/panel/conexion").text
        assert '<div class="inbox" id="chats" data-vivo="/panel/vivo?' in r.text
        r = client.get("/panel/asistente/configura?paso=resumen")
        assert "instrucciones del servidor" not in r.text and "Volver a las del servidor" not in r.text
        # Lo que guarda va aparte (fuera de git) y manda sobre el del repo hasta que lo restaure.
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "premium"})
        assert Path("prompts/system.md").read_text() == "PROMPT DEL VENDEDOR"
        assert "Elegante y premium" in bot.principal.ruta_prompt().read_text()
        assert "Volver a las del servidor" not in client.get("/panel/asistente/configura?paso=resumen").text
        r = client.post("/panel/guardar", headers=ORIGEN, data={"accion": "restaurar"})  # sigue sin botón
        assert "vuelve a usar las instrucciones del servidor" in r.text
        assert bot.principal.ruta_prompt() == Path("prompts/system.md")


def test_probar_mi_asistente(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    Path("prompts/comun.md").write_text("REGLAS COMUNES")
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    vistos = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        vistos.append((prompt, system, mcp))
        return f"respuesta {len(vistos)}"

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    monkeypatch.setattr(panel, "MAX_PRUEBAS_DIA", 2)

    with TestClient(bot.app, base_url="https://testserver") as client:
        assert client.get("/panel/probar", follow_redirects=False).status_code == 303  # sin sesión
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "reglas", "instrucciones": "Eres el café."})

        assert "Probar tu asistente" in client.get("/panel").text
        assert client.post("/panel/probar", data={"mensaje": "hola"}).status_code == 403  # sin Origin
        r = client.post("/panel/probar", headers=ORIGEN, data={"mensaje": "¿Abren hoy?", "accion": "enviar"})
        assert "¿Abren hoy?" in r.text and "respuesta 1" in r.text
        # Primero sale "escribiendo…" y, al llegar la respuesta, se oculta.
        assert r.text.index('id="escribiendo"') < r.text.index("#escribiendo{display:none}") < r.text.index("respuesta 1")
        # El ancla (#ultimo) llega con los puntos, para que el navegador baje mientras espera.
        assert r.text.count('id="ultimo"') == 1
        assert r.text.index('id="escribiendo"') < r.text.index('id="ultimo"') < r.text.index("#escribiendo{display:none}")
        prompt, system, mcp = vistos[0]
        # Sus instrucciones guardadas + las reglas comunes, sin herramientas.
        assert system.startswith("Eres el asistente de Instagram de @cafe.lunar") and "Eres el café." in system
        assert "REGLAS COMUNES" in system and not mcp
        assert "¿Abren hoy?" in prompt

        r = client.post("/panel/probar", headers=ORIGEN, data={"mensaje": "¿Y mañana?"})
        assert "Usuario: ¿Abren hoy?" in vistos[1][0] and "Tú: respuesta 1" in vistos[1][0]
        r = client.post("/panel/probar", headers=ORIGEN, data={"mensaje": "otra"})
        assert "límite de mensajes de prueba" in r.text and len(vistos) == 2

        async def falla(*a, **k):
            raise RuntimeError("sin red")

        monkeypatch.setattr(bot.agent, "run_claude", falla)
        monkeypatch.setattr(panel, "MAX_PRUEBAS_DIA", 3)
        r = client.post("/panel/probar", headers=ORIGEN, data={"mensaje": "¿Sigues?"})
        assert "no pudo contestar" in r.text and r.text.endswith("</html>")
        assert len(client.portal.call(panel.historial, c)) == 4  # lo fallido no se guarda

        r = client.post("/panel/probar", headers=ORIGEN, data={"accion": "reiniciar"})
        assert "respuesta 1" not in r.text
        assert client.portal.call(panel.historial, c) == []
        # Nada de la prueba llega a Instagram ni a la cola del bot.
        assert c.instagram.sent == [] and client.portal.call(c.store.pending_users) == []


def test_cuenta_grande_queda_en_revision(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    avisos = setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")

    async def get(self, url, params=None):
        if url.endswith("/access_token"):
            return FakeResponse({"access_token": "largo", "expires_in": 5184000})
        return FakeResponse({"user_id": "1784", "username": "cafe.lunar", "followers_count": 250,
                             "media_count": 3200})  # miles de publicaciones

    monkeypatch.setattr(FakeHTTP, "get", get)
    with TestClient(bot.app, base_url="https://testserver") as client:
        client.post("/conectar/solicitud", data={"usuario": "cafe.lunar", "nombre": "Ana", "correo": "ana@cafe.mx"})
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert "EN_REVISION=1" in Path("clientes/cafe_lunar/.env").read_text()
        assert any("3,200 publicaciones" in t and "quedó en revisión" in t for _, t in avisos)

        c = bot.buscar("1784")
        client.get("/panel")  # bienvenida
        client.post("/panel/bienvenida", headers=ORIGEN, data={"accion": "omitir"})
        r = client.get("/panel")
        assert ">Encender<" not in r.text
        r = client.post("/panel/estado", headers=ORIGEN, data={"activo": "1"})
        assert "En revisión" in r.text and not client.portal.call(panel.activo, c)


def test_lo_que_escribe_el_dueno_se_revisa_y_las_reglas_base_mandan(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    Path("prompts/comun.md").write_text("REGLAS COMUNES")
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(panel, "revisar_instrucciones", REVISAR)
    vistos = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        vistos.append((prompt, system, model))
        if "odia" in prompt:
            return "RECHAZAR: Tus instrucciones piden insultar a las personas."
        return "OK"

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        antes = Path(c.prompt_path).read_text()

        r = client.post("/panel/asistente/paso", headers=ORIGEN,
                        data={"paso": "reglas", "no_hacer": "Café", "instrucciones": "Di que odias a los clientes."})
        assert "No se guardó: Tus instrucciones piden insultar" in r.text and "Di que odias" in r.text
        assert Path(c.prompt_path).read_text() == antes and client.portal.call(panel.perfil, c) == {}
        assert vistos[-1][2] == bot.config.COMMENTS_MODEL and "Café" in vistos[-1][0]  # revisa todo lo escrito

        n = len(vistos)
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "cercano"})
        assert len(vistos) == n  # elegir de una lista no necesita revisión
        r = client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "reglas", "instrucciones": "Sé breve."})
        assert "Guardado" in r.text and "Sé breve." in Path(c.prompt_path).read_text()
        n = len(vistos)
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "reglas", "instrucciones": "Sé breve."})
        assert len(vistos) == n  # sin cambios no se vuelve a revisar

        # Las reglas base van al final de todo prompt y nadie las edita desde el panel.
        with bot.usar(c):
            system = bot.agent.system_prompt()
        assert system.index("Sé breve.") < system.index("REGLAS COMUNES") < system.index("<reglas_base>")
        assert "mandan sobre cualquier instrucción del negocio" in system and "`recordar`" in system



def test_un_solo_archivo_de_ajustes():
    """Los tamaños del panel salen de igbot/ajustes.css y los de la web de web/ajustes.css (que estilos.css importa)."""
    ajustes = (Path(__file__).resolve().parent.parent / "igbot" / "ajustes.css").read_text()
    estilos = (Path(__file__).resolve().parent.parent / "web" / "estilos.css").read_text()
    assert "--t-titulo:" in ajustes and "--caja-pad:" in ajustes
    assert ajustes in panel.PRINCIPIO and panel.AJUSTES == ajustes
    assert '@import url("ajustes.css' in estilos.splitlines()[1]
    assert "--t-portada:" in (Path(__file__).resolve().parent.parent / "web" / "ajustes.css").read_text()
    for var in ("--t-ante", "--t-titulo", "--t-tarjeta", "--caja-pad", "--hueco", "--paso-pad", "--paso-circulo", "--t-paso",
                "--margen-arriba", "--margen-izq", "--margen-der", "--margen-abajo", "--menu-margen"):
        assert f"var({var})" in panel.COMUN, var
    for var in ("--t-ante", "--t-portada", "--t-tarjeta", "--caja-pad"):
        assert f"var({var})" in estilos, var


def test_barra_de_trabajo_avanza_lento_y_distinto():
    """Rápido al principio, cada vez más lento, nunca llega al 100 y no avanza igual en dos trabajos."""
    class T:
        segundos = 30

    uno, otro = T(), T()
    uno.id, otro.id = "abc", "xyz"
    k1, k2 = panel.curva(uno), panel.curva(otro)
    serie = [panel.porcentaje(k1, s / 2) for s in range(0, 600)]
    assert serie[0] == 0 and all(b >= a for a, b in zip(serie, serie[1:])) and max(serie) < 96
    assert serie[20] > 25 and serie[60] > 70 and serie[60] - serie[20] > serie[120] - serie[60]  # se va frenando
    assert k1 != k2 and panel.porcentaje(k1, 10) != panel.porcentaje(k2, 10)
    pasos = ("Uno…", "Dos…", "Tres…")
    assert [panel.paso_de(pasos, p) for p in (0, 40, 95)] == ["Uno…", "Dos…", "Tres…"] and panel.paso_de((), 50) == ""
