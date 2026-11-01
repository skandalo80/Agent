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
        if metric == "online_followers":  # por hora UTC, un renglón por día
            if self.chica:
                raise RuntimeError("Instagram respondió 400: not enough followers")
            return [{"name": metric, "period": "lifetime", "values": [
                {"value": {"2": 10, "3": 30, "14": 4}, "end_time": "2026-10-27T07:00:00+0000"},
                {"value": {"2": 20, "3": 10, "14": 6}, "end_time": "2026-10-28T07:00:00+0000"}]}]
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
    # 1 de la cuenta + 7 días × (insights + seguidores ganados) + 3 del público + 1 de a qué horas está conectado.
    assert ig.llamadas.count("me") == 1 and ig.llamadas.count("follows_and_unfollows") == 7
    assert ig.llamadas.count("follower_demographics") == 3 and ig.llamadas.count("online_followers") == 1
    assert len(ig.llamadas) == 19
    client.portal.call(metricas.tick, c)
    assert len(ig.llamadas) == 19  # una vez al día
    # Las horas se guardan en la zona de la cuenta, promediadas por día (UTC-6 en octubre para América/Mexico_City).
    horas = json.loads(client.portal.call(c.store.get, metricas.CLAVE_HORAS))["horas"]
    corrimiento = int(datetime.now(zona).utcoffset().total_seconds() // 3600)
    assert horas == {str((2 + corrimiento) % 24): 15.0, str((3 + corrimiento) % 24): 20.0, str((14 + corrimiento) % 24): 5.0}

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
    assert client.portal.call(metricas.horas_activas, c) == {}


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
        await c.store.set(metricas.CLAVE_HORAS, json.dumps({"semana": "x", "horas": {"9": 4, "20": 12, "21": 9}}))

    client.portal.call(llenar)
    monkeypatch.setattr("igbot.resultados._cache", {})
    html = client.get("/panel/resultados").text
    assert ">121<" in html and "+10%" in html and "+21%" in html  # semana y mes
    assert "+14" in html and "7 dejaron de seguirte · neto +7" in html
    assert ">700<" in html and ">63<" in html  # vistas e interacciones (9 × 7)
    assert "7 clics al enlace" in html and "Mujeres" in html and "Guadalajara<b>50" in html
    assert "Cuándo está tu público" in html and "a las 20:00, 21:00 y 09:00" in html and 'class="b mejor"' in html
    assert "<span><b>60</b></span>" in html and "<title>" not in html.split("<main")[1]


def test_curva_no_se_sale():
    camino = metricas.suave([(0, 0), (10, 100), (20, 0), (30, 100)])
    numeros = [float(n) for parte in camino.replace("M", " ").replace("C", " ").split() for n in parte.split(",")]
    assert all(0 <= y <= 100 for y in numeros[1::2])
    assert "sin datos" in metricas.linea([None, None], [date(2026, 1, 1), date(2026, 1, 2)], "x")


def test_barra_del_periodo_y_descarga(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    hoy = datetime.now(ZoneInfo(c.timezone)).date()
    html = client.get("/panel/resultados?dias=30").text
    assert 'class="tarjeta cal res-barra"' in html and 'class="on">30 días' in html and "90 días" in html
    assert 'aria-label="Siguiente"' not in html  # en el periodo de hoy no hay ›
    assert "el primer guardado es mañana" in html
    atras = (hoy - timedelta(days=30)).isoformat()
    assert f'href="/panel/resultados?dias=30&fin={atras}"' in html

    client.portal.call(c.store.guardar_metricas, (hoy - timedelta(days=40)).isoformat(),
                       {"seguidores": 90, "alcance": 50, "nuevos": 4, "vistas": 70})
    client.portal.call(c.store.set, metricas.CLAVE_HORA, str(datetime.now(ZoneInfo(c.timezone)).timestamp()))
    html = client.get(f"/panel/resultados?dias=30&fin={atras}").text
    assert 'aria-label="Siguiente"' in html and "actualizado hoy a las" in html and ">90<" in html
    assert client.get("/panel/resultados?dias=7&fin=nada").status_code == 200

    r = client.get(f"/panel/resultados/descargar?dias=30&fin={atras}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert f"resultados-instagram-{(hoy - timedelta(days=59)).isoformat()}-al-{atras}.pdf" in r.headers["content-disposition"]

    r = client.get("/panel/resultados/descargar?dias=7&ver=conversaciones")
    assert r.status_code == 200 and "resultados-conversaciones-" in r.headers["content-disposition"]
    html = client.get("/panel/resultados?ver=conversaciones").text
    assert "Mensajes: se actualizan cada 10 minutos" in html and "Seguidores vs." not in html


def test_publicaciones_con_numeros(cliente, monkeypatch):  # noqa: F811
    import time

    from igbot import informe
    client, c = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    numeros = {"m1": {"reach": 200, "views": 400, "likes": 10, "comments": 2, "saved": 4, "shares": 4},
               "m2": {"reach": 100, "views": 150, "likes": 3, "comments": 0, "saved": 1, "shares": 0}}

    async def insights(c, media_id, publicado=None):
        return numeros.get(media_id)

    monkeypatch.setattr(informe, "numeros", insights)

    async def publicar():
        for n, (texto, media) in enumerate((("Post flojo", "m2"), ("Post bueno", "m1"), ("Post nuevo", ""))):
            post = await c.store.add_post("panel", [], texto, time.time() - 3600 * (n + 1))
            link = f"https://www.instagram.com/p/{n}/"
            await c.store.set_post(post, "publicada", permalink=link)
            if media:
                await c.store.set(f"media_de:{link}", media)

    client.portal.call(publicar)
    html = client.get("/panel/resultados").text
    assert "3 publicadas" in html and "Tu mejor post" in html
    assert html.index("Post bueno") < html.index("Post flojo") < html.index("Post nuevo")  # de mejor a peor
    assert "10%" in html and "4%" in html and "Interacción promedio: 7.0%" in html
    assert html.count("Instagram aún no da sus números") == 1


def test_estadisticas_de_cada_post_se_guardan_una_vez_al_dia(cliente, monkeypatch):  # noqa: F811
    """El guardado de las 6:00 refresca los posts de los últimos 30 días; después las páginas leen de la base."""
    import time

    from igbot import informe
    client, c = cliente
    ig = InstagramConInsights()
    pedidos = []

    async def media_insights(media_id):
        pedidos.append(media_id)
        return {"reach": 50, "likes": 5}
    ig.media_insights = media_insights
    monkeypatch.setattr(c, "instagram", ig)
    zona = ZoneInfo(c.timezone)
    monkeypatch.setattr(tools, "now", lambda: datetime(2026, 10, 28, 7, tzinfo=zona))
    client.portal.call(c.store.set, metricas.CLAVE_INTENTO, "")

    async def publicar():
        for n, dias in enumerate((5, 40)):  # uno de hace 5 días y otro de hace 40
            post = await c.store.add_post("panel", [], f"Post {n}", time.time() - dias * 86400)
            link = f"https://www.instagram.com/p/P{n}/"
            await c.store.set_post(post, "publicada", permalink=link)
            await c.store.set(f"media_de:{link}", f"m{n}")
    client.portal.call(publicar)

    client.portal.call(metricas.tick, c)
    assert pedidos == ["m0"]  # el de hace 40 días ya no cambia: no se refresca
    hace = time.time() - 5 * 86400
    assert client.portal.call(informe.numeros, c, "m0", hace) == {"reach": 50, "likes": 5} and pedidos == ["m0"]
    # El de hace 40 días nunca se pidió: se pide una vez y desde ahí sale de la base.
    for _ in range(2):
        assert client.portal.call(informe.numeros, c, "m1", time.time() - 40 * 86400) == {"reach": 50, "likes": 5}
    assert pedidos == ["m0", "m1"]
