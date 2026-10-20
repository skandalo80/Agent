"""Botones de inicio (ice breakers), sin red."""
import asyncio
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
from igbot import botones  # noqa: E402
from igbot.store import Store  # noqa: E402


class FakeIG:
    def __init__(self):
        self.calls = []

    async def set_ice_breakers(self, questions):
        self.calls.append(questions)


def test_los_botones_de_cada_cuenta_son_validos():
    for path in ("negocio/botones.yaml", "clientes/nortex/botones.yaml"):
        assert 1 <= len(botones.load(path)) <= 4


def test_se_mandan_solo_si_cambiaron(monkeypatch, tmp_path):
    file = tmp_path / "botones.yaml"
    monkeypatch.setattr(cuenta(), "buttons_path", str(file))

    async def main():
        store = Store(":memory:")
        await store.open()
        ig = FakeIG()
        await botones.sync(store, ig)  # sin archivo: nada
        file.write_text("preguntas:\n  - ¿Precios?\n  - ¿Horario?\n", encoding="utf-8")
        await botones.sync(store, ig)
        await botones.sync(store, ig)  # reinicio sin cambios
        file.write_text("preguntas:\n  - ¿Precios?\n", encoding="utf-8")
        await botones.sync(store, ig)
        await store.close()
        return ig.calls

    assert asyncio.run(main()) == [["¿Precios?", "¿Horario?"], ["¿Precios?"]]


def test_maximo_cuatro_preguntas(tmp_path):
    file = tmp_path / "b.yaml"
    file.write_text("preguntas: [a, b, c, d, e]", encoding="utf-8")
    with pytest.raises(ValueError):
        botones.load(str(file))
