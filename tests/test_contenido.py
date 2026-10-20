"""Contenido en el panel: marca, crear post, editarlo, aprobarlo y publicarlo."""
import io
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as bot  # noqa: E402
from igbot import admin, contenido, imagenes, tools  # noqa: E402
from igbot.cuenta import usar  # noqa: E402
from test_conectar import setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402
from test_imagenes import Falso  # noqa: E402

ORIGEN = {"Origin": "https://bot.skandar.online"}

POST = {"titulo": "Cartera Atelier", "subtitulo": "Piel italiana, hecha a mano.", "precio": "Desde $1,290 MXN",
        "caption": "Hecha a mano en piel italiana. Comenta PRECIO y te mandamos los colores.\n#cartera #piel",
        "palabra_clave": "Precio", "respuesta_comentario": "¡Te mandamos la info por mensaje!",
        "mensaje_dm": "La cartera Atelier cuesta $1,290 MXN.", "imagen": "A leather handbag on a stone table."}


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 1000), (120, 90, 60)).save(out, "PNG")
    return out.getvalue()


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
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
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
        client.post("/panel/guardar", headers=ORIGEN, data={"accion": "guardar", "prompt": "Vendemos carteras."})
        yield client, c, llamadas, proveedor


def test_sin_contenido_en_el_plan(cliente):
    client, c, *_ = cliente
    assert c.imagenes_mes == 0
    assert "Contenido</a>" not in client.get("/panel").text  # el menú solo sale si su plan lo tiene
    assert "solo incluye el asistente" in client.get("/panel/contenido").text
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert "solo incluye el asistente" in r.text


def test_marca_crear_aprobar_y_publicar(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    assert 'href="/panel/contenido">Contenido</a>' in client.get("/panel").text
    assert "Aún no tienes posts" in client.get("/panel/contenido").text
    assert client.get("/panel/contenido/nuevo").text.count('name="objetivo"') == 5

    # Tu marca: colores, letra y una foto del producto.
    marca = {"principal": "1f3a5f", "acento": "#F7D046", "fondo": "#zzz", "pantone_acento": "116 C",
             "fuente": "Playfair Display", "peso": "700", "estilo": "premium", "plantilla": "banda"}
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data={**marca, "accion": "vista"})
    assert "Pulsa Guardar" in r.text and client.portal.call(contenido.marca_de, c) == {}  # vista no guarda
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data={**marca, "accion": "subir"},
                    files={"fotos": ("cartera.png", png(), "image/png")})
    assert "1 foto nueva" in r.text
    guardada = client.portal.call(contenido.marca_de, c)
    assert guardada["principal"] == "#1F3A5F" and guardada["fondo"] == "" and guardada["pantone_acento"] == "116 C"
    assert guardada["fuente"] == "Playfair Display" and len(guardada["fotos"]) == 1
    token_foto = guardada["fotos"][0]
    r = client.get("/panel/contenido/muestra.jpg?fuente=Lora&acento=%23000000")
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (1080, 1350)
    assert client.get(f"/media/cafe_lunar/{token_foto}.jpg").status_code == 200

    # Crear post: Claude escribe, Gemini pinta (con la foto real) y queda en borrador.
    assert client.post("/panel/contenido/nuevo", data={"objetivo": "estrella"}).status_code == 403  # sin Origin
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN,
                    data={"objetivo": "estrella", "tema": "La cartera Atelier", "foto_0": token_foto})
    assert r.text.index('id="generando"') < r.text.index("#generando{display:none}")
    assert 'url=/panel/contenido/post/1"' in r.text and r.text.endswith("</html>")
    prompt, system, model = llamadas[0]
    assert "Vendemos carteras." in prompt and "La cartera Atelier" in prompt and "precio" in prompt
    assert "JSON" in system and model == "claude-sonnet-5-5"
    idea, fotos = proveedor.llamadas[0]
    assert "leather handbag" in idea and "Dark background" in idea and "Do not include any text" in idea
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
    editado = {"plantilla": "marco", "titulo": "Cartera Atelier", "subtitulo": "", "precio": "$1,190 MXN",
               "caption": "Nuevo texto #cartera", "clave": "precio", "respuesta": "¡Listo!", "dm": "Cuesta $1,190."}
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
    post = client.portal.call(c.store.post, 1)
    assert post["status"] == "programada" and post["publish_at"] == hora.timestamp()
    assert post["caption"] == "Nuevo texto #cartera"

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
    assert [(r.post, r.keywords, r.dm, r.public) for r in reglas] == [("ABC123", ["PRECIO"], "Cuesta $1,190.", "¡Listo!")]
    r = client.get("/panel/contenido")
    assert "Publicados" in r.text and "instagram.com/p/ABC123" in r.text
    assert "ya no se puede cambiar" in client.post("/panel/contenido/post/1", headers=ORIGEN,
                                                   data={**editado, "accion": "guardar"}).text


def test_pasado_el_cupo_pide_confirmar_la_imagen_extra(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 1)
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert len(proveedor.llamadas) == 1

    r = client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "historia", "tema": "Cómo empezamos"})
    assert "Generar por US$0.25" in r.text and "Cómo empezamos" in r.text
    assert len(proveedor.llamadas) == 1 and len(llamadas) == 1  # ni Claude ni Gemini antes de aceptar

    r = client.post("/panel/contenido/nuevo", headers=ORIGEN,
                    data={"objetivo": "historia", "tema": "Cómo empezamos", "extra": "1", "accion": "generar"})
    assert "post/2" in r.text and len(proveedor.llamadas) == 2
    assert client.portal.call(imagenes.usadas, c.store) == (1, 1)
    assert "1 de 1 imágenes de tu plan este mes · 1 extra" in client.get("/panel/contenido").text

    # Otra imagen para un post también pide confirmar.
    r = client.post("/panel/contenido/post/2", headers=ORIGEN, data={"titulo": "Cómo empezamos", "accion": "otra"})
    assert "Generar por US$0.25" in r.text and len(proveedor.llamadas) == 2


def test_si_falla_la_imagen_se_avisa_y_no_queda_post(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)
    monkeypatch.setattr(imagenes, "proveedor", Falso(imagenes.ErrorImagen("Esa idea no se pudo ilustrar.")))
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "explicar"})
    assert "Esa idea no se pudo ilustrar." in r.text and "post/" not in r.text
    assert client.portal.call(c.store.posts, ("borrador",), None, admin.PANEL) == []
    assert client.portal.call(imagenes.usadas, c.store) == (0, 0)
