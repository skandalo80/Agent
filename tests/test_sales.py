"""Pasar a humano y pausa cuando contesta el dueño (las cotizaciones, en test_cotizacion.py)."""
import hashlib
import hmac
import json
import time

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import config, tools  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_tools import NOW, FakeGoogle, run  # noqa: E402


def test_el_prompt_ya_no_trae_un_catalogo_de_archivo():
    prompt = bot.agent.system_prompt()
    assert "<catalogo>" not in prompt and "nortex" not in prompt.lower()


@pytest.fixture
def sales(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "now", lambda: NOW)
    monkeypatch.setattr(config, "MAIL_FROM", "yo@gmail.com")
    monkeypatch.setattr(cuenta(), "mail_cc", "copia@gmail.com")
    monkeypatch.setattr(cuenta(), "owner_email", "yo@gmail.com")
    monkeypatch.setattr(cuenta(), "quotes_dir", str(tmp_path))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")

    async def make():
        store = Store(":memory:")
        await store.open()
        await store.add_incoming("ana", "m1", "mi correo es ana@mail.com")
        google, state = FakeGoogle(), {}
        all_tools = tools.human_tools(store, google, "ana", state)
        return store, google, None, state, {t.name: t.handler for t in all_tools}

    return make


def test_pasar_a_humano_pausa_y_avisa(sales):
    async def main():
        store, google, ig, state, t = await sales()
        await t["pasar_a_humano"]({"motivo": "quiere descuento"})
        assert await store.paused_until("ana") > time.time()
        assert google.sent[0][1] == "yo@gmail.com"
        await store.close()

    run(main())


def test_si_el_duenio_contesta_el_bot_se_calla(monkeypatch):
    replies = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        replies.append(new_messages)
        return "ok", False

    async def fake_send(user_id, text):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    monkeypatch.setattr(bot, "ECHO_WAIT", 0)

    def post(event):
        body = json.dumps({"entry": [{"messaging": [event]}]}).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        client.post("/webhook", content=body, headers={"X-Hub-Signature-256": f"sha256={sig}"})

    def echo(user, mid, txt):
        return {"sender": {"id": "negocio"}, "recipient": {"id": user},
                "message": {"mid": mid, "text": txt, "is_echo": True}}

    def dm(user, mid, txt):
        return {"sender": {"id": user}, "recipient": {"id": "negocio"},
                "message": {"mid": mid, "text": txt}}

    with TestClient(bot.app) as client:
        run_ = client.portal.call
        run_(bot.store.add_sent, "del-bot")
        post(echo("luis", "del-bot", "respuesta del bot"))  # eco propio: no pausa
        post(echo("ana", "a-mano", "Hola Ana, soy Skandar"))  # el dueño escribió
        time.sleep(0.3)
        post(dm("ana", "a1", "gracias!"))
        post(dm("luis", "l1", "hola"))
        time.sleep(1)
        assert replies == [["hola"]]  # solo luis recibe respuesta del bot
        history = run_(bot.store.recent, "ana", 10)
        assert ("assistant", "[contestó una persona del equipo] Hola Ana, soy Skandar") in history
        assert ("user", "gracias!") in history


def test_el_prompt_lleva_el_estilo():
    prompt = bot.agent.system_prompt()
    assert "<estilo>" in prompt and "Una sola pregunta" in prompt and "<temas>" in prompt


def test_el_eco_espera_a_que_el_bot_anote_su_envio(monkeypatch):
    """El eco llega antes de que send_text anote el id: no es el dueño, el chat no se pausa."""
    import asyncio

    async def main():
        store = bot.cuenta().store
        await store.open()
        monkeypatch.setattr(bot, "ECHO_WAIT", 1)
        monkeypatch.setattr(bot, "ECHO_PASO", 0.05)

        async def anotar_tarde():
            await asyncio.sleep(0.2)
            await store.add_sent("tarde")

        asyncio.create_task(anotar_tarde())
        await bot.owner_replied("ana", "tarde", "Hola Ana")
        assert await store.paused_until("ana") == 0
        # Un eco con id desconocido y texto nuevo sí es el dueño: pausa.
        await bot.owner_replied("ana", "a-mano", "Te mando la lista")
        assert await store.paused_until("ana") > 0
        await store.close()

    asyncio.run(main())
