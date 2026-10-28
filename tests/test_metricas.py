"""Métricas diarias de Instagram: qué se guarda, cuántas veces se pide y cómo se ve en Resultados."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from test_resultados import cliente  # noqa: F401, I001  (antes: pone el entorno de prueba)

from igbot import metricas, tools  # noqa: E402


class InstagramConInsights:
    """Instagram falso que contesta insights y anota cada llamada."""

    def __init__(self, chica=False):
        self.llamadas = []
        self.chica = chica  # menos de 100 seguidores: sin seguidores ganados ni público

    async def numeros_cuenta(self):
        self.llamadas.append("me")
        return {"followers_count": 120, "follows_count": 80, "media_count": 30}

    async def insights_cuenta(self, metric, **params):
        self.llamadas.append(metric)
        if metric == "follows_and_unfollows" or metric == "follower_demographics":
            if self.chica:
                raise RuntimeError("Instagram respondió 400: not enough followers")
            if metric == "follows_and_unfollows":
                return [{"name": metric, "total_value": {"breakdowns": [{"results": [
                    {"dimension_values": ["FOLLOWER"], "value": 3}, {"dimension_values": ["NON_FOLLOWER"], "value": 1}]}]}}]
            resultados = {"age": [(["18-24"], 40), (["25-34"], 60)], "gender": [(["F"], 70), (["M"], 30)],
                          "city": [(["Guadalajara, Jalisco"], 50), (["Zapopan, Jalisco"], 20)]}[params["breakdown"]]
            return [{"name": metric, "total_value": {"breakdowns": [{"results": [
                {"dimension_values": d, "value": v} for d, v in resultados]}]}}]
        if "," in metric and self.chica:
            raise RuntimeError("Instagram respondió 400: profile_links_taps no disponible")
        if metric == "profile_links_taps" and self.chica:
            raise RuntimeError("no")
        return [{"name": m, "total_value": {"value": 10}} for m in metric.split(",")]

    async def close(self):
        pass


def test_tick_guarda_una_vez_al_dia_y_rellena_la_semana(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    ig = InstagramConInsights()
    monkeypatch.setattr(c, "instagram", ig)
    zona = ZoneInfo(c.timezone)
    ahora = {"t": datetime(2026, 10, 28, 5, 30, tzinfo=zona)}
    monkeypatch.setattr(tools, "now", lambda: ahora["t"])
    client.portal.call(c.store.set, metricas.CLAVE_INTENTO, "")  # por si el bucle de fondo ya pasó

    client.portal.call(metricas.tick, c)
    assert ig.llamadas == []  # antes de las 6 no hace nada
    ahora["t"] = ahora["t"].replace(hour=7)
    client.portal.call(metricas.tick, c)
    # 1 de la cuenta + 7 días × (insights + seguidores ganados) + 3 del público.
    assert ig.llamadas.count("me") == 1 and ig.llamadas.count("follows_and_unfollows") == 7
    assert ig.llamadas.count("follower_demographics") == 3 and len(ig.llamadas) == 18
    client.portal.call(metricas.tick, c)
    assert len(ig.llamadas) == 18  # una vez al día

    dias = client.portal.call(c.store.metricas, "2026-10-01")
    assert dias["2026-10-28"] == {"seguidores": 120, "siguiendo": 80, "publicaciones": 30}
    assert dias["2026-10-27"]["alcance"] == 10 and dias["2026-10-27"]["nuevos"] == 3
    assert dias["2026-10-27"]["perdidos"] == 1 and "2026-10-20" not in dias and "2026-10-21" in dias

    # Al día siguiente: solo los dos últimos días (Instagram aún los ajusta) y sin público (es semanal).
    ig.llamadas.clear()
    ahora["t"] += timedelta(days=1)
    client.portal.call(metricas.tick, c)
    assert ig.llamadas.count("follows_and_unfollows") == 2 and "follower_demographics" not in ig.llamadas
    assert len(ig.llamadas) == 5


def test_cuenta_chica_se_salta_lo_que_no_hay(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    ig = InstagramConInsights(chica=True)
    monkeypatch.setattr(c, "instagram", ig)
    valores = client.portal.call(metricas.insights_del_dia, c, date(2026, 10, 27))
    assert valores["alcance"] == 10 and "clics" not in valores and "nuevos" not in valores
    assert client.portal.call(metricas.publico, c) == {}


def test_resultados_muestra_tarjetas(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    assert "Empezamos a guardar tus números de Instagram" in client.get("/panel/resultados").text

    hoy = datetime.now(ZoneInfo(c.timezone)).date()

    async def llenar():
        await c.store.guardar_metricas((hoy - timedelta(days=30)).isoformat(), {"seguidores": 100})
        await c.store.guardar_metricas((hoy - timedelta(days=7)).isoformat(), {"seguidores": 110})
        await c.store.guardar_metricas(hoy.isoformat(), {"seguidores": 121})
        for i in range(1, 8):
            await c.store.guardar_metricas((hoy - timedelta(days=i)).isoformat(), {
                "alcance": 40 + i, "vistas": 100, "nuevos": 2, "perdidos": 1,
                "likes": 5, "comentarios": 2, "compartidos": 1, "guardados": 1, "clics": 1})
        await c.store.set(metricas.CLAVE_PUBLICO, json.dumps({
            "semana": "x", "edad": {"18-24": 40, "25-34": 60}, "genero": {"F": 70, "M": 30},
            "ciudad": {"Guadalajara, Jalisco": 50}}))

    client.portal.call(llenar)
    monkeypatch.setattr("igbot.resultados._cache", {})
    html = client.get("/panel/resultados").text
    assert ">121<" in html and "+10%" in html and "+21%" in html  # semana y mes
    assert "+14" in html and "7 dejaron de seguirte · neto +7" in html
    assert ">700<" in html and ">63<" in html  # vistas e interacciones (9 × 7)
    assert "7 clics al enlace" in html and "Mujeres" in html and "Guadalajara<b>50" in html
    assert "25-34 años: 60" in html


def test_curva_no_se_sale():
    camino = metricas.suave([(0, 0), (10, 100), (20, 0), (30, 100)])
    numeros = [float(n) for parte in camino.replace("M", " ").replace("C", " ").split() for n in parte.split(",")]
    assert all(0 <= y <= 100 for y in numeros[1::2])
    assert "sin datos" in metricas.linea([None, None], [date(2026, 1, 1), date(2026, 1, 2)], "x")
