"""Facebook: la página del cliente (Messenger, comentarios, publicar) y su Instagram por el login de Facebook."""
import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402

import test_contenido  # noqa: E402
from igbot import config, facebook, pagina_fb  # noqa: E402
from igbot.cuenta import Cuenta, usar  # noqa: E402
from igbot.instagram import Instagram, api  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_contenido import ORIGEN, bot  # noqa: E402

cliente = test_contenido.cliente


def con_cuenta(**cambios) -> Cuenta:
    return Cuenta.desde_env("prueba", {"IG_ACCESS_TOKEN": "tok", **cambios})


def test_con_login_de_facebook_va_por_graph_facebook_con_el_id_del_instagram():
    with usar(con_cuenta(IG_ACCOUNT_ID="1784", IG_VIA="facebook")):
        assert api("me/media") == f"https://graph.facebook.com/{config.API_VERSION}/1784/media"
        assert api("me") == f"https://graph.facebook.com/{config.API_VERSION}/1784"
        # Mensajes, botones de inicio y avisos son de la página ("me" con el token de la página).
        assert api("me/messages") == f"https://graph.facebook.com/{config.API_VERSION}/me/messages"
        assert api("123_456/replies").startswith("https://graph.facebook.com/")
    with usar(con_cuenta(IG_ACCOUNT_ID="1784")):
        assert api("me/media") == f"https://graph.instagram.com/{config.API_VERSION}/me/media"


def test_a_quien_escribio_por_messenger_se_le_contesta_por_la_pagina():
    async def main():
        store = Store(":memory:")
        await store.open()
        await pagina_fb.guardar(store, "PAG", "Café Lunar", "token-pagina")
        enviados = []

        def manejar(request: httpx.Request) -> httpx.Response:
            enviados.append((request.url.host, request.url.path, request.url.params.get("access_token"),
                             json.loads(request.content)))
            return httpx.Response(200, json={"message_id": "m-fb-1"})

        ig = Instagram(store)
        ig.http = ig.pagina.http = httpx.AsyncClient(transport=httpx.MockTransport(manejar))
        with usar(con_cuenta(IG_ACCOUNT_ID="1784")):
            await ig.send_text("fb:555", "hola")
        assert enviados == [("graph.facebook.com", f"/{config.API_VERSION}/PAG/messages", "token-pagina",
                             {"recipient": {"id": "555"}, "message": {"text": "hola"}, "messaging_type": "RESPONSE"})]
        assert await store.was_sent("m-fb-1")  # su eco no se confunde con el dueño
        assert await store.get("fb_publicar") == "1"  # al conectar, las campañas salen también ahí
        await ig.close()
        await store.close()

    asyncio.run(main())


def test_aviso_de_la_pagina_queda_con_forma_de_instagram():
    entry = {"id": "PAG", "messaging": [{"sender": {"id": "555"}, "recipient": {"id": "PAG"},
                                         "message": {"mid": "m1", "text": "hola"}}],
             "changes": [{"field": "feed", "value": {"item": "comment", "verb": "add", "comment_id": "PAG_1_9",
                                                     "post_id": "PAG_1", "message": "precio?", "from": {"id": "777"}}},
                         {"field": "feed", "value": {"item": "reaction", "verb": "add"}}]}
    e = bot.de_messenger(entry)
    assert e["messaging"][0]["sender"] == {"id": "fb:555"} and e["messaging"][0]["recipient"] == {"id": "fb:PAG"}
    assert e["changes"] == [{"field": "comments", "value": {
        "id": "PAG_1_9", "from": {"id": "fb:777"}, "media": {"id": "PAG_1"}, "text": "precio?", "pagina": True}}]


def firmado(client, payload):
    body = json.dumps(payload).encode()
    sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body,
                       headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"})


def test_messenger_de_punta_a_punta(cliente, monkeypatch):
    client, c, *_ = cliente
    client.portal.call(pagina_fb.guardar, c.store, "PAG", "Café Lunar", "token-pagina")
    monkeypatch.setattr(c, "fb_page_id", "PAG")
    client.portal.call(c.store.set, "bot_activo", "1")
    enviados = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        return "respuesta a: " + " / ".join(new_messages), False

    async def fake_send(user_id, text):
        enviados.append((user_id, text))

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(c.instagram, "send_text", fake_send, raising=False)
    monkeypatch.setattr(bot.dispatchers[c.slug], "debounce", 0)
    assert firmado(client, {"object": "page", "entry": [{"id": "PAG", "messaging": [{
        "sender": {"id": "555"}, "recipient": {"id": "PAG"}, "message": {"mid": "m1", "text": "hola"}}]}]}).status_code == 200
    deadline = time.time() + 3
    while not enviados and time.time() < deadline:
        time.sleep(0.05)
    assert enviados == [("fb:555", "respuesta a: hola")]


def test_publicar_tambien_en_la_pagina(monkeypatch):
    async def main():
        store = Store(":memory:")
        await store.open()
        llamadas = []

        def manejar(request: httpx.Request) -> httpx.Response:
            datos = dict(request.url.params)
            if request.content:
                datos.update(json.loads(request.content))
            llamadas.append((request.url.path.rsplit("/", 1)[-1], datos))
            if request.url.path.endswith("/photos"):
                return httpx.Response(200, json={"id": f"foto{len(llamadas)}"})
            return httpx.Response(200, json={"id": "PAG_99"})

        real = httpx.AsyncClient
        monkeypatch.setattr(pagina_fb.httpx, "AsyncClient",
                            lambda **k: real(transport=httpx.MockTransport(manejar)))
        with usar(con_cuenta()):
            assert await pagina_fb.publicar_tambien(store, ["u1"], "hola", "https://instagram.com/p/X/") == ""
            await pagina_fb.guardar(store, "PAG", "Café Lunar", "token-pagina")
            await store.set("fb_publicar", "0")
            assert await pagina_fb.publicar_tambien(store, ["u1"], "hola", "https://instagram.com/p/X/") == ""
            await store.set("fb_publicar", "1")
            post = await pagina_fb.publicar_tambien(store, ["u1", "u2"], "Comenta PRECIO", "https://instagram.com/p/X/")
        assert post == "PAG_99"
        assert [n for n, _ in llamadas] == ["photos", "photos", "feed"]
        assert llamadas[0][1]["published"] == "false"
        assert llamadas[2][1]["attached_media"] == [{"media_fbid": "foto1"}, {"media_fbid": "foto2"}]
        # Un comentario en ese post de Facebook usa las palabras clave del de Instagram.
        assert await pagina_fb.Pagina(store).permalink("PAG_99") == "https://instagram.com/p/X/"
        await store.close()

    asyncio.run(main())


def test_en_facebook_el_sitio_web_va_al_final():
    assert pagina_fb.texto_para_facebook("Hola", "https://cafelunar.mx") == "Hola\n\nhttps://cafelunar.mx"
    assert pagina_fb.texto_para_facebook("Ve cafelunar.mx", "https://cafelunar.mx") == "Ve cafelunar.mx"
    assert pagina_fb.texto_para_facebook("Hola", "") == "Hola"


@pytest.fixture
def con_facebook(cliente, monkeypatch):
    monkeypatch.setattr(config, "FB_APP_ID", "app-fb")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    suscritas = []

    async def suscribir(c):
        suscritas.append(c.slug)
        c.fb_page_id = await c.store.get("fb_page_id") or ""

    monkeypatch.setattr(bot.panel, "conectar_pagina", suscribir)
    return (*cliente, suscritas)


def volver_de_facebook(client, monkeypatch, paginas):
    async def del_codigo(code):
        assert code == "cod1"
        return paginas

    monkeypatch.setattr(facebook, "paginas_del_codigo", del_codigo)
    r = client.post("/panel/facebook/conectar", headers=ORIGEN, follow_redirects=False)
    destino = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(destino.query).items()}
    assert destino.hostname == "www.facebook.com" and "pages_messaging" in q["scope"]
    assert q["redirect_uri"] == "https://console.skandar.online/panel/facebook/callback"
    return client.get(f"/panel/facebook/callback?code=cod1&state={q['state']}", follow_redirects=False)


def test_conectar_la_pagina_desde_el_panel(con_facebook, monkeypatch):
    client, c, _, _, suscritas = con_facebook
    assert "Página de Facebook</b><span>Sin conectar" in client.get("/panel/conexion").text
    assert "Conectar tu página" in client.get("/panel/facebook").text
    paginas = [{"id": "OTRA", "name": "Otra", "access_token": "t-otra"},
               {"id": "PAG", "name": "Café Lunar", "access_token": "t-pag",
                "instagram_business_account": {"id": "1784", "username": "cafe.lunar"}}]
    r = volver_de_facebook(client, monkeypatch, paginas)
    # Con varias páginas se elige sola la que tiene su mismo Instagram.
    assert r.headers["location"] == "/panel/facebook?aviso=conectada"
    assert client.portal.call(c.store.get, "fb_page_token") == "t-pag" and c.fb_page_id == "PAG"
    assert suscritas == [c.slug]
    assert "Café Lunar · Messenger, comentarios y publicar" in client.get("/panel/conexion").text
    client.post("/panel/facebook/publicar", headers=ORIGEN, data={"publicar": "0"})
    assert "no se publican" in client.get("/panel/facebook").text
    assert "Desconectamos tu página" in client.post("/panel/facebook/desconectar", headers=ORIGEN).text
    assert not client.portal.call(pagina_fb.conectada, c.store) and c.fb_page_id == ""


def test_varias_paginas_sin_su_instagram_se_elige(con_facebook, monkeypatch):
    client, c, *_ = con_facebook
    paginas = [{"id": "A", "name": "Página A", "access_token": "ta"}, {"id": "B", "name": "Página B", "access_token": "tb"}]
    r = volver_de_facebook(client, monkeypatch, paginas)
    assert "¿Qué página conectamos?" in r.text and "Página B" in r.text
    r = client.post("/panel/facebook/elegir", headers=ORIGEN, data={"pagina": "B"}, follow_redirects=False)
    assert r.headers["location"] == "/panel/facebook?aviso=conectada"
    assert client.portal.call(c.store.get, "fb_page_id") == "B"


def test_entrar_con_facebook_conecta_su_instagram_tambien(con_facebook, monkeypatch):
    client, c, *_ = con_facebook
    client.cookies.clear()  # alguien nuevo, sin sesión

    async def del_codigo(code):
        return [{"id": "PAG2", "name": "Panadería Sol", "access_token": "t-sol",
                 "instagram_business_account": {"id": "2001", "username": "pan.sol", "followers_count": 50}}]

    monkeypatch.setattr(facebook, "paginas_del_codigo", del_codigo)
    assert "Entrar con Facebook" in client.get("/panel").text
    q = parse_qs(urlsplit(client.get("/panel/facebook/entrar", follow_redirects=False).headers["location"]).query)
    r = client.get(f"/panel/facebook/callback?code=x&state={q['state'][0]}", follow_redirects=False)
    assert r.headers["location"] == "https://console.skandar.online/panel" and "panel=" in r.headers["set-cookie"]
    nueva = bot.buscar("2001")
    assert nueva and nueva.ig_via == "facebook"
    env = open(f"clientes/{nueva.slug}/.env").read()
    assert "IG_ACCESS_TOKEN=t-sol" in env and "IG_VIA=facebook" in env
    assert client.portal.call(nueva.store.get, "fb_page_id") == "PAG2"
    assert "a través de tu página de Facebook" in client.get("/panel/conexion").text
    # Su Instagram depende de esa página: no se puede desconectar desde el panel.
    client.post("/panel/facebook/desconectar", headers=ORIGEN)
    assert client.portal.call(pagina_fb.conectada, nueva.store)


def test_pagina_sin_instagram_no_crea_cuenta(con_facebook, monkeypatch):
    client, *_ = con_facebook
    client.cookies.clear()

    async def del_codigo(code):
        return [{"id": "PAG3", "name": "Sin IG", "access_token": "t3"}]

    monkeypatch.setattr(facebook, "paginas_del_codigo", del_codigo)
    q = parse_qs(urlsplit(client.get("/panel/facebook/entrar", follow_redirects=False).headers["location"]).query)
    r = client.get(f"/panel/facebook/callback?code=x&state={q['state'][0]}", follow_redirects=False)
    assert "Tu página no tiene Instagram" in r.text and "Entrar con Instagram" in r.text


def test_con_configuracion_de_meta_se_manda_config_id(con_facebook, monkeypatch):
    client, *_ = con_facebook
    monkeypatch.setattr(config, "FB_CONFIG_ID", "cfg123")
    q = parse_qs(urlsplit(client.get("/panel/facebook/entrar", follow_redirects=False).headers["location"]).query)
    assert q["config_id"] == ["cfg123"] and "scope" not in q


def test_el_codigo_se_cambia_con_el_secreto_de_la_app_de_meta(monkeypatch):
    import asyncio
    vistos = []

    class Resp:
        status_code = 200
        def json(self):
            return {"access_token": "t"}
        def raise_for_status(self):
            pass

    class Cliente:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None, **k):
            vistos.append(params or {})
            return Resp()

    monkeypatch.setattr(facebook.config, "FB_APP_SECRET", "secreto-meta")
    monkeypatch.setattr(facebook.httpx, "AsyncClient", Cliente)
    try:
        asyncio.run(facebook.paginas_del_codigo("cod"))
    except Exception:
        pass
    assert vistos and vistos[0].get("client_secret") == "secreto-meta"


def test_sin_pagina_va_por_graph_facebook_con_el_id_del_instagram_tambien_los_mensajes():
    with usar(con_cuenta(IG_ACCOUNT_ID="1784", IG_VIA="sistema")):
        assert api("me/messages") == f"https://graph.facebook.com/{config.API_VERSION}/1784/messages"
        assert api("me/media") == f"https://graph.facebook.com/{config.API_VERSION}/1784/media"
        assert api("me/subscribed_apps") == f"https://graph.facebook.com/{config.API_VERSION}/1784/subscribed_apps"


def test_conectar_con_meta_sin_pagina_crea_la_cuenta(con_facebook, monkeypatch):
    client, *_ = con_facebook
    client.cookies.clear()  # alguien nuevo desde el celular

    async def del_codigo(code):
        return []  # "Solo Instagram": sin páginas

    async def sin_pagina():
        return {"id": "3003", "username": "skandi.prueba", "followers_count": 10, "token": "t-sistema"}

    monkeypatch.setattr(facebook, "paginas_del_codigo", del_codigo)
    monkeypatch.setattr(facebook, "instagram_sin_pagina", sin_pagina)
    q = parse_qs(urlsplit(client.get("/panel/facebook/entrar?solo=ig", follow_redirects=False).headers["location"]).query)
    r = client.get(f"/panel/facebook/callback?code=x&state={q['state'][0]}", follow_redirects=False)
    assert r.headers["location"] == "https://console.skandar.online/panel" and "panel=" in r.headers["set-cookie"]
    nueva = bot.buscar("3003")
    assert nueva and nueva.ig_via == "sistema"
    env = open(f"clientes/{nueva.slug}/.env").read()
    assert "IG_ACCESS_TOKEN=t-sistema" in env and "IG_VIA=sistema" in env
    assert "a través de Meta" in client.get("/panel/conexion").text


def test_instagram_sin_pagina_lee_la_cuenta_elegida(monkeypatch):
    vistos = []

    class Resp:
        def __init__(self, datos):
            self.datos, self.status_code, self.is_error = datos, 200, False
        def json(self):
            return self.datos
        def raise_for_status(self):
            pass

    class Cliente:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None, **k):
            vistos.append(url)
            if url.endswith("/debug_token"):
                return Resp({"data": {"type": "SYSTEM_USER", "user_id": "u9"}})
            if url.endswith("/u9/assigned_instagram_accounts"):
                return Resp({"data": [{"id": "1784"}]})
            return Resp({"id": "1784", "username": "skandiconsulting"})

    monkeypatch.setattr(facebook.httpx, "AsyncClient", Cliente)

    async def correr():
        facebook._token_actual.set("t-sis")
        return await facebook.instagram_sin_pagina()

    ig = asyncio.run(correr())
    assert ig == {"id": "1784", "username": "skandiconsulting", "token": "t-sis"}
