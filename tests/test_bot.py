"""Pruebas sin red: Claude e Instagram se reemplazan por funciones falsas.

Correr con:  python -m pytest -q
"""
import asyncio
import hashlib
import hmac
import json
import os
import time

os.environ.update(
    IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok",
    DB_PATH=":memory:", DEBOUNCE_SECONDS="0.2",
)

from fastapi.testclient import TestClient  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot.dispatcher import Dispatcher  # noqa: E402
from igbot.instagram import split_text  # noqa: E402
from igbot.store import Store  # noqa: E402


def test_mensajes_seguidos_se_contestan_juntos_y_en_orden():
    async def main():
        store = Store(":memory:")
        await store.open()
        calls = []

        async def handle(user_id, rows):
            calls.append((user_id, [t for _, t in rows]))
            await asyncio.sleep(0.3)  # simula a Claude pensando
            await store.mark_answered(user_id, [i for i, _ in rows], "ok")

        d = Dispatcher(store, handle, debounce=0.1, max_concurrent=2)
        for i, text in enumerate(["hola", "una pregunta", "cuánto cuesta?"]):
            await store.add_incoming("ana", f"a{i}", text)
            d.notify("ana")
        await store.add_incoming("beto", "b0", "buenas")
        d.notify("beto")
        await asyncio.sleep(0.2)  # llega otro mientras se contesta lo de Ana
        await store.add_incoming("ana", "a3", "?")
        d.notify("ana")
        await d.drain()

        ana = [texts for u, texts in calls if u == "ana"]
        assert ana == [["hola", "una pregunta", "cuánto cuesta?"], ["?"]]
        assert ("beto", ["buenas"]) in calls
        assert await store.pending_users() == []
        await store.close()

    asyncio.run(main())


def test_duplicados_de_meta_se_ignoran():
    async def main():
        store = Store(":memory:")
        await store.open()
        assert await store.add_incoming("ana", "m1", "hola")
        assert not await store.add_incoming("ana", "m1", "hola")
        await store.close()

    asyncio.run(main())


def test_si_falla_varias_veces_sale_de_la_cola():
    async def main():
        store = Store(":memory:")
        await store.open()

        async def handle(user_id, rows):
            raise RuntimeError("Claude caído")

        d = Dispatcher(store, handle, debounce=0, max_concurrent=1, retries=2, retry_delay=0)
        await store.add_incoming("ana", "m1", "hola")
        d.notify("ana")
        await d.drain()
        assert await store.pending("ana") == []
        await store.close()

    asyncio.run(main())


def test_ventana_y_resumen():
    async def main():
        store = Store(":memory:")
        await store.open()
        for i in range(30):
            await store.add_incoming("ana", f"m{i}", f"msg {i}")
            (row,) = await store.pending("ana")
            await store.mark_answered("ana", [row[0]], f"resp {i}")
        old = await store.to_summarize("ana", keep=20)
        assert len(old) == 40 and old[0][2] == "msg 0"
        await store.save_summary("ana", "resumen", upto_id=old[-1][0])
        recent = await store.recent("ana", 20)
        assert recent[0] == ("user", "msg 20") and recent[-1] == ("assistant", "resp 29")
        await store.close()

    asyncio.run(main())


def test_split_text():
    parts = split_text("palabra " * 400)
    assert all(len(p) <= 1000 for p in parts)
    assert " ".join(parts).split() == ["palabra"] * 400


def test_webhook_de_punta_a_punta(monkeypatch):
    sent = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        if new_messages == ["hola"]:  # mientras "piensa" llega otro mensaje
            await store.add_incoming("123", "m2", "info?")
        return "respuesta a: " + " / ".join(new_messages), False

    async def fake_summary(store, user_id):
        pass

    async def fake_send(user_id, text):
        sent.append((user_id, text))

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", fake_summary)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    monkeypatch.setattr(bot, "ECHO_WAIT", 0)

    async def my_account():
        return "negocio"

    monkeypatch.setattr(bot.instagram, "account_id", my_account)
    monkeypatch.setattr(bot.principal, "ig_account_id", "")  # se vuelve a vaciar al terminar la prueba

    async def ours(mid):  # el eco de esta prueba es de un DM que mandó el bot
        return True

    monkeypatch.setattr(bot.store, "was_sent", ours)

    def post(payload):
        body = json.dumps(payload).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        return client.post(
            "/webhook", content=body,
            headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
        )

    def dm(mid, text, echo=False, account="negocio"):
        # En un eco el remitente es la cuenta del negocio y el destinatario, el cliente.
        ids = (account, "123") if echo else ("123", account)
        return {"entry": [{"id": account, "messaging": [{
            "sender": {"id": ids[0]}, "recipient": {"id": ids[1]},
            "message": {"mid": mid, "text": text, **({"is_echo": True} if echo else {})},
        }]}]}

    with TestClient(bot.app) as client:
        assert client.post("/webhook", content=b"{}").status_code == 403
        for hidden in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(hidden).status_code == 404
        assert post(dm("m1", "hola")).status_code == 200
        post(dm("m1", "hola"))  # reintento de Meta
        post(dm("m3", "eco", echo=True))
        post(dm("x1", "hola nortex", account="otra"))  # de otra cuenta: no se contesta
        deadline = time.time() + 3
        while not sent and time.time() < deadline:
            time.sleep(0.05)

    # "info?" llegó mientras se generaba la respuesta a "hola": se manda una sola.
    assert sent == [("123", "respuesta a: hola / info?")]


def test_reenvia_los_avisos_a_las_otras_cuentas(monkeypatch):
    forwarded = []

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, content, headers):
            forwarded.append((url, content, headers["X-Hub-Signature-256"]))
            return bot.httpx.Response(200, request=bot.httpx.Request("POST", url))

    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bot.config, "FORWARD_WEBHOOKS", ["http://127.0.0.1:8002/webhook"])

    async def main():
        await bot.forward(b'{"entry": []}', "sha256=abc")

    asyncio.run(main())
    assert forwarded == [("http://127.0.0.1:8002/webhook", b'{"entry": []}', "sha256=abc")]


def test_token_nuevo_en_env_reemplaza_al_renovado(monkeypatch):
    from igbot import instagram as ig_mod

    async def main():
        store = Store(":memory:")
        await store.open()
        ig = ig_mod.Instagram(store)
        monkeypatch.setattr(cuenta(), "access_token", "viejo")
        assert await ig.token() == "viejo"
        await store.set("ig_token", "viejo-renovado")  # lo que hace la renovación semanal
        assert await ig.token() == "viejo-renovado"
        monkeypatch.setattr(cuenta(), "access_token", "nuevo")  # se pegó otro en .env
        assert await ig.token() == "nuevo"
        await ig.close()
        await store.close()

    asyncio.run(main())


def test_firma_con_la_llave_de_meta_o_la_de_instagram(monkeypatch):
    import app as bot
    from igbot import config

    monkeypatch.setattr(config, "APP_SECRET", "meta")
    monkeypatch.setattr(config, "IG_OAUTH_SECRET", "instagram")
    body = b'{"entry": []}'

    def firma(secret):
        return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

    assert bot.signature_ok(body, firma(b"meta"))
    assert bot.signature_ok(body, firma(b"instagram"))
    assert not bot.signature_ok(body, firma(b"otra"))
    assert not bot.signature_ok(body, None)
