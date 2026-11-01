"""Fotos, audios y demás adjuntos en DMs: el asistente sabe qué le mandaron."""
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:",
                  DEBOUNCE_SECONDS="0.2")  # como test_admin y test_bot: el primero en importar app fija la espera

import test_contenido  # noqa: E402
from igbot import adjuntos  # noqa: E402
from test_contenido import bot  # noqa: E402
from test_historias import recibir, textos  # noqa: E402

cliente = test_contenido.cliente


def esperar(client, c, quien="adj"):
    for _ in range(100):  # se describe en segundo plano
        if textos(client, c, quien):
            break
        client.portal.call(lambda: __import__("anyio").sleep(0.02))
    return textos(client, c, quien)


def test_foto_y_audio_descritos(cliente, monkeypatch):
    client, c, *_ = cliente
    client.portal.call(c.store.set, "bot_activo", "1")

    async def describir(c, url):
        assert url == "https://cdn/foto.jpg"
        return "Una blusa roja de manga larga sobre una mesa."

    async def transcribir(c, url):
        assert url == "https://cdn/audio.mp4"
        return "Hola, ¿la tienen en talla M?"

    monkeypatch.setattr(adjuntos, "describir", describir)
    monkeypatch.setattr(adjuntos, "transcribir", transcribir)
    recibir(client, c, quien="adj", mensaje={"mid": "adj1", "attachments": [{"type": "image", "payload": {"url": "https://cdn/foto.jpg"}}],
                        "text": "¿Este?"})
    recibir(client, c, quien="adj", mensaje={"mid": "adj2", "attachments": [{"type": "audio", "payload": {"url": "https://cdn/audio.mp4"}}]})
    recibir(client, c, quien="adj", mensaje={"mid": "adj3", "attachments": [{"type": "video", "payload": {"url": "https://cdn/v.mp4"}},
                                                     {"type": "like_heart"}]})
    for _ in range(100):
        if len(textos(client, c, "adj")) == 3:
            break
        client.portal.call(lambda: __import__("anyio").sleep(0.02))
    assert sorted(textos(client, c, "adj")) == sorted([
        "[envió una foto: Una blusa roja de manga larga sobre una mesa.] ¿Este?",
        "[envió un audio que dice: «Hola, ¿la tienen en talla M?»]",
        "[envió un video] [mandó un corazón]"])


def test_sin_poder_ver_ni_oir(cliente, monkeypatch):
    client, c, *_ = cliente
    client.portal.call(c.store.set, "bot_activo", "1")

    async def nada(c, url):
        return ""

    monkeypatch.setattr(adjuntos, "describir", nada)
    monkeypatch.setattr(adjuntos, "transcribir", nada)
    recibir(client, c, quien="adj", mensaje={"mid": "adj1", "attachments": [{"type": "image", "payload": {"url": "https://cdn/x.jpg"}},
                                                     {"type": "audio", "payload": {"url": "https://cdn/a.mp4"}}]})
    assert esperar(client, c, "adj") == ["[envió una foto que no se pudo ver] [envió un audio que no se pudo escuchar;"
                                  " pídele que lo escriba]"]


def test_muchas_fotos_solo_se_describen_tres(cliente, monkeypatch):
    client, c, *_ = cliente
    client.portal.call(c.store.set, "bot_activo", "1")
    vistas = []

    async def describir(c, url):
        vistas.append(url)
        return "algo"

    monkeypatch.setattr(adjuntos, "describir", describir)
    recibir(client, c, quien="adj", mensaje={"mid": "adj1", "attachments": [{"type": "image", "payload": {"url": f"u{i}"}} for i in range(5)]})
    assert esperar(client, c, "adj") == ["[envió una foto: algo] " * 3 + "[envió una foto] [envió una foto]"]
    assert len(vistas) == 3


def test_transcribir_sin_openai(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(adjuntos.config, "OPENAI_API_KEY", "")
    assert client.portal.call(adjuntos.transcribir, c, "https://cdn/a.mp4") == ""


def test_el_eco_sigue_corto():
    assert bot.message_text({"attachments": [{"type": "image"}]}) == "[envió: image]"
