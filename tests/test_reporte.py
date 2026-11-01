"""Reporte semanal con datos de prueba (sin red ni Claude)."""
import asyncio
import time
from datetime import datetime

from igbot.cuenta import cuenta  # noqa: E402
from igbot import reporte, tools  # noqa: E402
from igbot.store import Store  # noqa: E402


class FakeIG:
    def __init__(self):
        self.texts = []

    async def username(self, user_id):
        return "@" + user_id

    async def send_text(self, user_id, text):
        self.texts.append((user_id, text))


class FakeGoogle:
    def __init__(self):
        self.sent = []

    async def send_email(self, sender, name, to, cc, subject, body, attachments=()):
        self.sent.append((to, subject, body))


async def no_ai(store, since, until):
    return "Preguntan mucho por bordado."


def run(coro):
    return asyncio.run(coro)


async def seeded():
    store = Store(":memory:")
    await store.open()
    await store.set("account_username", "nortex_uniformes")
    old = time.time() - 10 * 86400
    await store.db.execute(  # escribió hace 10 días: esta semana no es nueva
        "INSERT INTO messages (user_id, role, text, mid, created_at) VALUES ('ana', 'user', 'hola', 'm0', ?)", (old,))
    await store.add_incoming("ana", "m1", "cotízame 60 polos")
    await store.add_incoming("beto", "m2", "precio de gorras?")
    await store.add_incoming("admin:jefe", "m3", "publica esto")  # no cuenta
    await store.mark_answered("ana", [2], "Son $12,000")
    await store.add_owner_message("beto", "m4", "Yo te ayudo")
    await store.add_event("ana", "cotizacion", {"total": 1000.0, "productos": [{"producto": "Polo", "cantidad": 20}]})
    await store.add_event("ana", "cotizacion", {"total": 14534.8, "productos": [{"producto": "Polo", "cantidad": 60}]})
    await store.add_event("beto", "cotizacion", {"total": 3000.0, "productos": [{"producto": "Gorra", "cantidad": 24}]})
    await store.add_event("beto", "humano", {"motivo": "quiere descuento"})
    await store.pause("beto", time.time() + 3600)
    await store.add_comment("c1", "carla", "m1", "precio")
    await store.comment_result("c1", "PRECIO", True, True)
    post = await store.add_post("jefe", [1], "Nuevos polos", 0)
    await store.set_post(post, "publicada", permalink="https://www.instagram.com/p/X/")
    return store


def test_reporte_con_lo_de_la_semana():
    async def main():
        store = await seeded()
        try:
            return await reporte.build(store, FakeIG(), ai=no_ai)
        finally:
            await store.close()

    subject, text = run(main())
    assert subject.startswith("Reporte semanal de @nortex_uniformes")
    assert "2 personas escribieron (1 nuevas) y mandaron 2 mensajes" in text
    assert "El bot contestó 1 veces" in text
    # Ana recotizó: cuenta solo su última cotización.
    assert "2 personas pidieron cotización, por $17,534.80" in text
    assert "Polo (60 pzas), Gorra (24 pzas)" in text
    assert "pasó 1 conversaciones a una persona; contestaste a mano a 1" in text
    assert "En pausa ahora: @beto" in text
    assert "1 comentarios" in text and "PRECIO (1)" in text
    assert "https://www.instagram.com/p/X/" in text
    assert text.endswith("Preguntan mucho por bordado.")
    assert "publica esto" not in text


def test_semana_vacia_no_truena():
    async def main():
        store = Store(":memory:")
        await store.open()
        try:
            return await reporte.build(store, None, ai=no_ai)
        finally:
            await store.close()

    _, text = run(main())
    assert "0 personas escribieron" in text and "VENTAS" not in text


def test_manda_por_correo_y_dm(monkeypatch):
    monkeypatch.setattr(cuenta(), "report_email", "dueno@correo.com")
    monkeypatch.setattr(cuenta(), "admin_ig_ids", {"jefe"})
    monkeypatch.setattr(reporte, "topics", no_ai)

    async def main():
        store = await seeded()
        ig, google = FakeIG(), FakeGoogle()
        where = await reporte.send(store, ig, google)
        await store.close()
        return where, ig, google

    where, ig, google = run(main())
    assert where == "correo a dueno@correo.com, DM"
    assert google.sent[0][0] == "dueno@correo.com"
    assert ig.texts[0][0] == "jefe" and ig.texts[0][1].startswith("CONVERSACIONES")


def test_cuando_toca():
    monday_9 = datetime(2026, 10, 19, 9, 5, tzinfo=tools.tz())
    assert reporte.due(monday_9, None) == "2026-43"
    assert reporte.due(monday_9, "2026-43") is None  # ya se mandó esta semana
    assert reporte.due(monday_9.replace(hour=8), None) is None
    assert reporte.due(datetime(2026, 10, 20, 10, tzinfo=tools.tz()), None) is None  # martes
