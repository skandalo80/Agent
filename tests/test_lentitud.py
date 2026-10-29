"""Registro de peticiones lentas y compresión de las páginas."""
import asyncio
import gzip
import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.testclient import TestClient

from igbot import comprimir, lentitud


def app_de_prueba() -> FastAPI:
    app = FastAPI()

    @app.get("/pagina")
    def pagina():
        return HTMLResponse("<p>hola</p>" * 500)

    @app.get("/corta")
    def corta():
        return HTMLResponse("<p>hola</p>")

    @app.get("/partes")
    def partes():
        async def trozos():
            yield "<p>uno</p>" * 200
            yield "<p>dos</p>"
        return StreamingResponse(trozos(), media_type="text/html")

    @app.get("/lenta")
    async def lenta():
        lentitud.sumar("ia", 0.3)
        await asyncio.sleep(0.05)
        return HTMLResponse("ok")

    app.add_middleware(comprimir.Comprimir)
    app.add_middleware(lentitud.Medir)
    return app


def test_comprime_solo_respuestas_completas():
    client = TestClient(app_de_prueba())
    r = client.get("/pagina", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.text == "<p>hola</p>" * 500
    assert int(r.headers["content-length"]) < 200
    # Sin gzip, cortas o por partes (el chat de prueba): tal cual.
    assert "content-encoding" not in client.get("/pagina", headers={"Accept-Encoding": "identity"}).headers
    assert "content-encoding" not in client.get("/corta", headers={"Accept-Encoding": "gzip"}).headers
    r = client.get("/partes", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers and r.text.endswith("<p>dos</p>")


def test_anota_las_lentas(monkeypatch, caplog):
    monkeypatch.setattr(lentitud, "LENTO_SEG", 0.01)
    with caplog.at_level(logging.WARNING, logger="igbot.lento"):
        TestClient(app_de_prueba()).get("/lenta")
    linea = next(r.getMessage() for r in caplog.records if "LENTO" in r.getMessage())
    assert "GET /lenta" in linea and "ia 1× 0.30s" in linea


def test_linea_y_hosts():
    assert lentitud.tipo_de("graph.instagram.com") == "ig" and lentitud.tipo_de("api.openai.com") == "ia"
    assert lentitud.tipo_de("myshop.myshopify.com") == "http"
    assert lentitud.linea("GET", "/panel", 3.0, {"db": [12, 0.5], "ig": [1, 2.0]}) == (
        "LENTO 3.00s GET /panel · db 12× 0.50s · ig 1× 2.00s · resto 0.50s")


def test_mide_sqlite(tmp_path):
    import aiosqlite
    lentitud.instalar()

    async def correr():
        gasto: dict = {}
        lentitud.GASTO.set(gasto)
        async with aiosqlite.connect(tmp_path / "x.db") as db:
            await db.execute("SELECT 1")
        return gasto

    assert asyncio.run(correr())["db"][0] >= 1
