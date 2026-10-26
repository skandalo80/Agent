"""Contenido en el panel: marca, crear post, editarlo, aprobarlo y publicarlo."""
import io
import json
import os
import re
from functools import partial
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as bot  # noqa: E402
from igbot import admin, contenido, estilos, imagenes, panel, tools  # noqa: E402
from igbot.cuenta import usar  # noqa: E402
from test_conectar import setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402
from test_imagenes import Falso  # noqa: E402

MARCA = "/panel/contenido/marca"
ORIGEN = {"Origin": "https://console.skandar.online"}

POST = {"titulo": "Cartera Atelier", "subtitulo": "Piel italiana, hecha a mano.", "precio": "Desde $1,290 MXN",
        "caption": "Hecha a mano en piel italiana. Comenta PRECIO y te mandamos los colores.\n#cartera #piel",
        "palabra_clave": "Precio", "respuesta_comentario": "¡Te mandamos la info por mensaje!",
        "mensaje_dm": "La cartera Atelier cuesta $1,290 MXN.", "imagen": "A leather handbag on a stone table."}


class ConPosts(FakeInstagram):
    """Lo que ya hay en el Instagram del cliente: uno subido desde la app y el que publicó el panel."""

    async def username(self, user_id):
        return f"@{user_id}"

    async def recent_media(self, limit=12):
        return (await self.media_page(limit))[0]

    async def media_count(self):
        return 2

    async def media_page(self, limit, after="", fields=""):
        return [{"id": "1", "caption": "Desde la app\nSegunda línea", "media_url": "https://scontent.cdninstagram.com/a.jpg",
                 "permalink": "https://www.instagram.com/p/DESDEAPP/", "timestamp": "2099-01-01T10:00:00+0000"},
                {"id": "2", "caption": "Del panel", "permalink": "https://www.instagram.com/p/ABC123/",
                 "timestamp": "2020-01-01T10:00:00+0000"}][:limit], ""


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 1000), (120, 90, 60)).save(out, "PNG")
    return out.getvalue()


class Subida:
    """Una foto subida (como las de un formulario), para guardarla sin pasar por una página."""
    async def read(self, n):
        return png()


def crear(client, c, objetivo="marca", **kw) -> int:
    """Un post como lo arma Campañas (Crear post ya no existe): Claude escribe y se genera la imagen."""
    return client.portal.call(partial(contenido.generar, c, objetivo, **kw))


def test_leer_respuesta_de_claude():
    datos = contenido.leer_respuesta("Aquí va:\n```json\n" + json.dumps(POST) + "\n```")
    assert datos["titulo"] == "Cartera Atelier" and datos["palabra_clave"] == "PRECIO"
    with pytest.raises(imagenes.ErrorImagen):
        contenido.leer_respuesta("no puedo")


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    """Un cliente conectado (cafe.lunar) con 2 imágenes al mes, Claude y Gemini de mentira."""
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    Path("prompts/comun.md").write_text("REGLAS")
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: ConPosts())
    monkeypatch.setattr(contenido, "_de_instagram", {})
    monkeypatch.setattr(contenido, "_cursores", {})
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    llamadas = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system, model))
        return json.dumps(POST)

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    proveedor = Falso()
    monkeypatch.setattr(imagenes, "proveedor", proveedor)

    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "reglas", "instrucciones": "Vendemos carteras."})
        yield client, c, llamadas, proveedor


def test_sin_contenido_en_el_plan(cliente):
    client, c, *_ = cliente
    assert c.imagenes_mes == 0
    assert 'title="Contenido"' not in client.get("/panel").text  # el menú solo sale si su plan lo tiene
    assert "Tu plan actual es el Asistente virtual" in client.get("/panel/contenido").text
    assert client.get("/panel/contenido/nuevo", follow_redirects=False).headers["location"] == "/panel/contenido/campanas"


def test_marca_crear_aprobar_y_publicar(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    assert 'href="/panel/contenido" title="Contenido"' in client.get("/panel/asistente").text
    r = client.get("/panel/contenido")
    assert 'id="cal"' in r.text and "Próximos posts" not in r.text  # Calendario: solo el calendario
    r = client.get("/panel/contenido?fecha=2099-01-01&ver=lista")
    assert "DESDEAPP" in r.text and "Ya publicados" in r.text  # lo de la app sale aunque no haya del panel

    # Tu marca, paso a paso: sin marca abre el paso 1; cada paso guarda y lleva al siguiente.
    r = client.get("/panel/contenido/marca")
    assert "Paso 1 de 4" in r.text and '<main class="oscuro-pag">' in r.text and r.text.count('name="estilos"') == 9
    assert "¿Cómo quieres que se vean los diseños de tu marca?" in r.text and "Tu cuenta · Tu marca" in r.text
    assert "Sensación: rebelde, directo, impactante." in r.text and "1.º · Estilo principal:" in r.text
    # Tu marca vive en el menú de perfil (abajo), no en el submenú de Contenido.
    assert '<a href="/panel/contenido/marca" class="on">Tu marca</a>' in r.text
    assert 'Calendario</a></div>' in r.text and r.text.count('href="/panel/contenido/marca"') == 1
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "1"})
    assert "Elige al menos un estilo" in r.text and client.portal.call(contenido.marca_de, c) == {}
    r = client.post(MARCA, headers=ORIGEN, follow_redirects=False,
                    data={"paso": "1", "estilos": ["brutalista", "editorial", "bento", "espacial"], "orden": "editorial,brutalista"})
    assert r.status_code == 303 and r.headers["location"] == f"{MARCA}?paso=2"
    assert client.portal.call(contenido.marca_de, c)["estilos"] == ["editorial", "brutalista", "bento"]  # en el orden tocado
    r = client.get(f"{MARCA}?paso=1")
    assert "1.º · Estilo principal</span>" in r.text and "3.º · Estilo de apoyo</span>" in r.text
    # Colores: la paleta recién elegida (sin JavaScript) llena los cuatro; después manda el color que cambió.
    client.post(MARCA, headers=ORIGEN, data={"paso": "2", "paleta": "azul", "paleta_antes": "", "uso_color": "natural"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["principal"], guardada["secundario"], guardada["uso_color"]) == ("#16324F", "#3B82C4", "natural")
    client.post(MARCA, headers=ORIGEN, data={
        "paso": "2", "paleta": "azul", "paleta_antes": "azul", "uso_color": "natural",
        "principal": "#16324f", "principal_hex": "1f3a5f", "secundario": "#3b82c4", "secundario_hex": "nada",
        "acento": "#123456", "acento_hex": "#9ED8CF", "fondo": "#f5f8fb", "fondo_hex": "#F5F8FB"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["principal"], guardada["secundario"], guardada["acento"]) == ("#1F3A5F", "#3B82C4", "#123456")
    r = client.get(f"{MARCA}?paso=2")
    assert 'name="paleta" value="azul" data-colores' in r.text and "--m-principal:#1F3A5F" in r.text
    assert 'value="azul" data-colores="#16324F,#3B82C4,#9ED8CF,#F5F8FB">' in r.text  # ya no es la paleta: sin marcar
    # Tipografía: cada letra trae su grosor.
    r = client.get(f"{MARCA}?paso=2")
    assert "¿Cuánto quieres que destaquen los colores de tu marca?" in r.text and "Natural / Sin protagonismo" in r.text
    assert "Los colores de marca conviven con los colores naturales." in r.text and 'value="natural" checked' in r.text
    # Las paletas vienen plegadas; cada opción de color lleva su imagen.
    assert '<details class="mrc-otras"><summary>Elige una paleta</summary>' in r.text
    assert "url(/panel/static/tarjetas/marca/color-natural.webp)" in r.text
    # Cuánto destacan los colores: se pueden elegir varios, en el orden en que los toca.
    client.post(MARCA, headers=ORIGEN, data={
        "paso": "2", "paleta_antes": "", "uso_color": ["sutil", "natural"], "orden_uso": "natural,sutil",
        "principal": "#1F3A5F", "secundario": "#3B82C4", "acento": "#123456", "fondo": "#F5F8FB"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["usos_color"], guardada["uso_color"]) == (["natural", "sutil"], "natural")
    r = client.get(f"{MARCA}?paso=2")
    assert 'name="orden_uso" value="natural,sutil"' in r.text and 'value="sutil" checked' in r.text
    assert 'data-orden="orden_uso" data-campo="uso_color"' in r.text and '<em class="wiz-orden">2</em>' in r.text
    assert "How much color (the owner accepts several" in contenido.contexto_marca(guardada)
    client.post(MARCA, headers=ORIGEN, data={
        "paso": "2", "paleta_antes": "", "uso_color": "natural",
        "principal": "#1F3A5F", "secundario": "#3B82C4", "acento": "#123456", "fondo": "#F5F8FB"})
    # Tipografía: la combinación recomendada según el estilo principal (Editorial: Playfair Display + Inter).
    r = client.get(f"{MARCA}?paso=3")
    assert "Recomendada para ti" in r.text and "<h3>Playfair Display + Inter</h3>" in r.text
    assert r.text.count("Tu negocio, más cerca de tus clientes.") == 13 and "<details class=\"mrc-otras\">" in r.text
    client.post(MARCA, headers=ORIGEN, data={"paso": "3", "usar": "1", "fuente": "Oswald"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["fuente"], guardada["fuente_texto"]) == ("Playfair Display", "Inter")
    assert "✓ Es la que usas" in client.get(f"{MARCA}?paso=3").text
    # Otra letra de la lista: va en los titulares; los textos siguen con la de la combinación.
    client.post(MARCA, headers=ORIGEN, data={"paso": "3", "fuente": "Oswald"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["fuente"], guardada["peso"], guardada["fuente_texto"]) == ("Oswald", 700, "Inter")
    r = client.get(f"{MARCA}?paso=3")  # «Ver otras tipografías» sigue plegado y arriba dice cuál usa
    assert "<details class=\"mrc-otras\" open>" not in r.text and "Ahora usas Oswald + Inter" in r.text
    client.post(MARCA, headers=ORIGEN, data={"paso": "3", "fuente": "Playfair Display"})
    guardada = client.portal.call(contenido.marca_de, c)
    assert (guardada["fuente"], guardada["peso"]) == ("Playfair Display", 600)
    # Logotipo (opcional): es el último paso y lleva al resumen.
    r = client.get(f"{MARCA}?paso=4")
    assert r.text.count('name="logo_presencia"') == 5 and "Sin logotipo" in r.text and "Paso 4 de 4" in r.text
    assert "url(/panel/static/tarjetas/marca/logo-sin.webp)" in r.text and "Recursos visuales" not in r.text
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "4", "logo_presencia": "visible"}, follow_redirects=False,
                    files={"logo": ("logo.png", png(), "image/png")})
    assert r.headers["location"] == f"{MARCA}?paso=resumen"
    guardada = client.portal.call(contenido.marca_de, c)
    assert guardada["logos_presencia"] == ["visible"] and guardada["pasos"] == ["estilo", "colores", "tipografia", "logotipo"]
    # Ya configurada, Tu marca abre en el resumen, con Editar en cada parte.
    r = client.get("/panel/contenido/marca")
    assert "Así se verá tu contenido" in r.text and r.text.count(">Editar</a>") == 4 and "Completar" not in r.text
    assert "Colores propios · Natural / Sin protagonismo de marca" in r.text and "Playfair Display + Inter" in r.text
    assert "Firma visible" in r.text and "Recursos visuales" not in r.text
    assert client.get("/panel/static/fuentes/CormorantGaramond-Bold.ttf").status_code == 200
    # Las fotos del negocio que ya tenía (Tu marca ya no las pide) se conservan y se siguen usando.
    tokens, _ = client.portal.call(contenido.guardar_fotos, c, [Subida()], 1)
    guardada = {**guardada, "fotos": tokens}
    client.portal.call(c.store.set, "marca", json.dumps(guardada))
    token_foto = tokens[0]
    assert client.get(f"/media/cafe_lunar/{token_foto}.jpg").status_code == 200
    # La plantilla de inicio ya no se elige en Tu marca (pasa a Crear campaña): se conserva la guardada.
    client.portal.call(c.store.set, "marca", json.dumps({**guardada, "plantilla": "banda"}))
    client.post(MARCA, headers=ORIGEN, data={"paso": "3", "fuente": "Playfair Display"})
    assert client.portal.call(contenido.marca_de, c)["plantilla"] == "banda"

    # Un post: Claude escribe, el generador pinta (con la foto real) y queda en borrador.
    assert crear(client, c, "estrella", tema="La cartera Atelier", fotos=[token_foto], con_precio=True) == 1
    prompt, system, model = llamadas[0]
    assert "Vendemos carteras." in prompt and "La cartera Atelier" in prompt and "precio" in prompt
    assert "JSON" in system and model == "claude-sonnet-5-5"
    idea, fotos = proveedor.llamadas[0]
    assert "leather handbag" in idea and "High-end editorial magazine" in idea and "Do not include any text" in idea
    assert "A lighter secondary influence: Brutalist" in idea and "secondary #3B82C4" in idea
    assert "How much color: Prioritize the scene" in idea and "headlines in Playfair Display, secondary text in Inter" in idea
    assert len(fotos) == 1
    post = client.portal.call(c.store.post, 1)
    assert post["status"] == "borrador" and post["user_id"] == admin.PANEL and "Comenta PRECIO" in post["caption"]
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert (p["plantilla"], p["clave"], p["objetivo"]) == ("banda", "PRECIO", "estrella")

    # Editor: cambiar el texto vuelve a rotular sin gastar imagen.
    r = client.get("/panel/contenido/post/1")
    assert "Cartera Atelier" in r.text and "Aprobar y programar" in r.text
    token = client.portal.call(c.store.media, post["media_ids"][0])[0]
    antes = Path(f"data/cafe_lunar/media/{token}.jpg").read_bytes()
    assert 'id="con_precio"' in r.text and "Cancelar cambios" in r.text
    assert f'muestra.jpg?m={post["media_ids"][0]}"' in r.text  # otra imagen = otra URL: no sale la vieja del caché
    r = client.get("/panel/contenido/post/1/muestra.jpg?titulo=Otro&plantilla=minimal&precio=$9&con_precio=1")
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (1080, 1350)
    assert client.get("/panel/contenido/post/99/muestra.jpg").status_code == 404
    editado = {"plantilla": "marco", "titulo": "Cartera Atelier", "subtitulo": "", "precio": "$1,190 MXN",
               "con_precio": "1",
               "caption": "Nuevo texto #cartera", "clave_0": "precio", "respuesta_0": "¡Listo!", "dm_0": "Cuesta $1,190."}
    r = client.post("/panel/contenido/post/1", headers=ORIGEN, data={**editado, "accion": "actualizar"})
    assert "texto nuevo" in r.text
    assert Path(f"data/cafe_lunar/media/{token}.jpg").read_bytes() != antes
    assert len(proveedor.llamadas) == 1

    # Aprobar sin hora no se puede; con hora queda programado.
    r = client.post("/panel/contenido/post/1", headers=ORIGEN, data={**editado, "cuando": "", "accion": "aprobar"})
    assert "Elige cuándo sale" in r.text
    with usar(c):
        hora = (tools.now() + timedelta(days=2)).replace(hour=19, minute=0, second=0, microsecond=0)
    r = client.post("/panel/contenido/post/1", headers=ORIGEN,
                    data={**editado, "cuando": hora.strftime("%Y-%m-%dT%H:%M"), "accion": "aprobar"})
    assert "Listo: sale el" in r.text and "Programado" in r.text
    assert "Quitar de programados" in client.get("/panel/contenido/post/1").text
    post = client.portal.call(c.store.post, 1)
    assert post["status"] == "programada" and post["publish_at"] == hora.timestamp()
    assert post["caption"] == "Nuevo texto #cartera"
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == "$1,190 MXN"

    # Varias reglas de palabra clave: se agrega una con "Agregar regla +" y se puede quitar.
    r = client.get("/panel/contenido/post/1")
    assert "Agregar regla +" in r.text and 'name="clave_0"' in r.text and "data-confirma=" in r.text
    r = client.post("/panel/contenido/post/1", headers=ORIGEN,
                    data={**editado, "clave_nueva": "envío", "respuesta_nueva": "Va por DM", "dm_nueva": "Enviamos a todo MX.",
                          "accion": "regla"})
    assert "Regla guardada: «ENVÍO»." in r.text and "Regla 2 · ENVÍO" in r.text
    assert [x["clave"] for x in contenido.reglas_de(client.portal.call(contenido.pieza, c.store, 1))] == ["PRECIO", "ENVÍO"]
    r = client.post("/panel/contenido/post/1", headers=ORIGEN,
                    data={**editado, "clave_1": "ENVÍO", "dm_1": "Enviamos a todo MX.", "accion": "quitar_regla_1"})
    assert "Regla quitada." in r.text
    r = client.post("/panel/contenido/post/1", headers=ORIGEN,
                    data={**editado, "clave_nueva": "envio", "dm_nueva": "Enviamos.", "accion": "guardar"})
    assert len(contenido.reglas_de(client.portal.call(contenido.pieza, c.store, 1))) == 2

    # Le toca salir: se publica sin mandarle DM a nadie y la palabra clave ya responde comentarios.
    publicadas = []

    async def publish(urls, caption):
        publicadas.append((urls, caption))
        return "https://www.instagram.com/p/ABC123/"

    monkeypatch.setattr(c.instagram, "publish", publish, raising=False)
    client.portal.call(c.store.update_post, 1, None, 1.0)

    async def correr():
        with usar(c):
            await admin.publish_due(c.store, c.instagram)

    client.portal.call(correr)
    assert publicadas and publicadas[0][0][0].endswith(f"/media/cafe_lunar/{token}.jpg")
    assert c.instagram.sent == []
    reglas = client.portal.call(contenido.reglas, c.store)
    assert [(r.post, r.keywords, r.dm, r.public) for r in reglas] == [("ABC123", ["PRECIO"], "Cuesta $1,190.", "¡Listo!"),
                                                                    ("ABC123", ["ENVIO"], "Enviamos.", "")]
    with usar(c):
        dia = datetime.fromtimestamp(1.0, tools.tz()).date()
    r = client.get(f"/panel/contenido?fecha={dia}&ver=lista")
    assert "Ya publicados" in r.text and r.text.count("instagram.com/p/ABC123") == 1 and "Post suelto" in r.text
    r = client.get("/panel/contenido?fecha=2099-01-01&ver=lista")
    assert "instagram.com/p/DESDEAPP" in r.text and "Desde Instagram" in r.text
    assert "ya no se puede cambiar" in client.post("/panel/contenido/post/1", headers=ORIGEN,
                                                   data={**editado, "accion": "guardar"}).text


def test_pasado_el_cupo_pide_confirmar_la_imagen_extra(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 1)
    crear(client, c)
    assert len(proveedor.llamadas) == 1
    assert client.portal.call(contenido.falta_cupo, c, False) == "extra"  # se acabó el cupo: hay que confirmar
    assert crear(client, c, "historia", tema="Cómo empezamos", extra=True) == 2 and len(proveedor.llamadas) == 2
    assert client.portal.call(imagenes.usadas, c.store) == (1, 1)

    # Otra imagen para un post también pide confirmar.
    r = client.post("/panel/contenido/post/2", headers=ORIGEN, data={"titulo": "Cómo empezamos", "accion": "otra"})
    assert "Generar por US$0.25" in r.text and len(proveedor.llamadas) == 2


def test_si_falla_la_imagen_se_avisa_y_no_queda_post(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)
    monkeypatch.setattr(imagenes, "proveedor", Falso(imagenes.ErrorImagen("Esa idea no se pudo ilustrar.")))
    with pytest.raises(imagenes.ErrorImagen, match="Esa idea no se pudo ilustrar."):
        crear(client, c, "explicar")
    assert client.portal.call(c.store.posts, ("borrador",), None, admin.PANEL) == []
    assert client.portal.call(imagenes.usadas, c.store) == (0, 0)


def test_sin_precio_no_lleva_precio(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)
    crear(client, c)
    assert "<con_precio>no</con_precio>" in llamadas[0][0]
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == ""
    datos = {"plantilla": "editorial", "titulo": "Hola", "precio": "$5", "caption": "x", "accion": "guardar"}
    client.post("/panel/contenido/post/1", headers=ORIGEN, data=datos)
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == ""  # sin la casilla no se guarda


def test_si_instagram_falla_la_pagina_sale(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)

    async def falla(*a, **k):
        raise RuntimeError("caído")

    monkeypatch.setattr(c.instagram, "media_count", falla)
    monkeypatch.setattr(c.instagram, "media_page", falla)
    r = client.get("/panel/contenido?ver=lista")
    assert r.status_code == 200 and "Nada en estas fechas" in r.text


def test_publicados_de_instagram_solo_del_rango(cliente, monkeypatch):
    """De Instagram se piden páginas de 10 solo hasta pasar el inicio de las fechas que se ven."""
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)
    todos = [{"id": str(i), "caption": f"Post {i:02d}", "permalink": f"https://www.instagram.com/p/P{i:02d}/",
              "timestamp": f"2026-01-{31 - i // 2:02d}T{18 + i % 2}:00:00+0000"} for i in range(35)]
    pedidos = []

    async def media_page(limit, after="", fields=""):
        pedidos.append((limit, after, fields))
        i = int(after or 0)
        return todos[i:i + limit], str(i + limit) if i + limit < len(todos) else ""

    monkeypatch.setattr(c.instagram, "media_page", media_page)
    r = client.get("/panel/contenido?vista=dia&fecha=2026-01-30&ver=lista")
    assert r.text.count("Desde Instagram") == 2 and "Post 02" in r.text and "Post 04" not in r.text
    assert pedidos == [(10, "", "")]  # la primera página ya llega antes del 30
    r = client.get("/panel/contenido?fecha=2026-01-01&ver=lista")
    assert r.text.count("· Desde Instagram") == 35
    assert pedidos == [(10, "", ""), (10, "10", ""), (10, "20", ""), (10, "30", "")]
    r = client.get("/panel/contenido?fecha=2026-01-01&ver=lista&q=post+3")
    assert r.text.count("· Desde Instagram") == 5  # Post 30 a 34


def test_script_y_fuentes(cliente):
    client, *_ = cliente
    assert f'<script src="/panel/static/panel.js?v={panel.VERSION_JS}" defer></script>' in client.get("/panel").text
    r = client.get("/panel/static/panel.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert client.get("/panel/static/fuentes/Lora-Bold.ttf").headers["content-type"] == "font/ttf"
    assert client.get("/panel/static/fuentes/..%2F.env").status_code == 404


def test_chats_en_pausa(cliente):
    client, c, *_ = cliente
    import time
    r = client.get("/panel/asistente")
    assert '<div class="inbox" id="chats">' in r.text and "Aún no te escribe nadie" in r.text
    assert "Contestados hoy" not in r.text and "Mensajes restantes" not in r.text  # sin tarjetas de números
    client.portal.call(c.store.pause, "ana", time.time() + 3600)
    r = client.get("/panel/asistente?ver=todas")
    assert "@ana" in r.text and "Lo atiendes tú · 1 h" in r.text and "Todas<b" in r.text
    r = client.get("/panel/asistente?chat=ana")
    assert 'class="inbox abierto"' in r.text and 'href="https://ig.me/m/ana"' in r.text
    assert "Lo atiendes tú: tu asistente no le contesta en las próximas 1 h" in r.text
    assert '<button name="accion" value="tomar" aria-pressed="true" disabled>Tú</button>' in r.text
    assert client.post("/panel/pausas", data={"usuario": "ana", "accion": "reactivar"}).status_code == 403  # sin Origin
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "extender", "chat": "ana"})
    assert "sigue en pausa 12 h más" in r.text and 'class="inbox abierto"' in r.text  # sigue en el chat
    assert client.portal.call(c.store.paused_until, "ana") > time.time() + 11 * 3600
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "reactivar"})
    assert "vuelve a contestarle a @ana" in r.text and client.portal.call(c.store.paused_until, "ana") == 0
    assert "ya no estaba en pausa" in client.post("/panel/pausas", headers=ORIGEN,
                                                  data={"usuario": "beto", "accion": "reactivar"}).text


def test_conversaciones_y_tomar_un_chat(cliente):
    client, c, *_ = cliente
    store = c.store
    client.portal.call(store.add_incoming, "beto", "m1", "¿Tienen lugar mañana?")
    ids = [i for i, _ in client.portal.call(store.pending, "beto")]
    client.portal.call(store.mark_answered, "beto", ids, "Sí, a las 5.")
    client.portal.call(store.add_incoming, "caro", "m2", "Hola, ¿precios?")
    # Sin filtro abre Sin contestar, porque hay uno.
    r = client.get("/panel/asistente")
    assert 'class="on" aria-current="page">Sin contestar<b class="">1</b>' in r.text
    assert "@caro" in r.text and "Hola, ¿precios?" in r.text and "@beto" not in r.text
    r = client.get("/panel/asistente?ver=todas")
    assert "@beto" in r.text and "Asistente: Sí, a las 5." in r.text
    assert r.text.index("@caro") < r.text.index("@beto")
    assert 'href="/panel/asistente?ver=todas&amp;chat=beto#chats"' in r.text
    # El chat abierto: los mensajes en orden y el interruptor en Asistente.
    r = client.get("/panel/asistente?ver=todas&chat=beto")
    assert 'class="burbuja de-cliente">¿Tienen lugar mañana?' in r.text and 'de-bot">Sí, a las 5.' in r.text
    assert r.text.index("¿Tienen lugar mañana?</div>") < r.text.index("Sí, a las 5.</div>")
    assert '<button name="accion" value="reactivar" aria-pressed="true" disabled>Asistente</button>' in r.text
    assert 'action="/panel/responder"' in r.text and 'href="/panel/asistente?ver=todas#chats">‹ Tus chats' in r.text

    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "beto", "accion": "tomar", "chat": "beto"})
    assert "el asistente no le contesta a @beto en 12 h" in r.text and "Lo atiendes tú" in r.text
    assert client.portal.call(store.paused_until, "beto") > 0
    assert "No encontramos ese chat" in client.post("/panel/pausas", headers=ORIGEN,
                                                    data={"usuario": "nadie", "accion": "tomar"}).text
    # Un chat que no es de la cuenta no se abre.
    r = client.get("/panel/asistente?chat=nadie")
    assert 'class="inbox abierto"' not in r.text and "Elige una conversación" in r.text


def test_contestar_desde_el_panel(cliente):
    import time
    client, c, *_ = cliente
    store = c.store
    client.portal.call(store.add_incoming, "caro", "m2", "Hola, ¿precios?")
    assert client.post("/panel/responder", data={"usuario": "caro", "texto": "Hola"}).status_code == 403  # sin Origin
    r = client.post("/panel/responder", headers=ORIGEN, data={"usuario": "caro", "texto": "  "})
    assert "Escribe tu respuesta" in r.text and c.instagram.sent == []
    r = client.post("/panel/responder", headers=ORIGEN,
                    data={"usuario": "caro", "texto": "Te paso la lista ahora.", "ver": "todas"})
    assert c.instagram.sent == [("caro", "Te paso la lista ahora.")]
    assert "Enviado." in r.text and 'class="burbuja de-ti">Te paso la lista ahora.' in r.text
    assert client.portal.call(store.paused_until, "caro") > time.time() + 11 * 3600  # el asistente se calla
    assert "No encontramos ese chat" in client.post("/panel/responder", headers=ORIGEN,
                                                    data={"usuario": "nadie", "texto": "x"}).text
    # Pasadas 24 h desde su último mensaje, Instagram ya no deja: ni caja ni envío.
    client.portal.call(store.db.execute, "UPDATE messages SET created_at = created_at - 90000 WHERE role = 'user'")
    r = client.get("/panel/asistente?chat=caro")
    assert "Instagram solo deja contestar dentro de las 24 horas" in r.text and 'action="/panel/responder"' not in r.text
    r = client.post("/panel/responder", headers=ORIGEN, data={"usuario": "caro", "texto": "¿Sigues ahí?"})
    assert "Ya pasaron 24 horas" in r.text and len(c.instagram.sent) == 1


def test_chats_de_10_en_10_con_buscador(cliente):
    client, c, *_ = cliente
    for i in range(23):
        client.portal.call(c.store.add_incoming, f"u{i:02d}", f"m{i}", f"Hola, pregunta {i:02d}")
    client.portal.call(c.store.set, "username:u05", "@lupita.flores")
    r = client.get("/panel/asistente")
    assert r.text.count('class="conv') == 10 and "Sin contestar<b" in r.text
    assert '<span class="on" aria-current="page">1</span>' in r.text and 'href="/panel/asistente?ver=sin&amp;p=2#chats"' in r.text
    assert '<span class="apagada" aria-hidden="true">‹</span>' in r.text and 'aria-label="Siguiente"' in r.text
    r = client.get("/panel/asistente?p=3")
    assert r.text.count('class="conv') == 3 and "pregunta 00" in r.text and "pregunta 22" not in r.text
    assert client.get("/panel/asistente?p=99").text.count('class="conv') == 3  # se queda en la última
    # El filtro se mantiene al cambiar de página y al buscar; el buscador se manda solo (data-vivo).
    r = client.get("/panel/asistente?ver=todas&p=2")
    assert 'href="/panel/asistente?ver=todas&amp;p=3#chats"' in r.text
    r = client.get("/panel/asistente?q=lupita")
    assert r.text.count('class="conv') == 1 and "chat=u05" in r.text and 'class="paginas"' not in r.text
    assert 'value="lupita"' in r.text and "data-vivo" in r.text and "1 chat</p>" in r.text
    r = client.get("/panel/asistente?ver=sin&q=pregunta+1")
    assert r.text.count('class="conv') == 10 and '<input type="hidden" name="ver" value="sin">' in r.text
    assert "No encontramos chats con «zzz»" in client.get("/panel/asistente?q=zzz").text


TEMAS = {"temas": [{"tema": "Horario de fin de semana", "personas": 4, "ejemplo": "¿Abren el sábado?",
                    "objetivo": "explicar", "idea": "Post con el horario del sábado"},
                   {"tema": "x", "personas": "muchas", "objetivo": "otro"}],
          "elogios": ["Les encanta la atención", 3, ""]}
PLAN = {"posts": [{"objetivo": "explicar", "tema": "Horario del sábado", "por_que": "Te preguntaron 4 veces."},
                  {"objetivo": "marca", "tema": "Quiénes somos", "por_que": "Para que te conozcan."},
                  {"objetivo": "estrella", "tema": "El facial", "por_que": "Es lo que más te piden."}]}


@pytest.fixture
def con_mensajes(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system, model))
        if "Agrupas" in system:
            return json.dumps(TEMAS)
        if "3 posts de esta semana" in system:
            return json.dumps(PLAN)
        return json.dumps(POST)

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    monkeypatch.setattr(c, "imagenes_mes", 10)
    client.portal.call(c.store.add_incoming, "ana", "m1", "Hola, ¿abren el sábado?")
    client.portal.call(c.store.add_comment, "k1", "beto", "post1", "¿Horario del sábado?")
    return client, c, llamadas, proveedor


def test_lo_que_mas_preguntan(con_mensajes):
    """Los temas de DMs y comentarios los usa Campañas; sus páginas viejas llevan a Campañas."""
    from igbot import semana
    client, c, llamadas, _ = con_mensajes
    datos = client.portal.call(semana.temas, c)
    assert datos["temas"][0]["tema"] == "Horario de fin de semana" and datos["mensajes"] == 2
    assert datos["elogios"] == ["Les encanta la atención"]
    prompt, system, model = llamadas[0]
    assert "(DM, persona 1) Hola, ¿abren el sábado?" in prompt and "(comentario, persona 2)" in prompt
    assert model == "claude-haiku-5-5"
    client.portal.call(semana.temas, c)
    assert len(llamadas) == 1  # una vez al día
    for vieja in ("ideas", "semana", "nuevo"):
        r = client.get(f"/panel/contenido/{vieja}", follow_redirects=False)
        assert r.headers["location"] == "/panel/contenido/campanas"
    menu = client.get("/panel/contenido").text
    assert "Esta semana" not in menu and "Contenido sugerido" not in menu and "Crear post" not in menu
    assert 'href="/panel/contenido/campanas/nueva">+ Nueva campaña' in menu


def test_diseno_libre_con_el_estilo_de_sus_fotos(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    # Fotos del negocio guardadas antes en Tu marca (ya no se piden ahí, pero se conservan y guían el estilo).
    tokens, _ = client.portal.call(contenido.guardar_fotos, c, [Subida()], 1)
    guardada = {"fotos": tokens, "fotos_estilo": True}
    # El modo y "Describe tu estilo" se guardaban en Tu marca; ahora pasan a Crear campaña y se conservan.
    client.portal.call(c.store.set, "marca", json.dumps({
        **guardada, "principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": 700,
        "estilo": "premium", "plantilla": "editorial", "modo": "libre",
        "estilo_propio": "Fondos oscuros, minimalista, el celular como protagonista."}))

    crear(client, c)
    assert "<estilo_de_la_marca>Estilo visual: Fondos oscuros" in llamadas[-1][0] and "acento #E8590C" in llamadas[-1][0]
    prompt, fotos = proveedor.llamadas[-1]
    assert 'Headline (largest): "Cartera Atelier"' in prompt and "#E8590C" in prompt
    assert "celular como protagonista" in prompt and "brand's own photos" in prompt and len(fotos) == 1
    assert "Photo style, as the owner describes it" in prompt and "Dark background" not in prompt
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert p["plantilla"] == "libre"
    token = client.portal.call(c.store.media, client.portal.call(c.store.post, 1)["media_ids"][0])[0]
    assert Path(f"data/cafe_lunar/media/{token}.jpg").is_file()
    assert not Path(f"data/cafe_lunar/media/{token}.base.jpg").exists()

    r = client.get("/panel/contenido/post/1")
    assert "Rehacer el diseño" in r.text and "data-vista" not in r.text and 'name="plantilla"' not in r.text
    r = client.post("/panel/contenido/post/1", headers=ORIGEN,
                    data={"titulo": "Otro título", "caption": "x", "accion": "guardar"})
    assert "Guardado." in r.text and client.portal.call(contenido.pieza, c.store, 1)["plantilla"] == "libre"
    client.post("/panel/contenido/post/1", headers=ORIGEN, data={"titulo": "Otro título", "caption": "x", "accion": "otra"})
    assert 'Headline (largest): "Otro título"' in proveedor.llamadas[-1][0]

    # Con plantilla en este post, aunque la marca diga libre.
    crear(client, c, modo="plantilla")
    assert client.portal.call(contenido.pieza, c.store, 2)["plantilla"] == "editorial"
    assert "Do not include any text" in proveedor.llamadas[-1][0]


def test_el_estilo_depende_del_modo_del_post(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    # Una marca guardada con el formulario de antes: su estilo "limpio" ahora es "Minimalista".
    client.portal.call(c.store.set, "marca", json.dumps({
        "principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": 700,
        "estilo": "limpio", "estilo_propio": "Fondos oscuros y detalles en naranja."}))
    # Con plantilla mandan los estilos de la lista; en libre, su descripción.
    crear(client, c, modo="plantilla")
    assert "Minimalist premium" in proveedor.llamadas[-1][0] and "naranja" not in proveedor.llamadas[-1][0]
    assert "objetos que deban salir siempre): Minimalista." in llamadas[-1][0] and "naranja" not in llamadas[-1][0]

    crear(client, c, modo="libre")
    assert "naranja" in proveedor.llamadas[-1][0] and "Minimalist premium" not in proveedor.llamadas[-1][0]
    assert "Estilo visual: Fondos oscuros y detalles en naranja." in llamadas[-1][0]


def test_tu_marca_menu_de_pasos_y_marca_de_antes(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get(f"{MARCA}?paso=3")
    # El menú de pasos: cada uno es un enlace; el actual marcado y el resumen al final.
    assert all(f'href="{MARCA}?paso={i}"' in r.text for i in (1, 2, 3, 4, "resumen"))
    assert f'href="{MARCA}?paso=5"' not in r.text
    assert f'href="{MARCA}?paso=3" class="actual"' in r.text and "Paso 3 de 4" in r.text
    assert r.text.count('name="fuente"') == 12 and "font-family:'Oswald';font-weight:700" in r.text
    assert f'href="{MARCA}?paso=2">← Anterior' in r.text
    # Las tarjetas usan su imagen de static/tarjetas/marca (los 9 estilos la tienen); sin imagen, su color.
    from igbot import marca as tu_marca
    r = client.get(f"{MARCA}?paso=1")
    assert all(f"url(/panel/static/tarjetas/marca/{k}.webp)" in r.text for k in estilos.ESTILOS)
    assert tu_marca.imagen_tarjeta("sin-imagen", "#123456") == '<span class="wiz-img" style="--fondo:#123456"></span>'
    assert client.get("/panel/static/tarjetas/marca/editorial.webp").status_code == 200
    assert client.get("/panel/static/tarjetas/otra/editorial.webp").status_code == 404
    assert client.get("/panel/static/tarjetas/marca/..%2Fpanel.js").status_code == 404
    # Una marca guardada con el formulario de antes cuenta como lista y abre en el resumen; lo que falta dice Completar.
    client.portal.call(c.store.set, "marca", json.dumps({"principal": "#111111", "acento": "#E8590C", "fondo": "#F7F5F1",
                                                         "fuente": "Lora", "peso": 700, "estilo": "premium"}))
    assert client.portal.call(panel.marca_lista, c)
    r = client.get(MARCA)
    assert "Así se verá tu contenido" in r.text and "<b>Editorial</b><small>Estilo principal" in r.text
    assert r.text.count(">Completar</a>") == 1
    assert 'name="estilos" value="editorial" checked' in client.get(f"{MARCA}?paso=1").text
    # Sin colores no está lista: abre en el primer paso que falta.
    # Los estilos de la versión anterior pasan a los nuevos (urbano y geométrico: brutalista y bento grid).
    client.portal.call(c.store.set, "marca", json.dumps({"estilos": ["urbano", "geometrico", "minimal"]}))
    assert 'name="orden" value="brutalista,bento,minimalista"' in client.get(f"{MARCA}?paso=1").text
    assert not client.portal.call(panel.marca_lista, c)
    assert "Paso 2 de 4" in client.get(MARCA).text

    # «Oscuro» y «Alto contraste» de versiones anteriores son ahora Natural.
    assert contenido.uso_color({"uso_color": "oscuro"}) == "natural" and contenido.uso_color({"uso_color": "contraste"}) == "natural" and contenido.uso_color({}) == ""
    # «No estoy seguro, recomiéndame uno»: marca tres según el giro de Perfil de empresa, sin guardar.
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "1", "accion": "recomendar"})
    assert "Te recomendamos Minimalista, Editorial y Bento Grid (llena el giro" in r.text
    client.portal.call(c.store.set, "empresa", json.dumps({"giro": "Tecnología y software"}))
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "1", "accion": "recomendar"})
    assert "Te recomendamos Neofuturista, Cristalino y Bento Grid para un negocio de tecnología y software" in r.text
    assert 'name="orden" value="neofuturista,cristalino,bento"' in r.text
    assert client.portal.call(contenido.marca_de, c)["estilos"] == ["urbano", "geometrico", "minimal"]


def test_insights_de_un_post(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    pedidos = []

    async def insights(media_id):
        pedidos.append(media_id)
        return {"reach": 1520, "views": 2100, "likes": 87, "comments": 9, "saved": 14, "shares": 6}
    monkeypatch.setattr(c.instagram, "media_insights", insights, raising=False)
    r = client.get("/panel/contenido?fecha=2099-01-01&ver=lista")
    assert 'href="/panel/contenido/insights/1"' in r.text
    r = client.get("/panel/contenido/insights/1")
    assert "Alcance" in r.text and "1,520" in r.text and "Guardados" in r.text and pedidos == ["1"]
    assert client.get("/panel/contenido/insights/999").status_code == 404
    assert client.get("/panel/contenido/insights/abc").status_code == 404

    async def sin_permiso(media_id):
        raise RuntimeError("Instagram respondió 400")
    monkeypatch.setattr(c.instagram, "media_insights", sin_permiso, raising=False)
    r = client.get("/panel/contenido/insights/1")
    assert r.status_code == 200 and "vuelve a conectarla" in r.text


def test_ajustes_y_conexion(cliente, monkeypatch):
    from igbot import agent, ajustes
    from igbot.cuenta import usar
    client, c, *_ = cliente
    ajustes._revision.clear()
    assert 'name="pausa" value="12" checked' in client.get("/panel/asistente/configura?paso=equipo").text
    r = client.get("/panel/asistente")
    assert 'id="ajustes"' not in r.text  # Horario y avisos va en Configura
    # Conexión va en el menú lateral (con su semáforo) y abre su propia vista.
    assert re.search(r'href="/panel/conexion">Conexión <span class="semaforo sem-\w+"></span>', r.text)
    assert "Recibiendo mensajes" not in r.text and 'id="conexion"' not in r.text
    r = client.get("/panel/conexion")
    assert "Recibiendo mensajes" in r.text and 'id="ajustes"' not in r.text and "Gasto de hoy" not in r.text
    assert '<a href="/panel/conexion" class="on">' in r.text and "compacta delgada" in r.text
    assert "Volver a tus conversaciones" not in r.text
    assert client.get("/panel/asistente?vista=conexion", follow_redirects=False).headers["location"] == "/panel/conexion"
    # Ya no está en el submenú de Tu asistente: vive en el menú de tu cuenta, abajo.
    assert "Tu cuenta · Conexión" in r.text and 'class="lat-item on"' not in r.text
    assert r.text.index('href="/panel/conexion"') > r.text.index('class="perfil"')
    assert client.get("/panel/asistente/conexion", follow_redirects=False).headers["location"] == "/panel/conexion"
    # Una dirección que no existe: página 404 con el menú, no el JSON de FastAPI.
    r = client.get("/panel/no-existe")
    assert r.status_code == 404 and "Esta página no existe" in r.text and 'class="perfil"' in r.text
    assert "Límite de gasto" not in r.text  # el gasto solo lo ve la cuenta principal

    datos = {"pausa": "24", "dias": ["1", "2", "3", "4", "5", "6"], "desde": "10:00", "hasta": "19:30",
             "fuera": "fijo", "fuera_texto": "Te contestamos el lunes.", "aviso_correo": "1", "correo": "ana@cafe.mx"}
    assert client.post("/panel/ajustes", data=datos).status_code == 403  # sin Origin
    r = client.post("/panel/ajustes", headers=ORIGEN, data=datos)
    assert "ya trabaja con estos ajustes" in r.text and 'name="pausa" value="24" checked' in r.text
    assert c.human_pause_hours == 24 and c.business_days == [1, 2, 3, 4, 5, 6]
    assert c.business_hours == "10:00-19:30" and c.owner_email == "ana@cafe.mx" and not c.aviso_dm
    with usar(c):
        prompt = agent.system_prompt()
    assert "lun–sáb 10:00–19:30" in prompt and '"Te contestamos el lunes."' in prompt
    # Se guardan en su base: al arrancar otra vez se vuelven a aplicar.
    c.human_pause_hours = 12
    client.portal.call(ajustes.cargar, c)
    assert c.human_pause_hours == 24

    # Valores raros no se guardan: se queda lo que había.
    client.post("/panel/ajustes", headers=ORIGEN, data={"pausa": "999", "desde": "25:00", "hasta": "x", "correo": "no"})
    assert c.human_pause_hours == 24 and c.business_hours == "10:00-19:30" and c.owner_email == "ana@cafe.mx"
    assert c.fuera_horario == "" and c.business_days == [1, 2, 3, 4, 5, 6]

    # El paso Tu equipo de Configura guarda Horario y avisos junto con qué decir si piden una persona.
    r = client.get("/panel/asistente/configura?paso=equipo")
    assert "Guardar ajustes" not in r.text and r.text.index('name="humano"') < r.text.index('name="pausa"')
    r = client.post("/panel/asistente/paso", headers=ORIGEN,
                    data={**datos, "pausa": "48", "paso": "equipo", "humano": "Te escribe Ana."},
                    follow_redirects=False)
    assert r.headers["location"] == "/panel/asistente/configura?paso=resumen&ok=1"
    assert c.human_pause_hours == 48 and c.business_hours == "10:00-19:30"
    assert "Te escribe Ana." in Path(c.prompt_path).read_text()


def test_conexion_avisa_si_falta_el_permiso_de_estadisticas(cliente, monkeypatch):
    from igbot import ajustes
    client, c, *_ = cliente
    ajustes._revision.clear()

    async def sin_permiso(media_id):
        raise RuntimeError("Instagram respondió 400")
    monkeypatch.setattr(c.instagram, "media_insights", sin_permiso, raising=False)
    r = client.get("/panel/asistente")
    assert "Hay algo que revisar en tu conexión" in r.text and "sem-aviso" in r.text
    r = client.get("/panel/conexion")
    assert "Estadísticas: falta" in r.text and 'href="/conectar/instagram"' in r.text and "Hay algo que revisar" in r.text


def test_bienvenida_con_contenido_y_lo_que_falta(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)

    async def con_empresa(c):
        return True

    monkeypatch.setattr(panel, "empresa_lista", con_empresa)  # ya llenó su Perfil de empresa
    r = client.get("/panel")
    assert "Configurar tu asistente" in r.text and "Configurar tu marca" in r.text and "Tu plan Asistente virtual" not in r.text
    assert client.get("/panel", follow_redirects=False).headers["location"] != "/panel/bienvenida"  # solo una vez
    client.portal.call(c.store.set, "perfil", "")
    client.portal.call(c.store.set, "asistente_listo", "0")
    assert "Te falta configurar <b>Tu asistente</b>" in client.get("/panel/contenido").text
    client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "personalidad", "personalidad": "cercano"})
    assert "Te falta configurar <b>Tu marca</b>" in client.get("/panel/asistente").text
    assert "Te falta configurar <b>Tu marca</b>" in client.get("/panel/contenido").text
    assert "Te falta configurar" not in client.get("/panel/contenido/marca").text  # ya está ahí
    client.post(MARCA, headers=ORIGEN, data={"paso": "1", "estilos": "minimalista"})
    assert "Te falta configurar <b>Tu marca</b>" in client.get("/panel/asistente").text  # faltan los colores
    client.post(MARCA, headers=ORIGEN, data={"paso": "2", "paleta": "mono"})
    assert "Te falta configurar" not in client.get("/panel/asistente").text
    assert "Te falta configurar" not in client.get("/panel/contenido").text


def test_encabezados_sin_titulo_ni_numeros(cliente, monkeypatch):
    """Arriba solo va el antetítulo y sus botones: ni título, ni bajada, ni las tarjetas de números."""
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 40)
    monkeypatch.setattr(c, "posts_mes", 12)
    monkeypatch.setattr(c, "mensajes_mes", 1000)
    r = client.get("/panel/contenido")
    assert '<p class="antetitulo">Contenido · Calendario</p>' in r.text and "+ Nueva campaña" in r.text
    assert "Posts restantes" not in r.text and 'class="hoy kpis"' not in r.text
    assert '<nav class="pestanas">' not in r.text and 'class="lat-sub"' in r.text
    for ruta in ("/panel/contenido", "/panel/contenido/marca", "/panel/contenido/campanas",
                 "/panel/contenido/campanas/nueva", "/panel/asistente", "/panel/asistente/configura",
                 "/panel/probar", "/panel/conexion", "/panel/empresa", "/panel/resultados"):
        texto = client.get(ruta).text
        assert "compacta delgada" in texto and "<h1>" not in texto and 'class="bajada"' not in texto, ruta
    r = client.get("/panel/asistente")
    assert "Mensajes restantes" not in r.text and 'class="hoy kpis"' not in r.text and 'class="estado"' in r.text


def test_crear_post_con_ajustes_solo_para_ese_post(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    client.portal.call(c.store.set, "marca", json.dumps({
        "principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": 700,
        "estilos": ["minimal"], "plantilla": "editorial", "modo": "plantilla"}))
    # Otra plantilla: banda, otro acento y estilo lujo; la marca no cambia.
    crear(client, c, ajustes={"plantilla": "banda", "acento": "#2255AA", "estilo": "lujo", "fuente": "Lora",
                              "principal": "#111111", "fondo": "#000000"})
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert p["plantilla"] == "banda" and p["marca"]["acento"] == "#2255AA" and p["marca"]["fuente"] == "Lora"
    assert "High-end editorial magazine" in proveedor.llamadas[-1][0]  # el estilo de este post (lujo de antes)
    marca = client.portal.call(contenido.marca_de, c)
    assert marca["acento"] == "#E8590C" and marca["plantilla"] == "editorial"
    assert client.portal.call(contenido.marca_post, c, p)["acento"] == "#2255AA"

    # Otro diseño libre, con su propia descripción.
    crear(client, c, modo="libre", ajustes={"estilo_propio": "Estilo revista, fondo blanco."})
    p = client.portal.call(contenido.pieza, c.store, 2)
    assert p["plantilla"] == "libre" and "Estilo revista" in proveedor.llamadas[-1][0]

    # Con mi plantilla: lo de la marca, sin ajustes guardados.
    crear(client, c)
    p = client.portal.call(contenido.pieza, c.store, 3)
    assert p["plantilla"] == "editorial" and "marca" not in p


def test_configura_tu_asistente_en_su_propia_pagina(cliente):
    client, c, *_ = cliente
    # Conversaciones y el formulario van en direcciones distintas.
    r = client.get("/panel/asistente")
    assert 'id="chats"' in r.text and 'id="configura"' not in r.text and "Configurar tu asistente" not in r.text
    assert '<a href="/panel/asistente" class="on">Conversaciones' in r.text
    r = client.get("/panel/asistente/configura")
    assert 'class="asis"' in r.text and 'id="chats"' not in r.text and "Tu asistente · Configura" in r.text
    assert '<a href="/panel/asistente/configura" class="on">Configura tu asistente' in r.text
    # Sin configurar: las conversaciones invitan a configurarlo.
    client.portal.call(c.store.set, "asistente_listo", "0")
    client.portal.call(c.store.set, "perfil", "")
    assert 'href="/panel/asistente/configura">Configurar tu asistente' in client.get("/panel/asistente").text


def test_un_token_inventado_no_lee_otro_archivo(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    crear(client, c, "estrella", fotos=["../../.env"])
    assert client.portal.call(contenido.pieza, c.store, 1)["fotos"] == []


def test_precio_escrito_por_el_dueno(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    crear(client, c, con_precio=True, precio="Desde $990 MXN")
    assert "<precio_del_dueño>Desde $990 MXN</precio_del_dueño>" in llamadas[-1][0]
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == "Desde $990 MXN"


def test_filtro_pidieron_una_persona(cliente):
    import time
    client, c, *_ = cliente
    store = c.store
    client.portal.call(store.add_incoming, "beto", "m1", "¿Tienen lugar mañana?")
    client.portal.call(store.add_incoming, "caro", "m2", "Quiero hablar con alguien")
    # El asistente pasó a caro con una persona (pasar_a_humano): pausa + evento con el motivo.
    client.portal.call(store.pause, "caro", time.time() + 12 * 3600)
    client.portal.call(store.add_event, "caro", "humano", {"motivo": "Quiere negociar el precio"})
    client.portal.call(store.pause, "dani", time.time() + 3600)  # este lo tomó el dueño

    r = client.get("/panel/asistente")
    assert "Sin contestar<b" in r.text and "Pidieron una persona<b" in r.text and 'Todas<b class="gris">3</b>' in r.text
    assert "Abiertas" not in r.text and "En pausa" not in r.text
    r = client.get("/panel/asistente?ver=persona")
    assert "@caro" in r.text and "Pidió hablar con una persona" in r.text
    assert "@beto" not in r.text and "@dani" not in r.text
    r = client.get("/panel/asistente?ver=persona&chat=caro")
    assert "<b>Pidió hablar con una persona.</b> Motivo: Quiere negociar el precio." in r.text
    r = client.get("/panel/asistente?ver=abiertas")  # enlace viejo: cae en Sin contestar
    assert "@beto" in r.text and "@caro" not in r.text


def test_menu_js_antes_de_pintar(cliente):
    client, *_ = cliente
    html = client.get("/panel").text
    menu = html.index(f'<script src="/panel/static/menu.js?v={panel.VERSION_MENU}"></script>')
    assert menu < html.index("</head>")  # sin defer y en el <head>: se pliega antes de pintar
    r = client.get("/panel/static/menu.js")
    assert r.status_code == 200 and "menu-plegado" in r.text


def test_identidad_de_marca_y_logo(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    # Nombre, frase y carácter ya no se piden en Tu marca (pasan a Perfil de empresa), pero lo guardado se conserva.
    client.portal.call(c.store.set, "marca", json.dumps({"nombre": "Café Lunar", "frase": "Café de especialidad.",
                                                         "caracter": ["Cercana", "Premium", "Natural"]}))
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "1", "estilos": "claymorfismo"})
    marca = client.portal.call(contenido.marca_de, c)
    assert marca["nombre"] == "Café Lunar" and marca["estilos"] == ["claymorfismo"]
    assert 'name="nombre"' not in client.get(f"{MARCA}?paso=1").text
    negocio = client.portal.call(contenido.negocio_de, c)
    assert "Nombre de la marca: Café Lunar" in negocio and "Carácter: Cercana, Premium, Natural" in negocio
    # Logo: se sube en el paso 4, sale en sus tarjetas y se puede quitar.
    png_ = io.BytesIO()
    Image.new("RGB", (64, 64), "#123456").save(png_, "PNG")
    client.post(MARCA, headers=ORIGEN, data={"paso": "4", "logo_presencia": ["discreta", "siempre"], "orden_logo": "siempre"},
                files={"logo": ("logo.png", png_.getvalue(), "image/png")})
    logo = client.portal.call(contenido.marca_de, c)["logo"]
    assert contenido.archivo(c, logo).is_file()
    r = client.get(f"{MARCA}?paso=4")
    assert 'alt="Tu logo"' in r.text and f'src="/media/{c.media_slug}/{logo}.jpg?v=' in r.text
    assert 'name="logo_presencia" value="siempre" checked' in r.text and 'name="orden_logo" value="siempre,discreta"' in r.text
    assert "Siempre presente + Firma discreta" in client.get(f"{MARCA}?paso=resumen").text
    # También sale en el círculo de tu cuenta, abajo en el menú lateral, en todas las páginas.
    assert f'<img class="lat-avatar" src="/media/{c.media_slug}/{logo}.jpg?v=' in client.get("/panel/asistente").text
    r = client.post(MARCA, headers=ORIGEN, data={"paso": "4", "accion": "quitar_logo"})
    assert "quitamos tu logo" in r.text and not contenido.archivo(c, logo).is_file()
    assert '<img class="lat-avatar"' not in r.text and '<span class="lat-avatar">' in r.text


def test_menu_lateral_despliega_secciones(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get("/panel/asistente").text
    # Las dos secciones traen su submenú; el de la sección en la que estás sale abierto.
    assert 'data-sub="asistente" aria-expanded="true"' in r and 'data-sub="contenido" aria-expanded="false"' in r
    assert '<div class="lat-sub" id="sub-asistente">' in r and '<div class="lat-sub cerrado" id="sub-contenido">' in r


def test_posicionamiento_sale_de_configura_y_llega_a_los_posts(cliente, monkeypatch):
    from igbot import posicionamiento
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    llamadas = []

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append(prompt)
        return json.dumps({"ofrece": "Carteras de piel", "cliente_ideal": "Mujeres de 25 a 40",
                           "distinto": "Piel <b>mexicana</b> cosida a mano", "competencia": "Marcas de Asia"})

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    perfil = {"negocio": "Carteras de piel hechas a mano.", "precios": "Cartera mini $900",
              "faq": [{"p": "¿Envían?", "r": "Sí, a todo México."}]}
    client.portal.call(posicionamiento.actualizar, c, perfil)
    assert "Cartera mini $900" in llamadas[0] and "¿Envían? Sí, a todo México." in llamadas[0]
    negocio = client.portal.call(contenido.negocio_de, c)
    assert "- Qué ofrece: Carteras de piel" in negocio and "Piel &lt;b&gt;mexicana&lt;/b&gt;" in negocio
    assert "- Con quién lo comparan (no la nombres en los posts): Marcas de Asia" in negocio
    client.portal.call(posicionamiento.actualizar, c, perfil)  # sin cambios en Configura no se vuelve a pedir
    assert len(llamadas) == 1
    # Ya no vive en Tu marca.
    assert "lo que te hace distinto" not in client.get("/panel/contenido/marca").text


def test_personalidad_sin_imagenes_y_con_toque_personal(cliente):
    client, c, *_ = cliente
    r = client.get("/panel/asistente/configura?paso=personalidad")
    assert "<img" not in r.text.split('class="as-tarjetas"')[1].split("</fieldset>")[0]
    assert "Comprensivo" not in r.text and "Ideal para:" in r.text and 'name="toque"' in r.text
    assert 'name="toque_on" value="1">' in r.text  # plegado mientras no escriba nada
    r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False,
                    data={"paso": "personalidad", "personalidad": "empatico", "toque": "Que explique <todo> con calma."})
    assert r.status_code == 303
    perfil = client.portal.call(panel.perfil, c)
    assert perfil["toque"] == "Que explique <todo> con calma." and perfil["personalidad"] == "empatico"
    prompt = Path(c.prompt_path).read_text()
    assert "<como_quiere_que_hables>\nQue explique &lt;todo&gt; con calma.\n</como_quiere_que_hables>" in prompt


def test_reglas_opcionales_plegadas_y_casilla_vacia_las_borra(cliente):
    client, c, *_ = cliente
    r = client.get("/panel/asistente/configura?paso=reglas")
    assert 'name="no_hacer_on" value="1">' in r.text and 'name="pasar_equipo_on"' in r.text
    assert 'name="instrucciones_on" value="1" checked>' in r.text  # ya trae "Vendemos carteras."
    datos = {"paso": "reglas", "opcionales": "1", "no_hacer": "No hablar de precios.", "no_hacer_on": "1",
             "pasar_equipo": "Esto no se guarda", "instrucciones": "Vendemos carteras."}
    client.post("/panel/asistente/paso", headers=ORIGEN, data=datos)
    perfil = client.portal.call(panel.perfil, c)
    assert perfil["no_hacer"] == "No hablar de precios." and perfil["pasar_equipo"] == "" and perfil["instrucciones"] == ""
