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
    # Después: siempre a Inicio, con lo que le falta, lo de hoy y quién lo espera.
    r = client.get("/panel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/panel/inicio"
    client.post("/panel/estado", headers=ORIGEN, data={"activo": "1"})
    client.portal.call(c.store.add_incoming, "ana", "m1", "hola")
    assert client.get("/panel", follow_redirects=False).headers["location"] == "/panel/inicio"
    html = client.get("/panel").text
    assert html.index(' title="Inicio"') < html.index(' title="Resultados"') < html.index(' title="Conversaciones"')
    assert 'class="lat-item on" href="/panel/inicio" title="Inicio"' in html
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
    assert "De comentario a mensaje" not in html  # los comentarios tienen su pestaña
    assert "1 publicada" not in html  # las publicaciones van en Instagram
    assert '>Comentarios</a>' in html
    html = client.get("/panel/resultados").text
    assert "1 publicada <small>· 1 programada" in html and "https://www.instagram.com/p/X/" in html
    assert "Instagram aún no da sus números" in html
    assert "De comentario a mensaje" not in html


def test_pestana_comentarios(cliente, monkeypatch):
    client, c = cliente
    pedidos = []

    async def media_basico(media_id):
        pedidos.append(media_id)
        return {"caption": "Pan de muerto", "media_url": "https://cdn/x.jpg", "permalink": "https://www.instagram.com/p/Y/",
                "timestamp": "2026-10-30T18:00:00+0000", "media_type": "IMAGE"}

    monkeypatch.setattr(c.instagram, "media_basico", media_basico, raising=False)
    # Básico: no hay pestaña (no atiende comentarios) y la dirección lleva a Instagram.
    assert ">Comentarios</a>" not in client.get("/panel/resultados").text
    assert 'class="on">Instagram' in client.get("/panel/resultados?ver=comentarios").text
    monkeypatch.setattr(c, "imagenes_mes", 4)
    assert "Aún no hay comentarios" in client.get("/panel/resultados?ver=comentarios").text
    monkeypatch.setattr(resultados, "_cache", {})

    async def actividad():
        for i, (tipo, dm, pub) in enumerate([("tipo: elogio", False, True), ("tipo: elogio", False, False),
                                             ("tipo: pregunta", True, True), ("PRECIO", True, False)]):
            await c.store.add_comment(f"c{i}", f"u{i}", "m1" if i < 3 else "m2", "hola")
            await c.store.comment_result(f"c{i}", tipo, dm, pub)
        await c.store.add_comment("s", "spam", "m1", "gana dinero")
        await c.store.comment_result("s", resultados.SPAM, False, False)
        await c.store.add_comment("mencion:1", "mencion", "m9", "")
        await c.store.comment_result("mencion:1", "mención", False, True)

    client.portal.call(actividad)
    html = client.get("/panel/resultados?ver=comentarios").text
    assert 'class="on">Comentarios' in html and "Descargar" not in html
    assert "75%" in html and "3 de 4 por tu asistente" in html  # el spam y la mención no cuentan
    assert "Spam ocultado" in html and "Menciones" in html
    assert "Elogio" in html and "Pregunta" in html and "Palabra clave" in html and "1 contestado" in html
    assert "Pan de muerto" in html and "https://www.instagram.com/p/Y/" in html and "3</b><small>comentarios" in html
    assert "PRECIO · 1" in html and "Comentarios recibidos" in html
    assert sorted(pedidos) == ["m1", "m2"]  # la mención no es un post tuyo
    monkeypatch.setattr(resultados, "_cache", {})
    client.get("/panel/resultados?ver=comentarios")
    assert len(pedidos) == 2  # la foto y el texto quedan guardados


def test_pagina_de_inicio(cliente, monkeypatch):
    client, c = cliente

    async def username(user_id):
        return f"@{user_id}"

    monkeypatch.setattr(c.instagram, "username", username, raising=False)
    assert client.get("/panel/inicio", follow_redirects=False).status_code == 200
    html = client.get("/panel/inicio").text.split("<main>")[1]
    # Básico: sin publicaciones, sin Crear campaña y solo el uso de mensajes.
    assert "Te falta" in html and "Hola" in html and "Mensajes que contestó tu asistente" in html
    assert "Nadie te espera" in html and "Próximas publicaciones" not in html and "Crear campaña" not in html
    assert "Tu plan · Asistente virtual" in html and ">Imágenes<" not in html
    assert "Personas que te escribieron" in html  # sin ventas todavía

    async def preparar():
        await c.store.add_incoming("beto", "m1", "quiero hablar con alguien")
        await c.store.pause("beto", time.time() + 3600)
        await c.store.add_event("beto", "humano", {"razon": "queja"})
        await c.store.add_venta("shopify", "1", "beto", "", 120, "MXN", {}, time.time())
        post = await c.store.add_post("panel", [], "Abrimos el sábado\nmás texto", time.time() + 86400)
        await c.store.set_post(post, "programada")

    client.portal.call(preparar)
    html = client.get("/panel/inicio").text.split("<main>")[1]
    assert "quiero hablar con alguien" in html and ">Queja<" in html and "Nadie te espera" not in html
    assert "$120 MXN" in html and "1 pedido" in html
    assert "Abrimos el sábado" not in html  # Básico no ve publicaciones
    monkeypatch.setattr(c, "imagenes_mes", 40)
    monkeypatch.setattr(c, "posts_mes", 12)
    html = client.get("/panel/inicio").text.split("<main>")[1]
    assert "Próximas publicaciones" in html and "Mañana" in html and "Abrimos el sábado" in html
    assert "Crear campaña" in html and ">Imágenes<" in html and ">Posts<" in html
    # Sin sesión, a entrar.
    client.cookies.clear()
    assert client.get("/panel/inicio", follow_redirects=False).headers["location"] == "/panel"


def test_rango_personalizado_y_descargas(cliente, monkeypatch):
    """?desde= y ?fin= dan un rango a la medida (la pastilla queda marcada con las fechas); el menú de Descargar
    tiene PDF y CSV del periodo y, en Instagram, por publicación."""
    import json

    from datetime import timedelta

    from igbot import informe, metricas
    client, c = cliente
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    html = client.get("/panel/resultados").text
    assert '<details class="res-pers"><summary>Personalizado</summary>' in html
    assert html.index("Personalizado") < html.index(">7 días<")  # a la izquierda de 7 días
    assert "PDF del periodo" in html and "CSV día por día" in html and "CSV por publicación" in html
    assert 'archivo=csv">CSV día por día' in html and 'que=posts&archivo=csv">CSV por publicación' in html
    # Conversaciones: del periodo nada más; Comentarios y Anuncios no descargan.
    html = client.get("/panel/resultados?ver=conversaciones").text
    assert "CSV día por día" in html and "por publicación" not in html
    assert "Descargar" not in client.get("/panel/resultados?ver=anuncios").text.split("<main>")[1]

    desde, fin = hoy - timedelta(days=11), hoy - timedelta(days=2)
    html = client.get(f"/panel/resultados?desde={desde.isoformat()}&fin={fin.isoformat()}").text
    assert f'<details class="res-pers on"><summary>{desde:%d/%m} – {fin:%d/%m}</summary>' in html
    assert f'value="{desde.isoformat()}"' in html and 'href="/panel/resultados?dias=10&fin=' in html  # ‹ › con 10 días
    assert 'class="on">7 días' not in html
    # Un desde después de fin, o inválido, cae en 7 días; más de un año se recorta.
    assert 'class="on">7 días' in client.get(f"/panel/resultados?desde={hoy.isoformat()}&fin={desde.isoformat()}").text
    assert 'class="on">7 días' in client.get("/panel/resultados?desde=ayer").text
    assert "?dias=366&" in client.get("/panel/resultados?desde=2020-01-01").text

    async def llenar():
        for i in range(1, 4):
            await c.store.guardar_metricas((hoy - timedelta(days=i)).isoformat(), {
                "seguidores": 100 + i, "alcance": 40 + i, "vistas": 100, "nuevos": 2, "perdidos": 1, "likes": 5,
                "interacciones": 7, "clics": 1, "vistas_seg": {"seguidores": 60, "otros": 40},
                "vistas_tipo": {"posts": 70, "reels": 30}, "alcance_seg": {"seguidores": 30, "otros": 10}})
        await c.store.add_incoming("ana", "m1", "hola")
        await c.store.set(metricas.CLAVE_PUBLICO, json.dumps({"semana": "x", "genero": {"F": 70, "M": 30}}))
    client.portal.call(llenar)
    monkeypatch.setattr(resultados, "_cache", {})

    r = client.get("/panel/resultados/descargar?dias=7&archivo=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f'resultados-instagram-{(hoy - timedelta(days=6)).isoformat()}-al-{hoy.isoformat()}.csv' in r.headers["content-disposition"]
    lineas = r.content.decode("utf-8-sig").splitlines()
    assert lineas[0].startswith("dia,seguidores,seguidores_nuevos,dejaron_de_seguir,alcance,alcance_seguidores,")
    assert lineas[0].endswith(",clics_perfil,mensajes_recibidos") and len(lineas) == 8  # 7 días completos (hasta ayer)
    ayer = [x for x in lineas if x.startswith((hoy - timedelta(days=1)).isoformat())][0]
    assert ayer == f"{(hoy - timedelta(days=1)).isoformat()},101,2,1,41,30,10,100,60,40,70,30,,,7,5,,,,1,0"  # el mensaje fue hoy
    r = client.get(f"/panel/resultados/descargar?desde={desde.isoformat()}&fin={fin.isoformat()}&archivo=csv")
    assert f"resultados-instagram-{desde.isoformat()}-al-{fin.isoformat()}.csv" in r.headers["content-disposition"]
    assert len(r.content.decode("utf-8-sig").splitlines()) == 11

    # Por publicación: CSV y PDF con los posts del periodo y sus números.
    numeros = {"m1": {"reach": 200, "views": 400, "likes": 10, "comments": 2, "saved": 4, "shares": 4}}

    async def insights(c, media_id, publicado=None):
        return numeros.get(media_id)
    monkeypatch.setattr(informe, "numeros", insights)
    monkeypatch.setattr(c, "imagenes_mes", 4)

    async def publicar():
        post = await c.store.add_post("panel", [], "Pan de muerto\nmás texto", time.time() - 3600)
        await c.store.set_post(post, "publicada", permalink="https://www.instagram.com/p/1/")
        await c.store.set("media_de:https://www.instagram.com/p/1/", "m1")
        post = await c.store.add_post("panel", [], "Sin números", time.time() - 7200)
        await c.store.set_post(post, "publicada", permalink="https://www.instagram.com/p/2/")
    client.portal.call(publicar)
    monkeypatch.setattr(resultados, "_cache", {})
    r = client.get("/panel/resultados/descargar?dias=7&que=posts&archivo=csv")
    lineas = r.content.decode("utf-8-sig").splitlines()
    assert "resultados-publicaciones-" in r.headers["content-disposition"]
    assert lineas[0] == "fecha,hora,tipo,texto,enlace,alcance,vistas,likes,comentarios,guardados,compartidos,interaccion_pct"
    assert len(lineas) == 3 and lineas[1].endswith(",post,Pan de muerto más texto,https://www.instagram.com/p/1/,200,400,10,2,4,4,10.0")
    assert lineas[2].endswith(",post,Sin números,https://www.instagram.com/p/2/,,,,,,,")
    r = client.get("/panel/resultados/descargar?dias=7&que=posts")
    assert r.content.startswith(b"%PDF") and "resultados-publicaciones-" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.pdf"')
    # En Conversaciones no hay "por post": cae en el periodo.
    r = client.get("/panel/resultados/descargar?dias=7&ver=conversaciones&que=posts")
    assert "resultados-conversaciones-" in r.headers["content-disposition"]
    # El PDF del periodo lleva la comparación y las vistas por formato.
    r = client.get("/panel/resultados/descargar?dias=7")
    assert r.content.startswith(b"%PDF")
