"""Configurar por chat: elegir cómo llenar lo que falta y contestar una pregunta a la vez."""
import json

import app as bot  # noqa: E402
from igbot import configurar, empresa  # noqa: E402
import test_contenido  # noqa: E402
from test_contenido import ORIGEN  # noqa: E402
from test_panel import campos_con_nombre  # noqa: E402
from test_sitio import web  # noqa: E402,F401

cliente = test_contenido.cliente
CHAT = "/panel/configurar/chat"


def test_sin_sesion_y_sin_origen(cliente):
    client, *_ = cliente
    assert client.post(CHAT, data={"texto": "x"}).status_code == 403  # sin Origin
    assert client.post("/panel/configurar/sitio", data={"sitio_web": "cafelunar.mx"}).status_code == 403
    client.cookies.clear()
    for ruta in ("/panel/configurar", CHAT):
        r = client.get(ruta, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/panel"


def test_que_falta_segun_lo_que_contesta():
    assert configurar.faltan({})[:2] == ["nombre", "giro"] and "giro_otro" not in configurar.faltan({})
    assert "giro_otro" in configurar.faltan({"giro": empresa.OTRO})
    solo_linea = configurar.faltan({"atiende": ["linea"]})
    assert "direccion" not in solo_linea and "envios" in solo_linea  # sin local no se pide dirección
    assert "envios" not in configurar.faltan({"atiende": ["local"]})
    assert "nombre" not in configurar.faltan({}, saltados=["nombre"])
    assert configurar.tenemos({"nombre": "Café", "giro": "Otro", "giro_otro": "Tostador"}) == ["Nombre", "Giro"]


def test_validar_campo_como_el_cuestionario():
    assert empresa.validar_campo("nombre", "  Café Lunar  ") == "Café Lunar"
    assert len(empresa.validar_campo("nombre", "x" * 500)) == 80
    assert empresa.validar_campo("giro", "Inventado") is None and empresa.validar_campo("giro", "Joyería") == "Joyería"
    assert empresa.validar_campo("pagos", ["tarjeta", "bitcoin", "efectivo"]) == ["efectivo", "tarjeta"]
    assert empresa.validar_campo("sitio_web", "cafelunar.mx") == "https://cafelunar.mx"
    assert empresa.validar_campo("terminado", "1") is None and empresa.validar_campo("faq", "x") is None


def test_elegir_muestra_lo_que_hay_y_lo_que_falta(cliente):
    client, c, *_ = cliente
    r = client.get("/panel/configurar")
    assert "¿Cómo quieres llenar tu perfil?" in r.text and "¿Tienes página web?" in r.text
    assert 'href="/panel/configurar/chat#fin"' in r.text and 'href="/panel/empresa"' in r.text
    campos_con_nombre(r.text)
    client.portal.call(empresa.guardar_campos, c, {"nombre": "Café Lunar", "zona": "Puebla"})
    r = client.get("/panel/configurar?aviso=web")
    assert "¿Cómo quieres llenar lo que falta?" in r.text and "Guardamos lo que aprobaste" in r.text
    assert "<li>Nombre</li>" in r.text and "<li>Horarios</li>" in r.text and "¿Tienes página web?" not in r.text
    client.portal.call(empresa.esperar_revision, c)


def test_la_pagina_web_se_lee_desde_la_bienvenida(cliente, web, monkeypatch):
    client, c, *_ = cliente

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        return json.dumps({"nombre": "Café Lunar", "horarios": "Lunes a viernes de 8:00 a 20:00"})

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    r = client.get("/panel/bienvenida")
    assert 'action="/panel/configurar/sitio"' in r.text and "No tengo página web" in r.text
    r = client.post("/panel/configurar/sitio", headers=ORIGEN, data={"sitio_web": "sin punto"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/configurar?aviso=sitio"
    r = client.post("/panel/configurar/sitio", headers=ORIGEN, data={"sitio_web": "cafelunar.mx"})
    assert 'url=/panel/empresa/sitio"' in r.text
    assert client.portal.call(empresa.de, c)["sitio_web"] == "https://cafelunar.mx"
    # Aprueba lo que encontramos y llega a elegir cómo llenar lo que falta.
    r = client.post("/panel/empresa/sitio", headers=ORIGEN, follow_redirects=False,
                    data={"accion": "usar", "usar": ["nombre", "horarios"], "s_nombre": "Café Lunar",
                          "s_horarios": "L a V de 8 a 20"})
    assert r.headers["location"] == "/panel/configurar?aviso=web"
    r = client.get(r.headers["location"])
    assert "<li>Nombre</li>" in r.text and "<li>Horarios</li>" in r.text and "<li>Giro</li>" in r.text
    client.portal.call(empresa.esperar_revision, c)


def test_chat_pregunta_solo_lo_que_falta_y_guarda(cliente, monkeypatch):
    client, c, *_ = cliente
    pedidos = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        pedidos.append((prompt, system, model))
        if "tostamos" in prompt:
            return json.dumps({"descripcion": "Tostamos café de especialidad desde 2019.", "zona": "Puebla",
                               "giro": "Joyería", "inventado": "x"})
        if "no tengo" in prompt:
            return json.dumps({"horarios": ""})
        return "no es json"

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    client.portal.call(empresa.guardar_campos, c, {"nombre": "Café Lunar"})
    r = client.get(CHAT)
    assert "¿A qué se dedica?" in r.text and "¿Cómo se llama tu empresa?" not in r.text  # el nombre ya lo tenemos
    assert 'name="opcion" value="Joyería"' in r.text and "Seguir en el cuestionario" in r.text
    campos_con_nombre(r.text)
    assert "autofocus" not in r.text  # en celular el teclado no se abre solo

    # Una opción que no existe no guarda nada; una de la lista, sí.
    client.post(CHAT, headers=ORIGEN, data={"opcion": "Inventado"})
    assert not client.portal.call(empresa.de, c).get("giro")
    r = client.post(CHAT, headers=ORIGEN, data={"opcion": "Restaurante o cafetería"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/configurar/chat#fin"
    assert client.portal.call(empresa.de, c)["giro"] == "Restaurante o cafetería"

    # Una respuesta abierta puede llenar varios campos; lo que no es de la lista no pasa (ni el giro, que es de botones).
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "responder", "texto": "tostamos café <b>rico</b> en Puebla"})
    e = client.portal.call(empresa.de, c)
    assert e["descripcion"] == "Tostamos café de especialidad desde 2019." and e["zona"] == "Puebla"
    assert e["giro"] == "Restaurante o cafetería" and "inventado" not in e
    prompt, system, model = pedidos[-1]
    assert model == bot.config.COMMENTS_MODEL and "&lt;b&gt;" in prompt and "no instrucciones" in system
    assert "También guardamos: ciudad o zona." in r.text and "¿Cómo atiendes a tus clientes?" in r.text
    assert "tostamos café &lt;b&gt;rico&lt;/b&gt; en Puebla" in r.text  # lo que escribió, escapado

    # Varias opciones con casillas.
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "responder", "opcion": ["local", "linea", "otra"]})
    assert client.portal.call(empresa.de, c)["atiende"] == ["local", "linea"]
    assert "En local o tienda, En línea" in r.text and "¿Cuáles son tus horarios" in r.text

    # "No tengo": queda sin llenar y no se vuelve a preguntar. Si el modelo falla, su texto va tal cual.
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "responder", "texto": "no tengo horario fijo"})
    assert not client.portal.call(empresa.de, c).get("horarios") and "lo dejamos sin llenar" in r.text
    assert "¿Cuál es la dirección de tu local?" in r.text
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "responder", "texto": "Av. Juárez 10"})
    assert client.portal.call(empresa.de, c)["direccion"] == "Av. Juárez 10"
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "saltar"})
    assert "lo dejamos para después" in r.text and not client.portal.call(empresa.de, c).get("pagos")
    assert "horarios" in client.portal.call(configurar.estado, c)["saltados"]
    client.portal.call(empresa.esperar_revision, c)


def test_al_terminar_lleva_a_las_pantallas(cliente, monkeypatch):
    client, c, *_ = cliente
    todo = {"nombre": "Café Lunar", "giro": "Joyería", "descripcion": "Café.", "zona": "Puebla", "atiende": ["local"],
            "horarios": "L a V", "direccion": "Av. Juárez 10", "pagos": ["efectivo"], "cambios": "No hay.",
            "clientes_tipo": "personas", "cliente_ideal": "Vecinos", "problema": "Sueño"}
    client.portal.call(empresa.guardar_campos, c, todo)
    monkeypatch.setattr(configurar, "LECTURAS_DIA", 0)  # sin lecturas: lo que escribe se guarda tal cual
    r = client.post(CHAT, headers=ORIGEN, data={"accion": "responder", "texto": "Tostamos aquí mismo."})
    assert client.portal.call(empresa.de, c)["distinto"] == "Tostamos aquí mismo."
    assert "Ya tenemos lo que se puede platicar" in r.text and 'href="/panel/empresa?paso=3"' in r.text
    assert 'name="accion" value="terminar"' in r.text and "<textarea" not in r.text
    assert "Tu perfil ya tiene lo esencial" in client.get("/panel/configurar").text
    client.portal.call(empresa.esperar_revision, c)
