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
        if "," in metric and self.chica and "breakdown" not in params:
            raise RuntimeError("Instagram respondió 400: profile_links_taps no disponible")
        if metric == "profile_links_taps" and self.chica:
            raise RuntimeError("no")
        if breakdown := params.get("breakdown"):  # los desgloses, con los nombres de Instagram
            if breakdown == "follower_type" and self.chica:
                raise RuntimeError("Instagram respondió 400: breakdown no disponible")
            valores = {"media_product_type": [("POST", 4), ("CAROUSEL_CONTAINER", 2), ("REEL", 3), ("STORY", 1)],
                       "follower_type": [("FOLLOWER", 6), ("NON_FOLLOWER", 4)],
                       "follow_type": [("FOLLOWER", 7), ("NON_FOLLOWER", 3)],
                       "contact_button_type": [("TEXT", 5), ("CALL", 2)]}[breakdown]
            return [{"name": m, "total_value": {"breakdowns": [{"results": [
                {"dimension_values": [d], "value": v} for d, v in valores]}]}} for m in metric.split(",")]
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
    # 1 de la cuenta + 7 días × (insights + seguidores ganados + 4 desgloses) + 3 del público + 1 de a qué horas
    # está conectado, y luego el primer lote del historial: LOTE_RELLENO días más hacia atrás.
    por_dia = 6
    assert ig.llamadas.count("me") == 1 and ig.llamadas.count("follows_and_unfollows") == 7 + metricas.LOTE_RELLENO
    assert ig.llamadas.count("follower_demographics") == 3 and ig.llamadas.count("online_followers") == 1
    assert len(ig.llamadas) == 1 + 7 * por_dia + 4 + metricas.LOTE_RELLENO * por_dia
    assert client.portal.call(c.store.get, metricas.CLAVE_RELLENO) == "2026-10-16"  # el siguiente lote sigue aquí
    ig.llamadas.clear()
    client.portal.call(metricas.tick, c)
    assert len(ig.llamadas) == metricas.LOTE_RELLENO * por_dia  # el día ya se guardó: solo sigue el historial
    ig.llamadas.clear()
    # Las horas se guardan en la zona de la cuenta, promediadas por día (UTC-6 en octubre para América/Mexico_City).
    horas = json.loads(client.portal.call(c.store.get, metricas.CLAVE_HORAS))["horas"]
    corrimiento = int(datetime.now(zona).utcoffset().total_seconds() // 3600)
    assert horas == {str((2 + corrimiento) % 24): 15.0, str((3 + corrimiento) % 24): 20.0, str((14 + corrimiento) % 24): 5.0}

    dias = client.portal.call(c.store.metricas, "2026-10-01")
    assert dias["2026-10-28"] == {"seguidores": 120, "siguiendo": 80, "publicaciones": 30}
    assert dias["2026-10-27"]["alcance"] == 10 and dias["2026-10-27"]["nuevos"] == 3
    assert dias["2026-10-27"]["perdidos"] == 1 and dias["2026-10-27"]["v"] == metricas.VERSION_DIA
    # Los desgloses, con nuestros nombres: carruseles y posts juntos, seguidores / otros, botones tal cual.
    assert dias["2026-10-27"]["vistas_tipo"] == {"posts": 6, "reels": 3, "historias": 1}
    assert dias["2026-10-27"]["vistas_seg"] == {"seguidores": 6, "otros": 4}
    assert dias["2026-10-27"]["alcance_seg"] == {"seguidores": 7, "otros": 3}
    assert dias["2026-10-27"]["clics_tipo"] == {"TEXT": 5, "CALL": 2}
    assert "2026-10-10" not in dias and "2026-10-11" in dias  # dos lotes del historial: hasta 17 días atrás

    # Al día siguiente: solo los dos últimos días (Instagram aún los ajusta), sin público (es semanal), y sigue el historial.
    ahora["t"] += timedelta(days=1)
    client.portal.call(metricas.tick, c)
    assert ig.llamadas.count("follows_and_unfollows") == 2 + metricas.LOTE_RELLENO and "follower_demographics" not in ig.llamadas
    assert len(ig.llamadas) == 1 + (2 + metricas.LOTE_RELLENO) * por_dia


def test_historial_termina_a_los_90_dias(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    ig = InstagramConInsights()
    monkeypatch.setattr(c, "instagram", ig)
    monkeypatch.setattr(metricas, "LOTE_RELLENO", 40)
    hoy = date(2026, 10, 28)
    # Sin primer guardado no se rellena nada; con él, va hacia atrás por lotes hasta DIAS_HISTORIAL y marca "listo".
    client.portal.call(c.store.set, metricas.CLAVE_INTENTO, hoy.isoformat())
    assert client.portal.call(metricas.rellenar, c, hoy) == 40
    assert client.portal.call(c.store.get, metricas.CLAVE_RELLENO) == (hoy - timedelta(days=40)).isoformat()
    assert client.portal.call(metricas.rellenar, c, hoy) == 40
    assert client.portal.call(metricas.rellenar, c, hoy) == 10 and client.portal.call(c.store.get, metricas.CLAVE_RELLENO) == "listo"
    assert client.portal.call(metricas.rellenar, c, hoy) == 0
    dias = client.portal.call(c.store.metricas, "2026-01-01")
    assert len(dias) == 90 and min(dias) == (hoy - timedelta(days=90)).isoformat()
    # Un día guardado antes sin desgloses (versión vieja) se vuelve a pedir; uno completo, no.
    assert not metricas.completo({"alcance": 5}) and metricas.completo({"alcance": 5, "v": metricas.VERSION_DIA})


def test_cuenta_chica_se_salta_lo_que_no_hay(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    ig = InstagramConInsights(chica=True)
    monkeypatch.setattr(c, "instagram", ig)
    valores = client.portal.call(metricas.insights_del_dia, c, date(2026, 10, 27))
    assert valores["alcance"] == 10 and "clics" not in valores and "nuevos" not in valores
    assert "vistas_seg" not in valores and valores["alcance_seg"] == {"seguidores": 7, "otros": 3}
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
                "likes": 5, "comentarios": 2, "compartidos": 1, "guardados": 1, "clics": 1,
                "vistas_seg": {"seguidores": 60, "otros": 40}, "vistas_tipo": {"posts": 70, "reels": 30},
                "alcance_seg": {"seguidores": 30, "otros": 10 + i}, "clics_tipo": {"TEXT": 1}})
        for i in range(8, 15):  # el periodo anterior, con la mitad de vistas
            await c.store.guardar_metricas((hoy - timedelta(days=i)).isoformat(), {"alcance": 20, "vistas": 50, "nuevos": 1})
        await c.store.set(metricas.CLAVE_PUBLICO, json.dumps({
            "semana": "x", "edad": {"18-24": 40, "25-34": 60}, "genero": {"F": 70, "M": 30},
            "ciudad": {"Guadalajara, Jalisco": 50}}))
        await c.store.set(metricas.CLAVE_HORAS, json.dumps({"semana": "x", "horas": {"9": 4, "20": 12, "21": 9}}))

    client.portal.call(llenar)
    monkeypatch.setattr("igbot.resultados._cache", {})
    html = client.get("/panel/resultados").text
    assert ">121<" in html and "+10%</span> desde que empezó el periodo" in html  # seguidores vs. el inicio del periodo
    assert "+14" in html and "7 dejaron de seguirte · neto +7" in html and "+100%</span> vs. periodo anterior" in html
    assert ">700 <small>· 40% de personas que no te siguen</small>" in html and ">63<" in html  # vistas e interacciones (9 × 7)
    assert "WhatsApp o SMS" in html and "Mujeres" in html and "Guadalajara<b>50" in html
    assert "Cuándo está tu público" in html and "a las 20:00, 21:00 y 09:00" in html and 'class="b mejor"' in html
    assert "<span><b>60</b></span>" in html and "<title>" not in html.split("<main")[1]
    # Vistas apiladas con el periodo anterior punteado, las pastillas de formato y Tu público plegado con su resumen.
    assert 'class="seg"' in html and 'class="otros"' in html and 'class="antes"' in html
    assert 'formato=posts">Posts</a>' in html and 'formato=reels">Reels</a>' in html and "Historias</a>" not in html
    assert '<details class="met-c ancho met-pleg">' in html and "mujeres de 25-34 años en Guadalajara" in html
    assert 'tabindex="0"' in html  # los valores salen también al tocar
    # Filtrado por formato: solo reels, sin desglose por seguidores.
    html = client.get("/panel/resultados?formato=reels").text
    assert 'class="on" href="/panel/resultados?formato=reels"' in html or 'formato=reels" class=on' in html
    assert ">210 </b>" in html and 'class="otros"' not in html


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

    async def insights(c, media_id, publicado=None, tipo="post"):
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


def test_granularidad_semana_y_mes(cliente, monkeypatch):  # noqa: F811
    """Por día / semana / mes se elige una vez en la barra y manda en todas las gráficas: las series van en
    bloques completos desde el final, y el periodo anterior se corre semanas (o meses) enteros para comparar
    los mismos días de la semana."""
    client, c = cliente
    assert metricas.opciones_cada(7) == ["dia"] and metricas.opciones_cada(30) == ["dia", "semana"]
    assert metricas.opciones_cada(90) == ["dia", "semana", "mes"]
    assert metricas.cada_por_defecto(7) == "dia" and metricas.cada_por_defecto(30) == "semana"
    assert metricas.salto(30, "dia") == 30 and metricas.salto(30, "semana") == 35 and metricas.salto(45, "mes") == 60
    assert metricas.leer_cada("mes", 30) == "semana" and metricas.leer_cada("dia", 30) == "dia"
    assert metricas.sumar_dias([{"seguidores": 10, "alcance": 20, "vistas": 5, "vistas_seg": {"seguidores": 3}},
                                {"alcance": 40, "vistas": 7, "vistas_seg": {"seguidores": 4, "otros": 1}, "v": 2}]) == {
        "seguidores": 10, "alcance": 30, "vistas": 12, "vistas_seg": {"seguidores": 7, "otros": 1}}

    hoy = datetime.now(ZoneInfo(c.timezone)).date()

    async def llenar():
        for i in range(1, 15):  # dos semanas completas
            await c.store.guardar_metricas((hoy - timedelta(days=i)).isoformat(),
                                           {"alcance": 30, "vistas": 100, "nuevos": 2, "seguidores": 200 - i})
        for i in range(15, 70):  # antes, la mitad
            await c.store.guardar_metricas((hoy - timedelta(days=i)).isoformat(), {"alcance": 10, "vistas": 50, "nuevos": 1})
        await c.store.add_incoming("ana", "m1", "hola")
    client.portal.call(llenar)
    d = client.portal.call(metricas.numeros, c, 14, None, "semana")
    assert d["cada"] == "semana" and len(d["fechas"]) == 2 and d["fechas"][0] == hoy - timedelta(days=14)
    assert [x["vistas"] for x in d["serie"]] == [700, 700] and [x["vistas"] for x in d["antes"]] == [350, 350]
    assert d["alcance_dia"] == [30, 30] and d["serie"][1]["seguidores"] == 199 and len(d["dias_fechas"]) == 14
    assert d["vistas"] == 1400 and d["vistas_cambio"] == 100.0  # los totales siguen contando todos los días
    d = client.portal.call(metricas.numeros, c, 30, None, "semana")
    assert len(d["fechas"]) == 4 and d["fechas"][-1] == hoy - timedelta(days=7)  # los 2 días sueltos sobran
    assert [x["vistas"] for x in d["serie"]] == [350, 350, 700, 700]
    assert [x.get("vistas") for x in d["antes"]] == [350, 350, 350, 350]  # 35 días atrás: mismos días de la semana

    monkeypatch.setattr("igbot.resultados._cache", {})
    html = client.get("/panel/resultados?dias=30").text
    assert 'class="cal-sw modo res-cada">' in html and 'class="on">Por semana</a>' in html and "Por mes" not in html
    assert 'href="/panel/resultados?dias=30&cada=dia">Por día</a>' in html
    assert "Comparado con 5 semanas atrás, mismos días de la semana" in html
    assert 'aria-label="vistas por semana"' in html and "Pico la semana del" in html
    assert "Por semana" not in client.get("/panel/resultados?dias=7").text  # con 7 días solo hay por día
    html = client.get("/panel/resultados?dias=30&cada=dia").text
    assert 'class="on">Por día</a>' in html and "Comparado con los 30 días anteriores" in html
    assert 'aria-label="vistas por día"' in html and 'href="/panel/resultados?dias=90&cada=dia">90 días' in html
    html = client.get("/panel/resultados?dias=90").text
    assert 'cada=mes">Por mes</a>' in html and 'class="on">Por semana</a>' in html
    html = client.get("/panel/resultados?dias=90&cada=mes").text
    assert 'aria-label="vistas por mes"' in html and "Comparado con 3 meses atrás" in html
    # Conversaciones sigue la misma elección, y el PDF también; el CSV siempre va día por día.
    html = client.get("/panel/resultados?ver=conversaciones&dias=30").text
    assert 'aria-label="Mensajes recibidos por semana"' in html and 'href="/panel/resultados?dias=30&ver=conversaciones&cada=dia">Por día' in html
    r = client.get("/panel/resultados/descargar?dias=30&cada=dia&archivo=csv")
    assert len(r.content.decode("utf-8-sig").splitlines()) == 31
    r = client.get("/panel/resultados/descargar?dias=30")
    assert r.content.startswith(b"%PDF")
