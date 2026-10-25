"""Perfil de empresa: pasos, catálogo, prompt del asistente y opciones de campaña."""
import json
import os
from pathlib import Path

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402

import app as bot  # noqa: E402
from igbot import empresa, panel  # noqa: E402
import test_contenido  # noqa: E402
from test_contenido import ORIGEN, png  # noqa: E402
from test_panel import campos_con_nombre  # noqa: E402

cliente = test_contenido.cliente  # el mismo cliente conectado de las pruebas de contenido

PASO_1 = {"paso": "1", "nombre": "Café Lunar", "giro": "Restaurante o cafetería", "frase": "Café de especialidad.",
          "descripcion": "Cafetería de especialidad en Puebla desde 2019.", "zona": "Puebla centro",
          "sitio_web": "cafelunar.mx", "caracter": ["Cercana", "Premium", "Natural", "Elegante"],
          "atiende": ["local", "nada"], "accion": "ir_2"}

OPCIONES = {"objetivos": [{"k": f"Obj {i}", "titulo": f"Objetivo {i}", "descripcion": "x"} for i in range(7)],
            "mensajes": [{"k": f"m{i}", "titulo": f"Mensaje {i}", "descripcion": "y", "ejemplo": "z"} for i in range(6)]}


def test_pasos_guardan_y_el_asistente_usa_el_perfil(cliente):
    client, c, llamadas, _ = cliente
    # Lo que ya llenó en Configura se precarga (sin guardarse todavía).
    client.portal.call(c.store.set, "perfil", json.dumps({"negocio": "Café en Puebla", "horarios": "L a V 8 a 20",
                                                          "envios": "No enviamos"}))
    r = client.get("/panel/empresa")
    assert "Empecemos por tu empresa" in r.text and "Café en Puebla" in r.text and "Te precargamos" in r.text
    assert 'href="/panel/empresa" class="on"' in r.text  # el menú de perfil la marca
    campos_con_nombre(r.text)
    assert client.portal.call(empresa.de, c) == {}
    assert not client.portal.call(empresa.empresa_lista, c)

    assert client.post("/panel/empresa", data=PASO_1).status_code == 403  # sin Origin
    r = client.post("/panel/empresa", headers=ORIGEN, data=PASO_1, follow_redirects=False)
    assert r.headers["location"] == "/panel/empresa?paso=2"
    e = client.portal.call(empresa.de, c)
    assert e["nombre"] == "Café Lunar" and e["sitio_web"] == "https://cafelunar.mx"
    assert e["caracter"] == ["Cercana", "Premium", "Natural"] and e["atiende"] == ["local"]
    assert e["horarios"] == "L a V 8 a 20"  # lo precargado de los otros pasos también queda
    assert client.portal.call(empresa.empresa_lista, c)

    # Paso 2 y 4; el riel guarda y salta a cualquier paso.
    r = client.post("/panel/empresa", headers=ORIGEN, follow_redirects=False,
                    data={"paso": "2", "clientes_tipo": "personas", "distinto": "Tostamos aquí.", "accion": "ir_4"})
    assert r.headers["location"] == "/panel/empresa?paso=4"
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "4", "horarios": "Diario 8 a 22", "pagos": ["tarjeta"],
                                                        "envios": "", "accion": "ir_5"})
    # Paso 5: + Agregar otra pregunta no guarda; Siguiente sí, sin las vacías.
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "5", "faq_n": "3", "accion": "mas_faq"})
    assert 'name="faq_n" value="4"' in r.text
    client.post("/panel/empresa", headers=ORIGEN, data={
        "paso": "5", "faq_n": "2", "faq_p_0": "¿Tienen wifi?", "faq_r_0": "Sí, gratis.", "faq_p_1": "", "faq_r_1": "",
        "accion": "ir_6"})
    e = client.portal.call(empresa.de, c)
    assert e["faq"] == [{"p": "¿Tienen wifi?", "r": "Sí, gratis."}] and e["envios"] == ""

    # El asistente ya contesta con el perfil (y no con lo viejo de Configura).
    prompt = Path(c.prompt_path).read_text()
    assert "Café Lunar (Restaurante o cafetería)" in prompt and "Diario 8 a 22" in prompt
    assert "Formas de pago: Tarjeta" in prompt and "¿Tienen wifi?" in prompt and "No enviamos" not in prompt
    # Guardar Configura después no lo pisa.
    client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "premium"})
    assert "Café Lunar" in Path(c.prompt_path).read_text() and "Elegante y premium" in Path(c.prompt_path).read_text()

    r = client.get("/panel/empresa")  # con lo esencial lleno abre el resumen
    assert "Así queda tu empresa" in r.text and "Se preparan cuando guardes" in r.text
    assert "Café Lunar<span" in r.text  # el nombre en el hero


def test_catalogo_con_fotos_y_texto_para_prompt(cliente):
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    r = client.get("/panel/empresa?paso=3")
    assert "Aún no tienes productos" in r.text and "<details open" in r.text
    campos_con_nombre(r.text)
    r = client.post("/panel/empresa/producto", headers=ORIGEN, follow_redirects=False, data={
        "nombre": "Latte <grande>", "precio": "$65", "descripcion": "Con leche de avena.", "url": "cafelunar.mx/latte",
        "tipo": "producto", "destacado": "1", "moneda": "MXN"},
        files=[("fotos", ("a.png", png(), "image/png")), ("fotos", ("b.png", png(), "image/png")),
               ("fotos", ("mala.png", b"no es foto", "image/png"))])
    assert r.headers["location"] == "/panel/empresa?paso=3&aviso=foto_mala"
    [p] = client.portal.call(empresa.productos, c)
    assert p["precio"] == "65" and p["url"] == "https://cafelunar.mx/latte" and len(p["imagenes"]) == 2
    assert client.portal.call(empresa.producto, c, p["id"])["nombre"] == "Latte <grande>"
    r = client.get("/panel/empresa?paso=3")
    assert "Latte &lt;grande&gt;" in r.text and "$65 MXN" in r.text and "2 fotos" in r.text
    assert "cafelunar.mx/latte" in r.text

    # Editar: quitar una foto se queda editando; quitar el producto borra sus fotos.
    r = client.get(f"/panel/empresa?paso=3&producto={p['id']}")
    assert "Editar Latte" in r.text and "Quitar producto" in r.text
    campos_con_nombre(r.text)
    r = client.post("/panel/empresa/producto", headers=ORIGEN, follow_redirects=False,
                    data={"id": p["id"], "nombre": "Latte", "precio": "70", "desde": "1", "destacado": "1",
                          "url": p["url"], "accion": "quitar_foto_0"})
    assert f"producto={p['id']}" in r.headers["location"]
    [p2] = client.portal.call(empresa.productos, c)
    assert p2["imagenes"] == p["imagenes"][1:] and p2["precio"] == "70" and p2["desde"]
    assert not (Path(c.media_dir) / f"{p['imagenes'][0]}.jpg").exists()

    texto = client.portal.call(empresa.texto_para_prompt, c)
    assert "Latte (destacado): Desde $70 MXN" in texto and "Enlace" not in texto  # el enlace es para el asistente
    assert "Con quién" not in texto and "Carácter de la marca: Cercana" in texto
    assert "Latte: Desde $70 MXN" in Path(c.prompt_path).read_text()
    assert "Enlace: https://cafelunar.mx/latte" in Path(c.prompt_path).read_text()

    client.post("/panel/empresa/producto", headers=ORIGEN, data={"id": p["id"], "accion": "quitar"})
    assert client.portal.call(empresa.productos, c) == []
    assert not (Path(c.media_dir) / f"{p['imagenes'][1]}.jpg").exists()
    r = client.post("/panel/empresa/producto", headers=ORIGEN, data={"nombre": " "})
    assert "Ponle nombre" in r.text


def test_la_revision_puede_rechazar(cliente, monkeypatch):
    client, c, *_ = cliente

    async def rechazar(c, texto):
        return "No prometas lo que no das." if "garantizado" in texto else ""

    monkeypatch.setattr(panel, "revisar_instrucciones", rechazar)
    r = client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "descripcion": "Éxito garantizado"})
    assert "No se guardó: No prometas" in r.text and "Éxito garantizado" in r.text
    assert client.portal.call(empresa.de, c) == {}
    r = client.post("/panel/empresa/producto", headers=ORIGEN, data={"nombre": "Curso garantizado"})
    assert "No se guardó" in r.text and 'name="id" value=""' in r.text  # sigue siendo uno nuevo
    assert client.portal.call(empresa.productos, c) == []


def test_opciones_de_campana_al_terminar(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"})
    assert "Llena el nombre, el giro y la descripción" in r.text  # sin lo esencial no termina
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    programadas = []
    monkeypatch.setattr(empresa, "programar_opciones", lambda c: programadas.append(c))
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/bienvenida" and programadas  # cuenta nueva: al paso 2 de la bienvenida
    client.portal.call(c.store.set, "bienvenida", "1")
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/empresa?paso=6&aviso=listo" and len(programadas) == 2
    assert client.portal.call(empresa.de, c)["terminado"] is True

    respuestas = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        respuestas.append(prompt)
        return "Claro:\n" + json.dumps(OPCIONES)

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    assert client.portal.call(empresa.opciones_campana, c) is None
    client.portal.call(empresa.generar_opciones, c)
    op = client.portal.call(empresa.opciones_campana, c)
    assert [o["k"] for o in op["objetivos"]] == ["obj-0", "obj-1", "obj-2", "obj-3", "obj-4", "obj-5"]
    assert op["mensajes"][0] == {"k": "m0", "titulo": "Mensaje 0", "descripcion": "y", "ejemplo": "z"}
    assert "<empresa>" in respuestas[0] and "Café Lunar" in respuestas[0]
    client.portal.call(empresa.generar_opciones, c)  # sin cambios en el perfil no se vuelve a pedir
    assert len(respuestas) == 1
    r = client.get("/panel/empresa?paso=6&aviso=listo")
    assert "Objetivo 0" in r.text and "Mensaje 5" in r.text and "ya usan tu perfil" in r.text
    # Ya terminado, cambiar un paso vuelve a pedirlas (en segundo plano).
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "2", "distinto": "Nuevo", "accion": "ir_3"})
    assert len(programadas) == 3


@pytest.mark.parametrize("respuesta", ["no sé", json.dumps({"objetivos": OPCIONES["objetivos"][:2],
                                                            "mensajes": OPCIONES["mensajes"]})])
def test_opciones_malas_no_se_guardan(cliente, monkeypatch, respuesta):
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)

    async def fake_claude(*a, **k):
        return respuesta

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    client.portal.call(empresa.generar_opciones, c)
    assert client.portal.call(empresa.opciones_campana, c) is None
