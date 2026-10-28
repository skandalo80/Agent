"""Perfil de empresa: pasos, catálogo, prompt del asistente y opciones de campaña."""
import asyncio
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
          "atiende": ["local", "nada"], "accion": "ir_3"}

OPCIONES = {"objetivos": [{"k": f"Obj {i}", "titulo": f"Objetivo {i}", "descripcion": "x",
                           "mensajes": [{"k": f"m{i}-{j}", "titulo": f"Mensaje {i}.{j}", "descripcion": "y",
                                         "ejemplo": "z"} for j in range(5)]} for i in range(7)]}


def test_pasos_guardan_y_el_asistente_usa_el_perfil(cliente):
    client, c, llamadas, _ = cliente
    # Lo que ya llenó en Configura se precarga (sin guardarse todavía).
    client.portal.call(c.store.set, "perfil", json.dumps({"negocio": "Café en Puebla", "horarios": "L a V 8 a 20",
                                                          "envios": "No enviamos"}))
    r = client.get("/panel/empresa")
    assert "Paso 1 de 5" in r.text and "Café en Puebla" in r.text and "Te precargamos" in r.text
    assert "¿Cómo es tu empresa?" not in r.text and 'class="leyenda-usos"' in r.text
    # Tus clientes va al final: el riel lo numera 5 y el paso 1 sigue a productos.
    assert "Siguiente: productos y servicios" in r.text and r.text.index("<b>Tus clientes") > r.text.index("<b>Preguntas")
    assert 'class="lat-item on" href="/panel/empresa" title="Tu empresa"' in r.text  # Configuración la marca
    campos_con_nombre(r.text)
    assert client.portal.call(empresa.de, c) == {}
    assert not client.portal.call(empresa.empresa_lista, c)

    assert client.post("/panel/empresa", data=PASO_1).status_code == 403  # sin Origin
    r = client.post("/panel/empresa", headers=ORIGEN, data=PASO_1, follow_redirects=False)
    assert r.headers["location"] == "/panel/empresa?paso=3"
    e = client.portal.call(empresa.de, c)
    assert e["nombre"] == "Café Lunar" and e["sitio_web"] == "https://cafelunar.mx"
    assert "caracter" not in e and e["atiende"] == ["local"]  # el carácter ya no se pregunta aquí
    assert e["horarios"] == "L a V 8 a 20"  # lo precargado de los otros pasos también queda
    assert client.portal.call(empresa.empresa_lista, c)

    # Aplicar guarda y se queda en el mismo paso.
    r = client.post("/panel/empresa", headers=ORIGEN, follow_redirects=False,
                    data={"paso": "2", "clientes_tipo": "personas", "distinto": "Tostamos.", "accion": "aplicar"})
    assert r.headers["location"] == "/panel/empresa?paso=2&aviso=aplicado"
    r = client.get(r.headers["location"]).text
    assert "Cambios aplicados." in r and 'value="aplicar">Aplicar</button>' in r and "Tostamos." in r
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

    # El asistente contesta con el perfil (y no con lo viejo de Configura) en cuanto se aprueba.
    client.portal.call(empresa.esperar_revision, c)
    prompt = Path(c.prompt_path).read_text()
    assert "Café Lunar (Restaurante o cafetería)" in prompt and "Diario 8 a 22" in prompt
    assert "Formas de pago: Tarjeta" in prompt and "¿Tienen wifi?" in prompt and "No enviamos" not in prompt
    # Guardar Configura después no lo pisa.
    client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "premium"})
    assert "Café Lunar" in Path(c.prompt_path).read_text() and "Elegante y premium" in Path(c.prompt_path).read_text()

    r = client.get("/panel/empresa")  # con lo esencial lleno abre el resumen
    assert '<div class="hoja-o"><form' in r.text and ">Resumen</p>" in r.text  # el título va dentro de la tarjeta
    assert 'class="antetitulo"' not in r.text.split("<main")[0] and "Café Lunar<span" not in r.text  # sin título arriba


def test_catalogo_con_fotos_y_texto_para_prompt(cliente):
    client, c, *_ = cliente
    # El carácter ya no está en el formulario; el que tenga guardado (de Tu marca o de antes) se sigue usando.
    client.portal.call(c.store.set, empresa.CLAVE, json.dumps({"caracter": ["Cercana"]}))
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
    client.portal.call(empresa.esperar_revision, c)
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
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)  # lo aprobado
    client.portal.call(empresa.esperar_revision, c)
    prompt = Path(c.prompt_path).read_text()
    # Se guarda sin esperar; la revisión corre después y, si rechaza, vuelve lo aprobado.
    r = client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "descripcion": "Éxito garantizado"},
                    follow_redirects=False)
    assert r.headers["location"] == "/panel/empresa?paso=3"
    client.portal.call(empresa.esperar_revision, c)
    assert client.portal.call(empresa.de, c)["descripcion"] == PASO_1["descripcion"]
    assert Path(c.prompt_path).read_text() == prompt and "garantizado" not in prompt  # nunca llegó al asistente
    r = client.get("/panel/empresa?paso=1")  # el aviso, con lo que escribió para corregirlo
    assert "No se guardó tu último cambio: No prometas" in r.text and "Éxito garantizado" in r.text
    client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "descripcion": "Café de verdad."})
    client.portal.call(empresa.esperar_revision, c)
    assert "No se guardó" not in client.get("/panel/empresa?paso=1").text
    assert "Café de verdad." in Path(c.prompt_path).read_text()

    client.post("/panel/empresa/producto", headers=ORIGEN, data={"nombre": "Curso garantizado"})
    client.portal.call(empresa.esperar_revision, c)
    assert client.portal.call(empresa.productos, c) == []
    r = client.get("/panel/empresa?paso=3")
    assert "No se guardó tu último cambio" in r.text and "Curso garantizado" in r.text
    assert 'name="id" value=""' in r.text  # sigue siendo uno nuevo


def test_cuentas_de_antes_conservan_lo_aprobado(cliente, monkeypatch):
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    client.portal.call(empresa.esperar_revision, c)
    client.portal.call(c.store.set, empresa.CLAVE_APROBADO, "")  # como una cuenta de antes de este cambio

    async def rechazar(c, texto):
        return "No." if "garantizado" in texto else ""

    monkeypatch.setattr(panel, "revisar_instrucciones", rechazar)
    client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "descripcion": "Éxito garantizado"})
    client.portal.call(empresa.esperar_revision, c)
    assert client.portal.call(empresa.de, c)["descripcion"] == PASO_1["descripcion"]


def test_opciones_de_campana_al_terminar(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"})
    assert "Llena el nombre, el giro y la descripción" in r.text  # sin lo esencial no termina
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    programadas = []
    monkeypatch.setattr(empresa, "programar_opciones", lambda c: programadas.append(c))
    monkeypatch.setattr(empresa.posicionamiento, "programar", lambda c, perfil: None)  # su prueba va aparte
    r = client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"}, follow_redirects=False)
    client.portal.call(empresa.esperar_revision, c)
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
    # Cada objetivo con sus propios mensajes (hasta 4), y todos juntos arriba para lo de antes.
    assert op["version"] == empresa.VERSION_OPCIONES and len(op["objetivos"][1]["mensajes"]) == 4
    assert op["objetivos"][1]["mensajes"][0] == {"k": "m1-0", "titulo": "Mensaje 1.0", "descripcion": "y",
                                                 "ejemplo": "z"}
    assert [m["k"] for m in empresa.mensajes_de(op, "obj-2")] == ["m2-0", "m2-1", "m2-2", "m2-3"]
    assert len(op["mensajes"]) == 6
    assert "<empresa>" in respuestas[0] and "Café Lunar" in respuestas[0]
    client.portal.call(empresa.generar_opciones, c)  # sin cambios en el perfil no se vuelve a pedir
    assert len(respuestas) == 1
    r = client.get("/panel/empresa?paso=6&aviso=listo")
    assert "tus campañas usan tu perfil" in r.text
    assert "Tus campañas, a la medida" not in r.text and "Objetivo 0" not in r.text  # se ven al crear la campaña
    # Ya terminado, cambiar un paso vuelve a pedirlas (en segundo plano).
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "2", "distinto": "Nuevo", "accion": "ir_3"})
    client.portal.call(empresa.esperar_revision, c)
    assert len(programadas) == 3


@pytest.mark.parametrize("respuesta", [
    "no sé", json.dumps({"objetivos": OPCIONES["objetivos"][:2]}),
    json.dumps({"objetivos": [{**o, "mensajes": o["mensajes"][:1]} for o in OPCIONES["objetivos"]]})])
def test_opciones_malas_no_se_guardan(cliente, monkeypatch, respuesta):
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)

    async def fake_claude(*a, **k):
        return respuesta

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    client.portal.call(empresa.generar_opciones, c)
    assert client.portal.call(empresa.opciones_campana, c) is None


def test_posicionamiento_sale_del_perfil_de_empresa(cliente, monkeypatch):
    from igbot import posicionamiento
    client, c, *_ = cliente
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    programados = []
    monkeypatch.setattr(empresa, "programar_opciones", lambda c: None)
    monkeypatch.setattr(posicionamiento, "programar", lambda cuenta, perfil: programados.append(cuenta))
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "2", "distinto": "Antes", "accion": "ir_3"})
    assert programados == []  # antes de terminar el primer recorrido no se recalcula
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "6", "accion": "terminar"})
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "2", "distinto": "Tostado <b>propio</b>", "accion": "ir_3"})
    client.portal.call(empresa.esperar_revision, c)
    assert programados

    prompts = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        prompts.append(prompt)
        return json.dumps({"ofrece": "Café", "cliente_ideal": "", "distinto": "Tostado propio", "competencia": ""})

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    client.portal.call(posicionamiento.actualizar, c, {"negocio": "Lo viejo de Configura"})
    # Del perfil de empresa, escapado una sola vez, y no de Configura.
    assert "Café Lunar" in prompts[0] and "Tostado &lt;b&gt;propio&lt;/b&gt;" in prompts[0]
    assert "&amp;lt;" not in prompts[0] and "Lo viejo de Configura" not in prompts[0]
    assert client.portal.call(posicionamiento.de, c)["distinto"] == "Tostado propio"
    client.portal.call(posicionamiento.actualizar, c, {})  # sin cambios en el perfil no se vuelve a pedir
    assert len(prompts) == 1


def test_iconos_de_quien_usa_cada_campo_y_caracter_guardado(cliente):
    client, c, _, _ = cliente
    client.portal.call(c.store.set, empresa.CLAVE, json.dumps({"nombre": "Café Lunar", "caracter": ["Cercana"]}))
    client.post("/panel/empresa", headers=ORIGEN, data={**PASO_1, "caracter": []})
    assert client.portal.call(empresa.de, c)["caracter"] == ["Cercana"]  # lo guardado no se borra

    def iconos(html: str, etiqueta: str) -> str:
        return html.split(etiqueta, 1)[1].split("</label>", 1)[0].split("</legend>", 1)[0]
    p1 = client.get("/panel/empresa?paso=1").text
    assert "<h2>Empecemos por tu empresa</h2>" in p1.split('class="cab-paso"')[1].split("</div>")[0]  # título y bajada, como Tu asistente y Tu marca
    assert iconos(p1, "Tu empresa en una frase").count('class="uso"') == 2
    p2 = client.get("/panel/empresa?paso=2").text
    assert "Paso 5 de 5" in p2 and "Siguiente: resumen" in p2
    assert "Lo usan tus campañas" in iconos(p2, "¿Con quién te comparan?")
    assert "Lo usa tu asistente" not in iconos(p2, "¿Con quién te comparan?")
    p3 = client.get("/panel/empresa?paso=3").text
    assert "Lo usa tu asistente" in iconos(p3, ">Enlace") and "Lo usan" not in iconos(p3, ">Enlace")
    assert "Lo usan tus campañas" in p3.split("Fotos <i", 1)[1].split('class="fotos-o"', 1)[0]
    p5 = client.get("/panel/empresa?paso=5").text
    assert iconos(p5, "Pregunta 1").count('class="uso"') == 1 and "Lo usa tu asistente" in iconos(p5, "Pregunta 1")


def test_una_sola_revision_al_recorrer_los_pasos(cliente, monkeypatch):
    """Guardar varios pasos seguidos lanza una revisión, cuando deja de guardar; Terminar no espera; y si lo que lee
    el asistente no cambió, no se le pregunta a la IA."""
    client, c, *_ = cliente
    revisiones = []

    async def revisar(c, texto):
        revisiones.append(texto)
        return ""

    monkeypatch.setattr(panel, "revisar_instrucciones", revisar)
    monkeypatch.setattr(empresa, "ESPERA_REVISION", 0.3)
    client.post("/panel/empresa", headers=ORIGEN, data=PASO_1)
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "4", "horarios": "Diario 8 a 22", "accion": "ir_5"})
    assert not revisiones and client.portal.call(empresa.revision_pendiente, c)  # todavía está guardando
    client.portal.call(empresa.esperar_revision, c)
    assert len(revisiones) == 1 and "Diario 8 a 22" in revisiones[0]
    assert not client.portal.call(empresa.revision_pendiente, c)

    monkeypatch.setattr(empresa, "ESPERA_REVISION", 3600)
    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "4", "horarios": "Diario 9 a 21", "accion": "terminar"})
    for _ in range(50):  # Terminar revisa sin esperar la hora
        if not empresa._revisiones.get(c.slug):
            break
        client.portal.call(asyncio.sleep, 0.05)
    assert len(revisiones) == 2 and "Diario 9 a 21" in Path(c.prompt_path).read_text()

    client.post("/panel/empresa", headers=ORIGEN, data={"paso": "4", "horarios": "Diario 9 a 21", "pagos": ["tarjeta"],
                                                        "accion": "terminar"})
    client.portal.call(empresa.esperar_revision, c)
    assert len(revisiones) == 3  # los pagos sí llegan al asistente
    e = client.portal.call(empresa.de, c)
    client.portal.call(c.store.set, empresa.CLAVE, json.dumps({**e, "actualizado": 1}))
    client.portal.call(empresa.revisar_y_aplicar, c)
    assert len(revisiones) == 3  # cambió algo que el asistente no lee: no se revisa
