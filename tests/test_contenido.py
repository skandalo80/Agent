"""Contenido en el panel: marca, crear post, editarlo, aprobarlo y publicarlo."""
import io
import json
import re
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


class ConPosts(FakeInstagram):
    """Lo que ya hay en el Instagram del cliente: uno subido desde la app y el que publicó el panel."""

    async def username(self, user_id):
        return f"@{user_id}"

    async def recent_media(self, limit=12):
        return [{"id": "1", "caption": "Desde la app\nSegunda línea", "media_url": "https://scontent.cdninstagram.com/a.jpg",
                 "permalink": "https://www.instagram.com/p/DESDEAPP/", "timestamp": "2099-01-01T10:00:00+0000"},
                {"id": "2", "caption": "Del panel", "permalink": "https://www.instagram.com/p/ABC123/",
                 "timestamp": "2020-01-01T10:00:00+0000"}]


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
    monkeypatch.setattr(bot, "Instagram", lambda store: ConPosts())
    monkeypatch.setattr(contenido, "_de_instagram", {})
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
    assert 'href="/panel/contenido">Contenido</a>' in client.get("/panel/asistente").text
    r = client.get("/panel/contenido")
    assert "Aún no tienes posts" in r.text and "DESDEAPP" in r.text  # lo de la app sale aunque no haya del panel
    assert "Este post lleva precio" in client.get("/panel/contenido/nuevo").text
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
                    data={"objetivo": "estrella", "tema": "La cartera Atelier", "foto_0": token_foto, "con_precio": "1"})
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
    assert 'id="con_precio"' in r.text and "Cancelar cambios" in r.text
    assert f'muestra.jpg?m={post["media_ids"][0]}"' in r.text  # otra imagen = otra URL: no sale la vieja del caché
    r = client.get("/panel/contenido/post/1/muestra.jpg?titulo=Otro&plantilla=minimal&precio=$9&con_precio=1")
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (1080, 1350)
    assert client.get("/panel/contenido/post/99/muestra.jpg").status_code == 404
    editado = {"plantilla": "marco", "titulo": "Cartera Atelier", "subtitulo": "", "precio": "$1,190 MXN",
               "con_precio": "1",
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
    assert "Quitar de programados" in client.get("/panel/contenido/post/1").text
    post = client.portal.call(c.store.post, 1)
    assert post["status"] == "programada" and post["publish_at"] == hora.timestamp()
    assert post["caption"] == "Nuevo texto #cartera"
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == "$1,190 MXN"

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
    assert "Ya publicados" in r.text and r.text.count("instagram.com/p/ABC123") == 1
    assert "instagram.com/p/DESDEAPP" in r.text and "Desde Instagram" in r.text
    assert r.text.index("DESDEAPP") < r.text.index("ABC123")  # el más nuevo primero
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
    assert "1 imágenes extra este mes" in client.get("/panel/contenido").text

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


def test_sin_precio_no_lleva_precio(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert "<con_precio>no</con_precio>" in llamadas[0][0]
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == ""
    datos = {"plantilla": "editorial", "titulo": "Hola", "precio": "$5", "caption": "x", "accion": "guardar"}
    client.post("/panel/contenido/post/1", headers=ORIGEN, data=datos)
    assert client.portal.call(contenido.pieza, c.store, 1)["precio"] == ""  # sin la casilla no se guarda


def test_si_instagram_falla_la_pagina_sale(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 3)

    async def falla(limit=12):
        raise RuntimeError("caído")

    monkeypatch.setattr(c.instagram, "recent_media", falla)
    r = client.get("/panel/contenido")
    assert r.status_code == 200 and "Aún no tienes posts" in r.text


def test_script_y_fuentes(cliente):
    client, *_ = cliente
    assert '<script src="/panel/static/panel.js" defer></script>' in client.get("/panel").text
    r = client.get("/panel/static/panel.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert client.get("/panel/static/fuentes/Lora-Bold.ttf").headers["content-type"] == "font/ttf"
    assert client.get("/panel/static/fuentes/..%2F.env").status_code == 404


def test_chats_en_pausa(cliente):
    client, c, *_ = cliente
    import time
    r = client.get("/panel/asistente")
    assert "Conversaciones recientes" in r.text and "Aún no te escribe nadie" in r.text
    assert "Contestados hoy" in r.text and "Mensajes restantes" in r.text and "Gasto de hoy" not in r.text
    client.portal.call(c.store.pause, "ana", time.time() + 3600)
    r = client.get("/panel/asistente?ver=pausa")
    assert "@ana" in r.text and "vuelve a contestar solo en 1 h" in r.text and "Reactivar" in r.text
    assert 'href="https://ig.me/m/ana"' in r.text and "En pausa (1)" in r.text
    assert client.post("/panel/pausas", data={"usuario": "ana", "accion": "reactivar"}).status_code == 403  # sin Origin
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "extender"})
    assert "sigue en pausa 12 h más" in r.text
    assert client.portal.call(c.store.paused_until, "ana") > time.time() + 11 * 3600
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "reactivar"})
    assert "vuelve a contestarle a @ana" in r.text and client.portal.call(c.store.paused_until, "ana") == 0
    assert "ya no estaba en pausa" in client.post("/panel/pausas", headers=ORIGEN,
                                                  data={"usuario": "beto", "accion": "reactivar"}).text


def test_conversaciones_recientes_y_tomar_un_chat(cliente):
    client, c, *_ = cliente
    store = c.store
    client.portal.call(store.add_incoming, "beto", "m1", "¿Tienen lugar mañana?")
    ids = [i for i, _ in client.portal.call(store.pending, "beto")]
    client.portal.call(store.mark_answered, "beto", ids, "Sí, a las 5.")
    client.portal.call(store.add_incoming, "caro", "m2", "Hola, ¿precios?")
    r = client.get("/panel/asistente")
    assert "@beto" in r.text and "«¿Tienen lugar mañana?»" in r.text and "Asistente" in r.text
    assert "@caro" in r.text and "Sin contestar" in r.text and r.text.index("@caro") < r.text.index("@beto")
    assert "Tomar este chat" in r.text and "Sin contestar (1)" in r.text
    assert '<b>1</b><small>en 1 chat</small>' in r.text and "Mensajes restantes</span><b>999</b>" in r.text  # PLAN=basico: 1,000
    r = client.get("/panel/asistente?ver=sin")
    assert "@caro" in r.text and "@beto" not in r.text

    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "beto", "accion": "tomar"})
    assert "el asistente no le contesta a @beto en 12 h" in r.text and "En pausa · tú" in r.text
    assert client.portal.call(store.paused_until, "beto") > 0
    assert "No encontramos ese chat" in client.post("/panel/pausas", headers=ORIGEN,
                                                    data={"usuario": "nadie", "accion": "tomar"}).text


TEMAS = {"temas": [{"tema": "Horario de fin de semana", "personas": 4, "ejemplo": "¿Abren el sábado?",
                    "objetivo": "explicar", "idea": "Post con el horario del sábado"},
                   {"tema": "x", "personas": "muchas", "objetivo": "otro"}]}
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


def test_comentarios_y_dms(con_mensajes):
    client, c, llamadas, _ = con_mensajes
    r = client.get("/panel/contenido/ideas")
    assert "Horario de fin de semana" in r.text and "4 personas" in r.text and "De 2 mensajes" in r.text
    assert 'href="/panel/contenido/ideas" class="on"' in r.text
    prompt, system, model = llamadas[0]
    assert "(DM, persona 1) Hola, ¿abren el sábado?" in prompt and "(comentario, persona 2)" in prompt
    assert model == "claude-haiku-5-5"
    client.get("/panel/contenido/ideas")
    assert len(llamadas) == 1  # una vez al día
    r = client.get("/panel/contenido/nuevo?objetivo=explicar&tema=Post+con+el+horario+del+s%C3%A1bado")
    assert "Post con el horario del sábado</textarea>" in r.text and 'value="explicar" checked' in r.text


def test_esta_semana(con_mensajes, monkeypatch):
    client, c, llamadas, proveedor = con_mensajes
    r = client.get("/panel/contenido/semana")
    assert "Tu semana en un clic" in r.text and "Armar mi semana · usa 3 imágenes" in r.text
    r = client.post("/panel/contenido/semana", headers=ORIGEN, data={"accion": "armar"})
    assert "Post 3 de 3" in r.text and 'url=/panel/contenido/semana"' in r.text
    assert len(proveedor.llamadas) == 3
    r = client.get("/panel/contenido/semana")
    assert r.text.count('class="propuesta"') == 3 and "Te preguntaron 4 veces." in r.text
    assert "0 de 3 aprobadas" in r.text and "Proponer 3 más" in r.text
    posts = client.portal.call(c.store.posts, ("borrador",), None, admin.PANEL)
    fechas = sorted(p["publish_at"] for p in posts)
    assert fechas[1] - fechas[0] == 2 * 86400
    r = client.post("/panel/contenido/semana", headers=ORIGEN, data={"aprobar": str(posts[0]["id"])})
    assert "Listo: sale el" in r.text and "1 de 3 aprobadas" in r.text
    assert client.portal.call(c.store.post, posts[0]["id"])["status"] == "programada"
    assert "ya no está en borrador" in client.post("/panel/contenido/semana", headers=ORIGEN,
                                                   data={"aprobar": str(posts[0]["id"])}).text

    # Sin cupo para 3 más, pide confirmar las extra antes de gastar.
    monkeypatch.setattr(c, "imagenes_mes", 4)
    r = client.post("/panel/contenido/semana", headers=ORIGEN, data={"accion": "armar"})
    assert "Te queda 1 imagen de tu plan" in r.text and len(proveedor.llamadas) == 3


def test_diseno_libre_con_el_estilo_de_sus_fotos(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    marca = {"principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": "700",
             "estilo": "premium", "plantilla": "editorial", "modo": "libre", "fotos_estilo": "1",
             "estilo_propio": "Fondos oscuros, minimalista, el celular como protagonista."}
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data={**marca, "accion": "subir"},
                    files={"fotos": ("post.png", png(), "image/png")})
    guardada = client.portal.call(contenido.marca_de, c)
    assert guardada["modo"] == "libre" and guardada["fotos_estilo"] is True and "celular" in guardada["estilo_propio"]
    assert 'name="modo" value="libre" checked' in r.text

    r = client.get("/panel/contenido/nuevo")
    assert 'name="como" value="marca" checked><b>Con mi diseño libre' in r.text and "celular como protagonista" in r.text
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert "<estilo_de_la_marca>Fondos oscuros" in llamadas[-1][0]
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
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca", "modo": "plantilla"})
    assert client.portal.call(contenido.pieza, c.store, 2)["plantilla"] == "editorial"
    assert "Do not include any text" in proveedor.llamadas[-1][0]


def test_el_estilo_depende_del_modo_del_post(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    base = {"principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": "700",
            "estilo": "limpio", "estilo_propio": "Fondos oscuros y detalles en naranja.", "accion": "guardar"}
    # Ya no hay casilla: con plantilla mandan los estilos de la lista; en libre, su descripción.
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data=base)
    assert 'name="propio"' not in r.text and 'class="si-libre"' in r.text
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca", "modo": "plantilla"})
    assert "Studio photography" in proveedor.llamadas[-1][0] and "naranja" not in proveedor.llamadas[-1][0]
    assert "<estilo_de_la_marca>" not in llamadas[-1][0]

    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca", "modo": "libre"})
    assert "naranja" in proveedor.llamadas[-1][0] and "Studio photography" not in proveedor.llamadas[-1][0]
    assert "<estilo_de_la_marca>" in llamadas[-1][0]
    # La vista previa en libre no usa la foto de ejemplo de un estilo de la lista.
    assert client.get("/panel/contenido/muestra.jpg?modo=libre").status_code == 200


def test_orden_de_tu_marca_y_miniaturas(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get("/panel/contenido/marca")
    orden = [r.text.index(t) for t in ("Cómo se arman tus posts", "Paleta de colores", "Tipografía de títulos",
                                        "Estilo de tus fotos", "Fotos de tu negocio")]
    assert orden == sorted(orden)
    assert 'data-plantilla="marco"' in r.text and 'data-plantilla="banda"' in r.text
    assert "plantilla=marco" in r.text and client.get("/panel/contenido/muestra.jpg?plantilla=banda").status_code == 200


def test_insights_de_un_post(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    pedidos = []

    async def insights(media_id):
        pedidos.append(media_id)
        return {"reach": 1520, "views": 2100, "likes": 87, "comments": 9, "saved": 14, "shares": 6}
    monkeypatch.setattr(c.instagram, "media_insights", insights, raising=False)
    r = client.get("/panel/contenido")
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
    r = client.get("/panel/asistente")
    assert 'id="ajustes"' in r.text and 'name="pausa" value="12" checked' in r.text
    # Conexión es un botón de la barra (con su semáforo) que abre su propia vista.
    assert re.search(r'href="/panel/asistente\?vista=conexion"><span class="semaforo sem-\w+"></span>Conexión', r.text)
    assert "Recibiendo mensajes" not in r.text and 'id="conexion"' not in r.text
    r = client.get("/panel/asistente?vista=conexion")
    assert "Recibiendo mensajes" in r.text and 'id="ajustes"' not in r.text and "Gasto de hoy" not in r.text
    assert 'secundario on" href="/panel/asistente">' in r.text  # pulsarlo otra vez regresa
    assert '<a class="enlace" href="/panel/asistente">← Volver a tu asistente</a>' in r.text
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


def test_conexion_avisa_si_falta_el_permiso_de_estadisticas(cliente, monkeypatch):
    from igbot import ajustes
    client, c, *_ = cliente
    ajustes._revision.clear()

    async def sin_permiso(media_id):
        raise RuntimeError("Instagram respondió 400")
    monkeypatch.setattr(c.instagram, "media_insights", sin_permiso, raising=False)
    r = client.get("/panel/asistente")
    assert "Hay algo que revisar en tu conexión" in r.text and "sem-aviso" in r.text
    r = client.get("/panel/asistente?vista=conexion")
    assert "Estadísticas: falta" in r.text and 'href="/conectar/instagram"' in r.text and "Hay algo que revisar" in r.text


def test_bienvenida_con_contenido_y_lo_que_falta(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get("/panel")
    assert "Configurar mi asistente" in r.text and "Configurar mi marca" in r.text and "Tu plan Básico" not in r.text
    assert client.get("/panel", follow_redirects=False).headers["location"] != "/panel/bienvenida"  # solo una vez
    client.portal.call(c.store.set, "perfil", "")
    client.portal.call(c.store.set, "asistente_listo", "0")
    assert "Te falta configurar <b>Tu asistente</b>" in client.get("/panel/contenido/nuevo").text
    client.post("/panel/guardar", headers=ORIGEN, data={"negocio": "Café", "accion": "guardar", "prompt": "Hola"})
    assert "Te falta configurar <b>Tu marca</b>" in client.get("/panel/asistente").text
    assert "Te falta configurar <b>Tu marca</b>" in client.get("/panel/contenido").text
    assert "Te falta configurar" not in client.get("/panel/contenido/marca").text  # ya está ahí
    client.post("/panel/contenido/marca", headers=ORIGEN, data={"principal": "#111111", "accion": "guardar"})
    assert "Te falta configurar" not in client.get("/panel/asistente").text
    assert "Te falta configurar" not in client.get("/panel/contenido").text


def test_resumen_del_plan_arriba(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 40)
    monkeypatch.setattr(c, "posts_mes", 12)
    monkeypatch.setattr(c, "mensajes_mes", 1000)
    r = client.get("/panel/contenido")
    assert "Posts restantes" in r.text and "de 12 este mes" in r.text and "de 40 este mes" in r.text
    # Tu marca es un ajuste: va junto al nombre, no entre las pestañas.
    nav = r.text.split('<nav class="pestanas">')[1].split("</nav>")[0]
    assert "/panel/contenido/marca" not in nav and 'class="boton secundario ajuste"' in r.text
    assert 'ajuste on"' in client.get("/panel/contenido/marca").text
    r = client.get("/panel/asistente")
    assert "Mensajes restantes" in r.text and "de 1,000 este mes" in r.text and "Gasto de hoy" not in r.text
    # Sin tope en su plan, sale solo el conteo del mes.
    monkeypatch.setattr(c, "mensajes_mes", 0)
    assert "Mensajes este mes" in client.get("/panel/asistente").text


def test_crear_post_con_ajustes_solo_para_ese_post(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    client.post("/panel/contenido/marca", headers=ORIGEN,
                data={"principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": "700",
                      "estilo": "limpio", "plantilla": "editorial", "modo": "plantilla", "accion": "guardar"})
    r = client.get("/panel/contenido/nuevo")
    assert "Con mi plantilla" in r.text and "Con otra plantilla" in r.text and "Diseño libre solo para este post" in r.text

    # Otra plantilla: banda, otro acento y estilo premium; la marca no cambia.
    client.post("/panel/contenido/nuevo", headers=ORIGEN,
                data={"objetivo": "marca", "como": "plantilla", "plantilla": "banda", "acento": "#2255AA",
                      "estilo": "premium", "fuente": "Lora", "principal": "#111111", "fondo": "#000000"})
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert p["plantilla"] == "banda" and p["marca"]["acento"] == "#2255AA" and p["marca"]["fuente"] == "Lora"
    assert "Dark background" in proveedor.llamadas[-1][0]  # el estilo premium
    marca = client.portal.call(contenido.marca_de, c)
    assert marca["acento"] == "#E8590C" and marca["plantilla"] == "editorial"
    assert client.portal.call(contenido.marca_post, c, p)["acento"] == "#2255AA"

    # Otro diseño libre, con su propia descripción.
    client.post("/panel/contenido/nuevo", headers=ORIGEN,
                data={"objetivo": "marca", "como": "libre", "estilo_propio": "Estilo revista, fondo blanco."})
    p = client.portal.call(contenido.pieza, c.store, 2)
    assert p["plantilla"] == "libre" and "Estilo revista" in proveedor.llamadas[-1][0]

    # Con mi plantilla: lo de la marca, sin ajustes guardados.
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca", "como": "marca"})
    p = client.portal.call(contenido.pieza, c.store, 3)
    assert p["plantilla"] == "editorial" and "marca" not in p


def test_configura_tu_asistente_va_plegado(cliente):
    client, c, *_ = cliente
    # Ya guardó sus instrucciones (el fixture): el formulario sale cerrado.
    assert '<details class="grupo plegable" id="configura">' in client.get("/panel/asistente").text
    client.portal.call(c.store.set, "asistente_listo", "0")
    client.portal.call(c.store.set, "perfil", "")
    assert '<details class="grupo plegable" id="configura" open>' in client.get("/panel/asistente").text


def test_subir_fotos_solo_para_un_post(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get("/panel/contenido/nuevo")
    assert 'name="fotos_post"' in r.text and 'enctype="multipart/form-data"' in r.text
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "producto"},
                files={"fotos_post": ("bolsa.png", png(), "image/png")})
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert len(p["fotos"]) == 1 and len(proveedor.llamadas[-1][1]) == 1
    assert client.portal.call(contenido.marca_de, c).get("fotos", []) == []  # Tu marca no cambia
    # Un token inventado no sirve para leer otro archivo.
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "producto", "foto_0": "../../.env"})
    assert client.portal.call(contenido.pieza, c.store, 2)["fotos"] == []


def test_ideas_para_el_tema_con_tope_diario(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system, model))
        return "Carrusel con los tres paquetes, su precio y lo que incluye cada uno."

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    assert "Te quedan 3 de 3 hoy" in client.get("/panel/contenido/nuevo").text
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN,
                    data={"objetivo": "estrella", "tema": "comparar <paquetes>", "accion": "ampliar"})
    assert "Carrusel con los tres paquetes" in r.text and "Te quedan 2 de 3 hoy" in r.text
    assert "<idea>comparar &lt;paquetes&gt;</idea>" in llamadas[-1][0]  # lo del cliente va escapado
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "estrella", "accion": "sugerir"})
    assert "<idea>" not in llamadas[-1][0]
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "estrella", "accion": "sugerir"})
    n = len(llamadas)
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "estrella", "accion": "sugerir"})
    assert "Ya usaste las ideas de hoy" in r.text and len(llamadas) == n  # ya no se llama a Claude
    assert client.portal.call(c.store.posts, ("borrador",)) == []  # las ideas no crean posts ni usan imagen


def test_precio_escrito_y_fotos_para_ver_antes_de_generar(cliente, monkeypatch):
    client, c, llamadas, _ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    r = client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca", "accion": "subir_fotos"},
                    files={"fotos_post": ("bolsa.png", png(), "image/png")})
    assert "tus fotos ya están marcadas" in r.text and "Fotos solo para este post" in r.text
    assert r.text.count('checked><img') == 1 and not llamadas[1:]  # se ve y queda marcada; no se generó nada
    token = re.search(r'name="foto_0" value="([^"]+)"', r.text).group(1)
    assert token not in client.portal.call(contenido.marca_de, c).get("fotos", [])  # no va a su marca

    client.post("/panel/contenido/nuevo", headers=ORIGEN,
                data={"objetivo": "marca", "con_precio": "1", "precio": "Desde $990 MXN", "foto_0": token})
    assert "<precio_del_dueño>Desde $990 MXN</precio_del_dueño>" in llamadas[-1][0]
    p = client.portal.call(contenido.pieza, c.store, 1)
    assert p["precio"] == "Desde $990 MXN" and p["fotos"] == [token]


def test_filtros_abiertas_y_pidieron_una_persona(cliente):
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
    assert "Abiertas (1)" in r.text and "Pidieron una persona (1)" in r.text and "En pausa (2)" in r.text
    assert "1 piden una persona" in r.text
    r = client.get("/panel/asistente?ver=persona")
    assert "@caro" in r.text and "Pidió una persona" in r.text and "Motivo: Quiere negociar el precio" in r.text
    assert "@beto" not in r.text and "@dani" not in r.text and "Reactivar" in r.text
    r = client.get("/panel/asistente?ver=abiertas")
    assert "@beto" in r.text and "@caro" not in r.text and "@dani" not in r.text
