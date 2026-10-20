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
    assert 'href="/panel/contenido">Contenido</a>' in client.get("/panel").text
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
    assert "Ninguno: tu asistente está contestando" in client.get("/panel/asistente").text
    client.portal.call(c.store.pause, "ana", time.time() + 3600)
    r = client.get("/panel/asistente")
    assert "@ana" in r.text and "Vuelve a contestar solo en 1 h" in r.text and "Reactivar" in r.text
    assert client.post("/panel/pausas", data={"usuario": "ana", "accion": "reactivar"}).status_code == 403  # sin Origin
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "extender"})
    assert "sigue en pausa 12 h más" in r.text
    assert client.portal.call(c.store.paused_until, "ana") > time.time() + 11 * 3600
    r = client.post("/panel/pausas", headers=ORIGEN, data={"usuario": "ana", "accion": "reactivar"})
    assert "vuelve a contestarle a @ana" in r.text and client.portal.call(c.store.paused_until, "ana") == 0
    assert "ya no estaba en pausa" in client.post("/panel/pausas", headers=ORIGEN,
                                                  data={"usuario": "beto", "accion": "reactivar"}).text


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
             "estilo": "premium", "plantilla": "editorial", "modo": "libre", "fotos_estilo": "1", "propio": "1",
             "estilo_propio": "Fondos oscuros, minimalista, el celular como protagonista."}
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data={**marca, "accion": "subir"},
                    files={"fotos": ("post.png", png(), "image/png")})
    guardada = client.portal.call(contenido.marca_de, c)
    assert guardada["modo"] == "libre" and guardada["fotos_estilo"] is True and "celular" in guardada["estilo_propio"]
    assert 'name="modo" value="libre" checked' in r.text

    r = client.get("/panel/contenido/nuevo")
    assert 'name="modo" value="libre" checked' in r.text
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


def test_estilo_propio_reemplaza_a_los_de_la_lista(cliente, monkeypatch):
    client, c, llamadas, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    base = {"principal": "#111111", "acento": "#E8590C", "fondo": "#000000", "fuente": "Inter", "peso": "700",
            "estilo": "limpio", "estilo_propio": "Fondos oscuros y detalles en naranja.", "accion": "guardar"}
    # Sin la casilla, la descripción se guarda pero manda el estilo de la lista.
    r = client.post("/panel/contenido/marca", headers=ORIGEN, data=base)
    assert 'id="propio" name="propio" value="1">' in r.text
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert "Studio photography" in proveedor.llamadas[-1][0] and "naranja" not in proveedor.llamadas[-1][0]
    assert "<estilo_de_la_marca>" not in llamadas[-1][0]

    r = client.post("/panel/contenido/marca", headers=ORIGEN, data={**base, "propio": "1"})
    assert 'id="propio" name="propio" value="1" checked>' in r.text
    client.post("/panel/contenido/nuevo", headers=ORIGEN, data={"objetivo": "marca"})
    assert "naranja" in proveedor.llamadas[-1][0] and "Studio photography" not in proveedor.llamadas[-1][0]
    # La vista previa no usa la foto de ejemplo de un estilo de la lista.
    assert client.get("/panel/contenido/muestra.jpg?propio=1").status_code == 200


def test_marca_viejo_con_descripcion_usa_su_estilo():
    assert contenido.usa_propio({"estilo": "limpio", "estilo_propio": "Oscuro"})
    assert not contenido.usa_propio({"estilo": "limpio"})
    assert not contenido.usa_propio({"estilo_propio": "Oscuro", "propio": False})
    assert contenido.usa_propio({"propio": "1"}) and not contenido.usa_propio({"propio": "0", "estilo_propio": "x"})


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
