"""Resultados en el panel: números de las conversaciones, gráficas y bloque de contenido."""
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import resultados  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from test_conectar import setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402

MX = ZoneInfo("America/Mexico_City")


def t(dia: int, hora: int, minuto: int = 0, segundo: int = 0) -> float:
    return datetime(2026, 10, dia, hora, minuto, segundo, tzinfo=MX).timestamp()


def test_conversaciones_tiempos_horario_y_personas(monkeypatch):
    c = cuenta()
    monkeypatch.setattr(c, "business_hours", "09:00-18:00")
    monkeypatch.setattr(c, "business_days", [1, 2, 3, 4, 5])
    filas = [
        ("ana", "user", t(19, 10), 0),  # lunes 10:00, en horario
        ("ana", "user", t(19, 10, 0, 5), 0),  # se juntan: cuenta desde el primero
        ("ana", "assistant", t(19, 10, 0, 20), 0),
        ("beto", "user", t(19, 23), 0),  # lunes 23:00, fuera de horario
        ("beto", "assistant", t(19, 23, 1), 0),
        ("carla", "user", t(25, 12), 0),  # domingo: fuera de horario
        ("carla", "assistant", t(25, 12, 0, 40), 1),  # la atendió una persona
        ("dani", "assistant", t(20, 9), 0),  # DM por un comentario: no responde a nada
    ]
    d = resultados.conversaciones(filas, c, datetime(2026, 10, 19, tzinfo=MX), 7)
    assert d["por_dia"] == [3, 0, 0, 0, 0, 0, 1]
    assert d["por_hora"][10] == 2 and d["por_hora"][23] == 1 and d["por_hora"][12] == 1
    assert d["respuesta"] == 40  # mediana de 20 s y 60 s (la de la persona no cuenta)
    assert d["fuera"] == 1  # solo la de beto: a carla no le contestó el asistente
    assert d["a_mano"] == {"carla"}
    assert resultados.dias_de_horario(c) == "lun–vie 9:00–18:00"


def test_duracion():
    assert resultados.duracion(None) == "—"
    assert resultados.duracion(0.2) == "1 s"
    assert resultados.duracion(42) == "42 s"
    assert resultados.duracion(600) == "10 min"
    assert resultados.duracion(7200) == "2 h"


def test_barras_sin_datos_no_truenan():
    svg = resultados.barras([0, 0, 0], ["a", "", "c"], ["1", "2", "3"], descripcion="vacía")
    assert svg.count("<rect") == 3 and 'aria-label="vacía"' in svg and svg.count("<span>") == 3


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    """Un cliente conectado (cafe.lunar), con sesión en su panel."""
    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(resultados, "_cache", {})

    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        yield client, bot.buscar("1784")


def test_sin_sesion_va_al_panel():
    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/panel/resultados", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/panel"


def test_basico_sin_mensajes_y_con_mensajes(cliente, monkeypatch):
    client, c = cliente
    assert 'href="/panel/resultados">Resultados</a>' in client.get("/panel").text  # todos los planes
    assert "Contenido</a>" not in client.get("/panel").text
    assert "Aún no hay mensajes" in client.get("/panel/resultados").text

    async def conversar():
        await c.store.add_incoming("ana", "m1", "¿precio?")
        await c.store.mark_answered("ana", [1], "Son $55")
        await c.store.add_incoming("beto", "m2", "quiero hablar con alguien")
        await c.store.add_owner_message("beto", "m3", "Yo te ayudo")
        await c.store.add_incoming("admin:yo", "m4", "no cuenta")

    client.portal.call(conversar)
    assert "Aún no hay mensajes" in client.get("/panel/resultados").text  # sigue en caché
    monkeypatch.setattr(resultados, "_cache", {})
    html = client.get("/panel/resultados").text
    assert "1 de 2 sin pasar a una persona" in html and "50%" in html
    assert "1 respuesta de tu asistente" in html
    assert html.count("<svg") == 2 and "no cuenta" not in html
    assert "De comentario a mensaje" not in html  # Básico: sin bloque de contenido
    assert 'class="on">30 días' in client.get("/panel/resultados?dias=30").text
    assert 'class="on">7 días' in client.get("/panel/resultados?dias=999").text


def test_community_ve_comentarios_y_publicaciones(cliente, monkeypatch):
    client, c = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)

    async def actividad():
        await c.store.add_comment("c1", "carla", "m1", "precio")
        await c.store.comment_result("c1", "PRECIO", True, True)
        await c.store.add_comment("c2", "spam", "m1", "gana dinero")
        await c.store.comment_result("c2", resultados.SPAM, False, False)
        post = await c.store.add_post("panel", [1], "Nueva carta de otoño", 0)
        await c.store.set_post(post, "publicada", permalink="https://www.instagram.com/p/X/")
        await c.store.add_post("panel", [2], "Programado", time.time() + 86400)
        await c.store.set_post(2, "programada")

    client.portal.call(actividad)
    html = client.get("/panel/resultados").text
    assert 'href="/panel/contenido">Contenido</a>' in html
    assert "De comentario a mensaje" in html and "PRECIO · 1" in html and "Spam ocultado" in html
    assert "1 publicada · 1 programada" in html and "https://www.instagram.com/p/X/" in html
