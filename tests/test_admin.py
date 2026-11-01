"""Administradores: publicar por DM y reactivar conversaciones (sin red ni Claude)."""
import asyncio
import hashlib
import hmac
import io
import json
import time

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import admin, config  # noqa: E402
from igbot.store import Store  # noqa: E402


def jpg(w, h) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(out, "PNG")  # llega en otro formato: se convierte
    return out.getvalue()


class FakeIG:
    def __init__(self):
        self.images, self.texts, self.published = [], [], []

    async def download(self, url):
        return jpg(1080, 1920) if "vertical" in url else jpg(1080, 1080)

    async def send_image(self, user_id, url):
        self.images.append((user_id, url))

    async def send_text(self, user_id, text):
        self.texts.append((user_id, text))

    async def publicar(self, urls, caption):
        self.published.append((urls, caption))
        return "179000111", "https://www.instagram.com/p/NUEVA/"

    async def username(self, user_id):
        return {"111": "@ana"}.get(user_id, user_id)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(cuenta(), "media_dir", str(tmp_path / "media"))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")

    async def setup():
        store = Store(":memory:")
        await store.open()
        return store

    store = asyncio.run(setup())
    yield store, FakeIG()
    asyncio.run(store.close())


def run(coro):
    return asyncio.run(coro)


def tools_for(store, ig, state=None):
    return {t.name: t.handler for t in admin.admin_tools(store, ig, "admin1", state if state is not None else {})}


def text_of(result):
    return result["content"][0]["text"]


def test_fotos_del_admin_se_guardan_y_se_nombran(env):
    store, ig = env
    message = {"attachments": [{"type": "image", "payload": {"url": "https://cdn/vertical.jpg"}},
                               {"type": "video", "payload": {"url": "https://cdn/v.mp4"}}],
               "text": "publica esto"}
    text, photos = run(admin.incoming_text(store, "admin1", message))
    assert text.startswith(f"[foto {photos[0]}] [envió: video") and text.endswith("publica esto")
    run(admin.save_photo(store, ig, photos[0]))
    token, _, note = run(store.media(photos[0]))
    img = Image.open(f"{cuenta().media_dir}/{token}.jpg")
    assert img.format == "JPEG" and img.size == (1080, 1350)  # 9:16 recortada a 4:5
    assert "4:5" in note


def test_publica_solo_despues_de_confirmar_en_otro_mensaje(env):
    store, ig = env
    _, photos = run(admin.incoming_text(store, "admin1", {"attachments": [
        {"type": "image", "payload": {"url": "https://cdn/a.jpg"}},
        {"type": "image", "payload": {"url": "https://cdn/b.jpg"}},
    ]}))
    t = tools_for(store, ig)
    r = run(t["preparar_publicacion"]({"fotos": photos, "texto": "Nuevos polos 🔥", "cuando": "ahora"}))
    assert "Borrador #1" in text_of(r)
    assert [u for _, u in ig.images] and all(u.startswith("https://bot.ejemplo.com/media/") for _, u in ig.images)
    # En el mismo turno no se puede confirmar (el admin aún no vio la vista previa).
    assert run(t["confirmar_publicacion"]({"numero": 1})).get("is_error")
    assert ig.published == []

    time.sleep(0.01)
    later = tools_for(store, ig)  # siguiente mensaje del admin
    r = run(later["confirmar_publicacion"]({"numero": 1}))
    assert "instagram.com/p/NUEVA" in text_of(r)
    urls, caption = ig.published[0]
    assert len(urls) == 2 and caption == "Nuevos polos 🔥"
    assert run(store.post(1))["status"] == "publicada"
    assert run(later["confirmar_publicacion"]({"numero": 1})).get("is_error")  # no se publica dos veces


def test_programada_sale_a_su_hora(env, monkeypatch):
    store, ig = env
    _, photos = run(admin.incoming_text(store, "admin1", {"attachments": [
        {"type": "image", "payload": {"url": "https://cdn/a.jpg"}}]}))
    t = tools_for(store, ig)
    assert run(t["preparar_publicacion"]({"fotos": photos, "texto": "x", "cuando": "2020-01-01 10:00"})).get("is_error")
    assert run(t["preparar_publicacion"]({"fotos": photos, "texto": "x", "cuando": "2099-01-01 10:00"})).get("is_error")
    assert run(t["preparar_publicacion"]({"fotos": [99], "texto": "x", "cuando": "ahora"})).get("is_error")
    monkeypatch.setattr(admin, "parse_when", lambda text: time.time() + 3600)  # dentro del límite
    run(t["preparar_publicacion"]({"fotos": photos, "texto": "En una hora", "cuando": "luego"}))
    time.sleep(0.01)
    later = tools_for(store, ig)
    assert "Programada" in text_of(run(later["confirmar_publicacion"]({"numero": 1})))
    run(admin.publish_due(store, ig))
    assert ig.published == []  # aún no es hora

    run(store.db.execute("UPDATE posts SET publish_at = 1 WHERE id = 1"))
    run(admin.publish_due(store, ig))
    assert ig.published == [([ig.published[0][0][0]], "En una hora")]
    assert ig.texts == [("admin1", "Ya salió la publicación #1: https://www.instagram.com/p/NUEVA/")]
    assert "cancelada" not in text_of(run(later["ver_publicaciones"]({})))


def test_ver_pausas_y_reactivar(env):
    store, ig = env
    run(store.add_incoming("111", "m1", "quiero hablar con alguien"))
    run(store.pause("111", time.time() + 3600))
    t = tools_for(store, ig)
    listing = text_of(run(t["ver_pausas"]({})))
    assert "@ana" in listing and "quiero hablar con alguien" in listing
    assert run(t["reactivar_conversacion"]({"usuario": "@otra"})).get("is_error")
    assert "@ana" in text_of(run(t["reactivar_conversacion"]({"usuario": "@Ana"})))
    assert run(store.paused_until("111")) == 0
    assert text_of(run(t["ver_pausas"]({}))) == "No hay conversaciones en pausa."


def test_comandos_de_modo():
    assert admin.command("Modo cliente!") == "cliente"
    assert admin.command("modo  admin") == "admin"
    assert admin.command("modo cliente por favor") is None


def test_ruta_de_fotos_no_deja_salir_de_data():
    assert admin.media_path("../etc", "a" * 32) is None
    assert admin.media_path("nortex", "../../x") is None
    assert str(admin.media_path("nortex", "a" * 32)) == f"data/nortex/media/{'a' * 32}.jpg"
    assert str(admin.media_path("principal", "a" * 32)) == f"data/media/{'a' * 32}.jpg"


def test_webhook_manda_al_admin_con_su_asistente(monkeypatch):
    calls, sent = [], []

    async def fake_admin_reply(store, google, user_id, new_messages, instagram=None):
        calls.append(("admin", user_id, new_messages))
        return "ok admin", False

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        calls.append(("cliente", user_id, new_messages))
        return "ok cliente", False

    async def fake_send(user_id, text):
        sent.append((user_id, text))

    async def nothing(*a):
        pass

    monkeypatch.setattr(bot.admin, "reply", fake_admin_reply)
    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.admin, "save_photo_quietly", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    monkeypatch.setattr(bot.instagram, "close", nothing)  # otras pruebas siguen usando su cliente
    monkeypatch.setattr(cuenta(), "admin_ig_ids", {"jefe"})
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(bot, "MULTI_ACCOUNT", False)

    def post(client, mid, sender, **message):
        body = json.dumps({"entry": [{"id": "negocio", "messaging": [{
            "sender": {"id": sender}, "recipient": {"id": "negocio"}, "message": {"mid": mid, **message},
        }]}]}).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        client.post("/webhook", content=body,
                    headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"})

    def wait(n):
        deadline = time.time() + 3
        while len(sent) < n and time.time() < deadline:
            time.sleep(0.05)

    with TestClient(bot.app) as client:
        post(client, "a1", "jefe", attachments=[{"type": "image", "payload": {"url": "https://cdn/x.jpg"}}])
        wait(1)
        post(client, "a2", "jefe", text="Modo cliente")
        wait(2)
        post(client, "a3", "jefe", text="hola")
        wait(3)

    assert calls[0][0:2] == ("admin", "admin:jefe") and calls[0][2][0].startswith("[foto ")
    assert sent[0] == ("jefe", "ok admin")
    assert sent[1][0] == "jefe" and sent[1][1].startswith("Modo cliente")
    assert calls[1] == ("cliente", "jefe", ["hola"])


def test_publicar_en_la_api_de_instagram(monkeypatch):
    import httpx

    from igbot.instagram import Instagram

    calls = []

    def answer(request):
        body = dict(httpx.QueryParams(request.content.decode())) if request.content else {}
        calls.append((request.method, request.url.path.split("/v23.0/")[1], body))
        path = request.url.path
        if path.endswith("/me/media"):
            return httpx.Response(200, json={"id": f"c{len(calls)}"})
        if path.endswith("/me/media_publish"):
            return httpx.Response(200, json={"id": "media9"})
        if request.url.params.get("fields") == "status_code":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.url.params.get("fields") == "permalink":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/OK/"})
        return httpx.Response(400, json={"error": {"message": "no"}})

    async def main():
        store = Store(":memory:")
        await store.open()
        ig = Instagram(store)
        ig.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        link = await ig.publish(["https://x/1.jpg", "https://x/2.jpg"], "Hola #nortex")
        await ig.close()
        await store.close()
        return link

    assert run(main()) == "https://www.instagram.com/p/OK/"
    posts = [(m, p, b) for m, p, b in calls if m == "POST"]
    assert posts[0][2] == {"image_url": "https://x/1.jpg", "is_carousel_item": "true"}
    assert posts[2][2]["media_type"] == "CAROUSEL" and posts[2][2]["children"] == "c1,c2"
    assert posts[2][2]["caption"] == "Hola #nortex"
    assert posts[3][1] == "me/media_publish"


def test_el_asistente_del_admin_arma_sus_herramientas(monkeypatch):
    seen = {}

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        seen.update(prompt=prompt, system=system, allowed=allowed)
        return "listo"

    async def main():
        store = Store(":memory:")
        await store.open()
        try:
            return await admin.reply(store, None, "admin:jefe", ["[foto 1] publica"], FakeIG())
        finally:
            await store.close()

    monkeypatch.setattr("igbot.agent.run_claude", fake_claude)
    assert run(main()) == ("listo", False)
    assert "mcp__admin__preparar_publicacion" in seen["allowed"] and "[foto 1] publica" in seen["prompt"]
    assert "confirmar_publicacion" in seen["system"] and "enviar_reporte" in seen["system"]


def test_tocar_un_boton_de_inicio_se_contesta_como_mensaje(monkeypatch):
    calls = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        calls.append(new_messages)
        return "ok", False

    async def nothing(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", nothing)
    monkeypatch.setattr(bot.instagram, "close", nothing)
    monkeypatch.setattr(bot.instagram, "subscribe", nothing)
    monkeypatch.setattr(bot.botones, "sync", nothing)
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(bot, "MULTI_ACCOUNT", False)

    body = json.dumps({"entry": [{"id": "negocio", "messaging": [{
        "sender": {"id": "ana"}, "recipient": {"id": "negocio"}, "timestamp": 1,
        "postback": {"mid": "pb1", "title": "¿Qué productos manejan?", "payload": "boton_1"},
    }]}]}).encode()
    sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
    with TestClient(bot.app) as client:
        client.post("/webhook", content=body,
                    headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"})
        deadline = time.time() + 3
        while not calls and time.time() < deadline:
            time.sleep(0.05)
    assert calls == [["¿Qué productos manejan?"]]


def test_publicar_una_historia_en_la_api(monkeypatch):
    import httpx

    from igbot.instagram import Instagram

    calls = []

    def answer(request):
        body = dict(httpx.QueryParams(request.content.decode())) if request.content else {}
        calls.append((request.method, request.url.path.split("/v23.0/")[1], body))
        if request.url.path.endswith("/me/media"):
            return httpx.Response(200, json={"id": "c1"})
        if request.url.path.endswith("/me/media_publish"):
            return httpx.Response(200, json={"id": "media9"})
        if request.url.params.get("fields") == "status_code":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        return httpx.Response(200, json={"permalink": "https://www.instagram.com/stories/negocio/9/"})

    async def main():
        store = Store(":memory:")
        await store.open()
        ig = Instagram(store)
        ig.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        link = await ig.publish_story("https://x/1.jpg")
        await ig.close()
        await store.close()
        return link

    assert run(main()) == "https://www.instagram.com/stories/negocio/9/"
    posts = [b for m, p, b in calls if m == "POST"]
    assert posts[0] == {"image_url": "https://x/1.jpg", "media_type": "STORIES"}  # sin caption
    assert posts[1] == {"creation_id": "c1"}
