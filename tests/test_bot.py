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

    async def fake_reply(store, google, user_id, new_messages):
        return "respuesta a: " + " / ".join(new_messages)

    async def fake_summary(store, user_id):
        pass

    async def fake_send(user_id, text):
        sent.append((user_id, text))

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", fake_summary)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    monkeypatch.setattr(bot.instagram, "refresh_loop", lambda: asyncio.sleep(0))

    def post(payload):
        body = json.dumps(payload).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        return client.post(
            "/webhook", content=body,
            headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
        )

    def dm(mid, text, echo=False):
        return {"entry": [{"messaging": [{
            "sender": {"id": "123"},
            "message": {"mid": mid, "text": text, **({"is_echo": True} if echo else {})},
        }]}]}

    with TestClient(bot.app) as client:
        assert client.post("/webhook", content=b"{}").status_code == 403
        assert post(dm("m1", "hola")).status_code == 200
        post(dm("m1", "hola"))  # reintento de Meta
        post(dm("m2", "info?"))
        post(dm("m3", "eco", echo=True))
        deadline = time.time() + 3
        while not sent and time.time() < deadline:
            time.sleep(0.05)

    assert sent == [("123", "respuesta a: hola / info?")]
