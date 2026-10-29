"""Resultados en el panel: números de las conversaciones, gráficas y bloque de contenido."""
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import panel, resultados  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from test_conectar import setup  # noqa: E402
from test_cuenta import FakeInstagram  # noqa: E402

ORIGEN = {"Origin": "https://console.skandar.online"}

MX = ZoneInfo("America/Mexico_City")


def t(dia: int, hora: int, minuto: int = 0, segundo: int = 0) -> float:
    return datetime(2026, 10, dia, hora, minuto, segundo, tzinfo=MX).timestamp()


def test_conversaciones_tiempos_horario_y_personas(monkeypatch, tmp_path):
    import asyncio

    from igbot.store import MANUAL, Store
    c = cuenta()
    monkeypatch.setattr(c, "business_hours", "09:00-18:00")
    monkeypatch.setattr(c, "business_days", [1, 2, 3, 4, 5])
    filas = [
        ("ana", "user", t(19, 10), "hola"),  # lunes 10:00, en horario
        ("ana", "user", t(19, 10, 0, 5), "?"),  # se juntan: cuenta desde el primero
        ("ana", "assistant", t(19, 10, 0, 20), "hola"),
        ("beto", "user", t(19, 23), "hola"),  # lunes 23:00, fuera de horario
        ("beto", "assistant", t(19, 23, 1), "hola"),
        ("carla", "user", t(25, 12), "hola"),  # domingo: fuera de horario
        ("carla", "assistant", t(25, 12, 0, 40), MANUAL + " ya voy"),  # la atendió una persona
        ("dani", "assistant", t(20, 9), "info"),  # DM por un comentario: no responde a nada
        ("eva", "user", t(18, 10), "antes"),  # antes del periodo: no cuenta
        ("admin:yo", "user", t(19, 11), "prueba"),  # el dueño probando: no cuenta
    ]

    async def calcular():
        store = Store(str(tmp_path / "x.db"))
        await store.open()
        await store.db.executemany("INSERT INTO messages (user_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                                   [(u, r, x, ts) for u, r, ts, x in filas])
        await store.db.commit()
        # De 2 en 2 para probar que se recorren por partes.
        d = await resultados.conversaciones(store.message_times(t(19, 0), t(26, 0), chunk=2), c,
                                            datetime(2026, 10, 19, tzinfo=MX), 7)
        await store.close()
        return d

    d = asyncio.run(calcular())
    assert d["escribieron"] == {"ana", "beto", "carla"} and d["contestados"] == 3 and d["chats_contestados"] == 3
    assert d["por_dia"] == [3, 0, 0, 0, 0, 0, 1]
    assert d["por_hora"][10] == 2 and d["por_hora"][23] == 1 and d["por_hora"][12] == 1
    assert d["respuesta"] == 40  # mediana de 20 s y 60 s (la de la persona no cuenta)
    assert d["fuera"] == 1  # solo la de beto: a carla no le contestó el asistente
    assert d["a_mano"] == {"carla"}
    assert resultados.mediana(Counter({5: 1, 9: 2, 100: 1})) == 9 and resultados.mediana(Counter()) is None
    assert resultados.dias_de_horario(c) == "lun–vie 9:00–18:00"


def test_duracion():
    assert resultados.duracion(None) == "—"
    assert resultados.duracion(0.2) == "1 s"
    assert resultados.duracion(42) == "42 s"
    assert resultados.duracion(600) == "10 min"
    assert resultados.duracion(7200) == "2 h"


def test_barras_sin_datos_no_truenan():
    svg = resultados.barras([0, 0, 0], ["a", "", "c"], ["1", "2", "3"], descripcion="vacía")
    assert svg.count("<rect") == 3 and 'aria-label="vacía"' in svg and svg.count('class="mh') == 3
    assert svg.split('class="ejex"')[1].count("<span>") == 3 and "<title>" not in svg


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
    assert 'href="/panel/resultados" title="Resultados"' in client.get("/panel/asistente").text  # todos los planes
    assert 'title="Contenido"' not in client.get("/panel").text
    assert "Aún no hay mensajes" in client.get("/panel/resultados?ver=conversaciones").text

    async def conversar():
        await c.store.add_incoming("ana", "m1", "¿precio?")
        await c.store.mark_answered("ana", [1], "Son $55")
        await c.store.add_incoming("beto", "m2", "quiero hablar con alguien")
        await c.store.add_owner_message("beto", "m3", "Yo te ayudo")
        await c.store.add_incoming("admin:yo", "m4", "no cuenta")

    client.portal.call(conversar)
    assert "Aún no hay mensajes" in client.get("/panel/resultados?ver=conversaciones").text  # sigue en caché
    monkeypatch.setattr(resultados, "_cache", {})
    html = client.get("/panel/resultados?ver=conversaciones").text
    assert "1 de 2 sin ayuda" in html and "50%" in html
    assert ">1 respuesta<" in html
    assert html.split("<main")[1].count("<svg") == 2 and "no cuenta" not in html
    assert "De dónde es tu público" in html and "Seguidores nuevos" not in html  # solo conversaciones
    assert "De comentario a mensaje" not in html  # Básico: sin bloque de contenido
    assert 'class="on">30 días' in client.get("/panel/resultados?dias=30").text
    html = client.get("/panel/resultados?dias=30&ver=conversaciones").text
    assert 'class="on">Conversaciones' in html and 'href="/panel/resultados?dias=7&ver=conversaciones"' in html
    assert 'class="on">7 días' in client.get("/panel/resultados?dias=999").text


def test_inicio_del_panel(cliente, monkeypatch):
    client, c = cliente
    lista = {"empresa": False}

    async def empresa_lista(c):
        return lista["empresa"]

    monkeypatch.setattr(panel, "empresa_lista", empresa_lista)
    # Recién conectada: primero su Perfil de empresa; mientras no lo llene ni lo omita, ahí se queda.
    for _ in range(2):
        r = client.get("/panel", follow_redirects=True)
        assert "Hola, @" in r.text and "Perfil de empresa" in r.text and 'href="/panel/empresa"' in r.text
        assert "Omitir por ahora" in r.text and "Tu plan Asistente virtual" not in r.text
    assert client.post("/panel/bienvenida", data={"accion": "omitir"}).status_code == 403  # sin Origin
    # Lo omite: paso 2, una sola vez (Básico: un solo camino y seguir explorando).
    r = client.post("/panel/bienvenida", headers=ORIGEN, data={"accion": "omitir"})
    assert "¿Por dónde seguimos?" in r.text and "Tu plan Asistente virtual" in r.text and "Tu marca" not in r.text
    assert "te espera en Configuración" in r.text and "Seguir explorando" in r.text
    assert "Te falta llenar tu <b>Perfil de empresa</b>" in client.get("/panel/asistente").text
    lista["empresa"] = True
    assert "Te falta llenar tu" not in client.get("/panel/asistente").text
    # Después (en pausa y sin mensajes): a configurar su asistente.
    r = client.get("/panel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/panel/asistente"
    client.post("/panel/estado", headers=ORIGEN, data={"activo": "1"})
    assert client.get("/panel", follow_redirects=False).headers["location"] == "/panel/asistente"  # sin mensajes
    client.portal.call(c.store.add_incoming, "ana", "m1", "hola")
    assert client.get("/panel", follow_redirects=False).headers["location"] == "/panel/resultados"
    html = client.get("/panel").text
    assert html.index(" title=\"Resultados\"") < html.index(" title=\"Conversaciones\"")  # primero en el menú
    assert 'href="/panel/asistente" title="Conversaciones"' in html


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
    html = client.get("/panel/resultados?ver=conversaciones").text
    assert 'href="/panel/contenido" title="Calendario"' in html
    assert "De comentario a mensaje" in html and "PRECIO · 1" in html and "Spam ocultado" in html
    assert "1 publicada" not in html  # las publicaciones van en Instagram
    html = client.get("/panel/resultados").text
    assert "1 publicada <small>· 1 programada" in html and "https://www.instagram.com/p/X/" in html
    assert "Instagram aún no da sus números" in html
    assert "De comentario a mensaje" not in html
