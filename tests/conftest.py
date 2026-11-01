import os
import sys

import pytest

# Las pruebas de DMs esperan la respuesta como mucho un segundo: la espera para agrupar mensajes debe ser corta
# en toda la corrida, sin importar qué módulo importe `app` primero (config lee el entorno al importarse).
os.environ.setdefault("DEBOUNCE_SECONDS", "0.2")
# El entorno mínimo para importar `app` y `igbot.config` en cualquier prueba (antes cada archivo lo repetía).
for _k, _v in dict(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:").items():
    os.environ.setdefault(_k, _v)


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


@pytest.fixture
def cuenta_de_prueba(monkeypatch):
    """La cuenta principal con una base nueva en memoria y el bucle para usarla: `c, correr = cuenta_de_prueba`
    y `correr(main())` en vez de `asyncio.run` (la conexión de aiosqlite vive en un solo bucle). La base se
    cierra al terminar, así una prueba que falla a medias no deja el `store` cerrado o sucio para las siguientes."""
    import asyncio

    import app as bot
    from igbot.store import Store

    loop = asyncio.new_event_loop()
    store = Store(":memory:")
    loop.run_until_complete(store.open())
    monkeypatch.setattr(bot.principal, "store", store)
    monkeypatch.setattr(bot.principal.instagram, "store", store)  # Instagram guarda ahí los nombres de usuario

    async def sin_red(user_id, *a, **k):  # el @usuario de cada cliente, sin preguntarle a Meta
        return "@" + user_id

    monkeypatch.setattr(bot.principal.instagram, "username", sin_red)
    yield bot.principal, loop.run_until_complete
    loop.run_until_complete(store.close())
    loop.close()
