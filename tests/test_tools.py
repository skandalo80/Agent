"""Reglas de las herramientas de agenda y correo, con un Google falso."""
import asyncio
import os
from datetime import datetime, timedelta

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="s", IG_ACCESS_TOKEN="t")

import pytest  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
from igbot import config, tools  # noqa: E402
from igbot.store import Store  # noqa: E402

# Martes 14/10/2026 08:00 en Ciudad de México.
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=tools.tz())


class FakeGoogle:
    def __init__(self, busy=()):
        self._busy = list(busy)
        self.created, self.cancelled, self.sent = [], [], []

    async def busy(self, start, end):
        return self._busy

    async def create_meeting(self, start, end, tz, email, summary, description, meet=True):
        self.created.append((start, email))
        return f"ev{len(self.created)}", "https://meet.google.com/abc"

    async def cancel_meeting(self, event_id):
        self.cancelled.append(event_id)

    async def send_email(self, sender, name, to, cc, subject, body, attachments=()):
        self.sent.append((sender, to, cc, subject))
        self.attachments = list(attachments)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(tools, "now", lambda: NOW)
    monkeypatch.setattr(config, "MAIL_FROM", "info@negocio.com")
    monkeypatch.setattr(cuenta(), "mail_cc", "copia@negocio.com")

    async def make(busy=()):
        store = Store(":memory:")
        await store.open()
        google = FakeGoogle(busy)
        await store.add_incoming("ana", "m1", "mi correo es Ana@Mail.com")
        by_name = {t.name: t.handler for t in tools.workspace_tools(store, google, "ana")}
        return store, google, by_name

    return make


def run(coro):
    return asyncio.run(coro)


def text(result):
    return result["content"][0]["text"]


def test_disponibilidad_respeta_horario_ocupado_y_anticipacion(env):
    async def main():
        busy = [("2026-10-14T12:00:00-06:00", "2026-10-14T13:00:00-06:00")]
        store, _, t = await env(busy)
        out = text(await t["ver_disponibilidad"]({"fecha": "2026-10-14"}))
        assert "09:30" not in out and "10:00" in out  # 2 h de anticipación desde las 8:00
        assert "12:00" not in out and "12:30" not in out and "13:00" in out
        assert "17:30" in out and "18:00" not in out
        assert "no hay atención" in text(await t["ver_disponibilidad"]({"fecha": "2026-10-18"}))
        await store.close()

    run(main())


def test_agendar_solo_con_correo_que_la_persona_escribio(env):
    async def main():
        store, google, t = await env()
        args = {"fecha": "2026-10-15", "hora": "10:00", "nombre": "Ana", "motivo": "info"}
        r = await t["agendar_reunion"]({**args, "email": "otro@victima.com"})
        assert r.get("is_error") and not google.created
        r = await t["agendar_reunion"]({**args, "email": "ana@mail.com"})
        assert not r.get("is_error") and google.created == [("2026-10-15T10:00:00-06:00", "ana@mail.com")]
        assert "ev1" in text(await t["mis_reuniones"]({}))
        await store.close()

    run(main())


def test_agendar_valida_horario_y_limite(env):
    async def main():
        store, google, t = await env()
        base = {"email": "ana@mail.com", "nombre": "Ana", "motivo": "x"}
        assert (await t["agendar_reunion"]({**base, "fecha": "2026-10-15", "hora": "20:00"}))["is_error"]
        assert (await t["agendar_reunion"]({**base, "fecha": "2026-10-14", "hora": "09:00"}))["is_error"]
        for hora in ["10:00", "11:00"]:
            assert not (await t["agendar_reunion"]({**base, "fecha": "2026-10-15", "hora": hora})).get("is_error")
        r = await t["agendar_reunion"]({**base, "fecha": "2026-10-16", "hora": "10:00"})
        assert r["is_error"] and "máximo" in text(r)
        await t["cancelar_reunion"]({"id": "ev1"})
        assert google.cancelled == ["ev1"]
        assert not (await t["agendar_reunion"]({**base, "fecha": "2026-10-16", "hora": "10:00"})).get("is_error")
        await store.close()

    run(main())


def test_agendar_rechaza_horario_ocupado(env):
    async def main():
        busy = [("2026-10-15T09:45:00-06:00", "2026-10-15T10:15:00-06:00")]
        store, google, t = await env(busy)
        r = await t["agendar_reunion"](
            {"fecha": "2026-10-15", "hora": "10:00", "email": "ana@mail.com", "nombre": "A", "motivo": ""}
        )
        assert r["is_error"] and not google.created
        await store.close()

    run(main())


def test_no_cancela_reuniones_de_otra_persona(env):
    async def main():
        store, google, t = await env()
        start = (NOW + timedelta(days=1)).isoformat()
        await store.add_meeting("ajena", "beto", start, "b@b.com", "")
        assert (await t["cancelar_reunion"]({"id": "ajena"}))["is_error"]
        assert google.cancelled == []
        await store.close()

    run(main())


def test_correo_solo_al_cliente_con_copia_y_limite(env):
    async def main():
        store, google, t = await env()
        assert (await t["enviar_correo"]({"email": "x@y.com", "asunto": "a", "cuerpo": "b"}))["is_error"]
        for _ in range(cuenta().max_emails_per_user_per_day):
            r = await t["enviar_correo"]({"email": "ana@mail.com", "asunto": "Info", "cuerpo": "hola"})
            assert not r.get("is_error")
        assert google.sent[0] == ("info@negocio.com", "ana@mail.com", "copia@negocio.com", "Info")
        r = await t["enviar_correo"]({"email": "ana@mail.com", "asunto": "Info", "cuerpo": "hola"})
        assert r["is_error"]
        await store.close()

    run(main())
