import sys

import pytest


@pytest.fixture(autouse=True)
def no_instagram_on_startup(monkeypatch):
    """Al arrancar la app en pruebas, no se llama a Instagram (suscripción y botones)."""
    if "app" in sys.modules:
        bot = sys.modules["app"]

        async def nothing(*a, **k):
            pass

        monkeypatch.setattr(bot.instagram, "subscribe", nothing)
        monkeypatch.setattr(bot.instagram, "set_ice_breakers", nothing)
