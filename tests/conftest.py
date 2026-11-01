import os
import sys

import pytest

# Las pruebas de DMs esperan la respuesta como mucho un segundo: la espera para agrupar mensajes debe ser corta
# en toda la corrida, sin importar qué módulo importe `app` primero (config lee el entorno al importarse).
os.environ.setdefault("DEBOUNCE_SECONDS", "0.2")


@pytest.fixture(autouse=True)
def no_instagram_on_startup(monkeypatch):
    """Al arrancar la app en pruebas, no se llama a Instagram (suscripción y botones)
    ni corren los bucles de fondo (token, publicaciones, reporte semanal)."""
    if "app" in sys.modules:
        bot = sys.modules["app"]

        async def nothing(*a, **k):
            pass

        monkeypatch.setattr(bot.instagram, "subscribe", nothing)
        monkeypatch.setattr(bot.instagram, "set_ice_breakers", nothing)
        monkeypatch.setattr(bot, "fondo", lambda: [])


@pytest.fixture(autouse=True)
def sin_revision_de_instrucciones(monkeypatch):
    """Guardar el asistente no gasta una llamada de Claude en revisarlo (test_panel prueba la revisión)."""
    if "igbot.panel" in sys.modules:
        panel = sys.modules["igbot.panel"]

        async def aprobar(c, texto):
            return ""

        monkeypatch.setattr(panel, "revisar_instrucciones", aprobar)


@pytest.fixture(autouse=True)
def trabajos_al_momento(monkeypatch):
    """Los trabajos largos (campañas, imágenes) contestan ya terminados: los generadores falsos tardan nada."""
    if "igbot.panel" in sys.modules:
        monkeypatch.setattr(sys.modules["igbot.panel"], "ESPERA_TRABAJO", 30)
