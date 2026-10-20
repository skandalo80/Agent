"""Topes contra abuso: largo de cada DM, respuestas por persona y gasto diario."""
import asyncio
import os

os.environ.update(
    IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok",
    DB_PATH=":memory:", DEBOUNCE_SECONDS="0.2",
)

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import limites  # noqa: E402
from igbot.store import Store  # noqa: E402


def setup(monkeypatch):
    sent, asked = [], []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        asked.append(new_messages)
        return "ok", False

    async def fake_send(user_id, text):
        sent.append(text)

    async def nothing(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    return sent, asked


async def new_store(monkeypatch):
    store = Store(":memory:")
    await store.open()
    monkeypatch.setattr(bot, "store", store)
    monkeypatch.setattr(limites, "store", store)
    return store


async def dm(store, n):
    await store.add_incoming("ana", f"m{n}", f"mensaje {n}")
    await bot.handle("ana", await store.pending("ana"))


def test_los_mensajes_largos_se_recortan(monkeypatch):
    monkeypatch.setattr(cuenta(), "max_message_chars", 10)
    assert limites.trim("corto") == "corto"
    assert limites.trim("x" * 50) == "x" * 10 + " […mensaje recortado]"


def test_tope_diario_por_persona(monkeypatch):
    sent, asked = setup(monkeypatch)
    monkeypatch.setattr(cuenta(), "max_replies_per_user_per_day", 2)

    async def main():
        store = await new_store(monkeypatch)
        for n in range(5):
            await dm(store, n)
        # Dos respuestas, un aviso y luego silencio; Claude solo se llamó dos veces.
        assert sent == ["ok", "ok", limites.LIMIT_NOTICE]
        assert len(asked) == 2
        assert await store.pending_users() == []
        await store.close()

    asyncio.run(main())


def test_presupuesto_diario(monkeypatch):
    sent, asked = setup(monkeypatch)
    monkeypatch.setattr(cuenta(), "daily_budget_usd", 1.0)

    async def main():
        store = await new_store(monkeypatch)
        await dm(store, 0)
        await limites.add_cost(0.6)
        await limites.add_cost(0.5)
        await dm(store, 1)
        assert sent == ["ok"] and len(asked) == 1
        assert await limites.first_budget_alert(store) is False  # ya se avisó al pasarlo
        await store.close()

    asyncio.run(main())
