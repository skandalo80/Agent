"""Resultados > Ventas por post: de qué post o historia llegó cada chat y cuánto vendió cada publicación."""
import time

from igbot import config, resultados
from test_resultados import cliente  # noqa: F401  (fixture)

DIA = config.DIA


async def origenes(store):
    cur = await store.db.execute("SELECT user_id, tipo, media_id, detalle FROM origenes_chat ORDER BY id")
    return await cur.fetchall()


async def mover(store, user_id, media_id, cuando):
    await store.db.execute("UPDATE origenes_chat SET created_at = ? WHERE user_id = ? AND media_id = ?",
                           (cuando, user_id, media_id))
    await store.db.commit()


def test_el_chat_guarda_de_que_post_llego_y_la_venta_va_a_la_ultima(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    s = c.store
    ahora = time.time()

    async def armar():
        # Comentario con palabra clave y DM; otro contestado por la tabla (tipo); otro sin DM (no cuenta).
        await s.add_comment("c1", "ana", "post1", "LA QUIERO")
        await s.comment_result("c1", "LA QUIERO; PRECIO", True, False)
        await s.add_comment("c2", "beto", "post1", "¿precio?")
        await s.comment_result("c2", "tipo: pregunta", True, True)
        await s.add_comment("c3", "carla", "post2", "qué bonito")
        await s.comment_result("c3", "tipo: elogio", False, True)
        await s.guardar_origen("beto", "historia", "hist9")
        assert await origenes(s) == [("ana", "comentario", "post1", "LA QUIERO; PRECIO"),
                                     ("beto", "comentario", "post1", ""), ("beto", "historia", "hist9", "")]
        await mover(s, "ana", "post1", ahora - 3 * DIA)
        await mover(s, "beto", "post1", ahora - 5 * DIA)
        await mover(s, "beto", "hist9", ahora - 2 * DIA)  # la última que tocó beto antes de comprar
        await s.add_venta("shopify", "o1", "ana", "", 500, "MXN", {}, ahora - DIA)
        await s.add_venta("stripe", "p1", "beto", "", 300, "MXN", {}, ahora - DIA)
        await s.add_venta("manual", "m1", "carla", "", 100, "MXN", {}, ahora - DIA)  # su chat no vino de un post
        await s.add_venta("shopify", "o2", "ana", "", 200, "MXN", {}, ahora - 4 * DIA)  # antes de comentar
        await s.add_venta("shopify", "o3", "", "ad1", 900, "MXN", {}, ahora - DIA)  # de anuncio, sin chat
        filas, todas = await s.ventas_por_publicacion(ahora - 7 * DIA, ahora, 30 * DIA)
        assert sorted((f["media_id"], f["tipo"], f["monto"]) for f in filas) == [
            ("hist9", "historia", 300), ("post1", "comentario", 500)]
        assert todas == {"ventas": 4, "monto": 1100.0, "moneda": "MXN"}  # las de chats, vengan o no de un post
        # Fuera de la ventana: lo que tocaron fue más de medio día antes de la venta.
        filas, _ = await s.ventas_por_publicacion(ahora - 7 * DIA, ahora, DIA / 2)
        assert [f["media_id"] for f in filas] == []
        # Volver a comentar el mismo post actualiza la hora, no suma otra fila.
        await s.add_comment("c4", "ana", "post1", "LA QUIERO")
        await s.comment_result("c4", "LA QUIERO", True, False)
        assert len(await origenes(s)) == 3
        # La retención también los borra.
        await mover(s, "beto", "post1", ahora - 400 * DIA)
        assert (await s.purgar(365, ahora))["origenes_chat"] == 1

    correr(armar())


def test_camino():
    assert resultados.camino("historia", "") == "respuesta a tu historia"
    assert resultados.camino("comentario", "LA QUIERO; PRECIO") == "comentario «LA QUIERO» → DM"
    assert resultados.camino("comentario", "<b>") == "comentario «&lt;b&gt;» → DM"
    assert resultados.camino("comentario", "") == "comentario → DM"


def test_pestana_ventas_por_post(cliente, monkeypatch):  # noqa: F811
    client, c = cliente
    r = client.get("/panel/resultados?ver=ventas")
    assert 'class="on">Ventas por post' in r.text and "Aún no hay ventas que vengan de un post" in r.text
    assert "Descargar" not in r.text.split("<main>")[1]
    pedidos = []

    async def media_basico(media_id):
        pedidos.append(media_id)
        if media_id == "hist9":
            raise RuntimeError("las historias se van a las 24 h")
        return {"caption": "Bolsa tote\nHecha a mano", "media_url": "https://cdn/x.jpg",
                "permalink": "https://www.instagram.com/p/Y/", "timestamp": "2026-10-12T18:00:00+0000",
                "media_type": "IMAGE"}

    monkeypatch.setattr(c.instagram, "media_basico", media_basico, raising=False)
    ahora = time.time()

    async def armar():
        s = c.store
        for i, quien in enumerate(("ana", "beto", "dani")):
            await s.add_comment(f"c{i}", quien, "post1", "LA QUIERO")
            await s.comment_result(f"c{i}", "LA QUIERO", True, False)
        await s.guardar_origen("eva", "historia", "hist9")
        hist = await s.add_post("panel", [], "Buen Fin\nTodo al 20 %", 0, "historia")
        await s.set_post(hist, "publicada", "", "", None, "hist9")
        await s.guardar_insights_post("post1", {"reach": 2140}, ahora)
        await s.add_venta("shopify", "o1", "ana", "", 1500, "MXN", {}, ahora + 1)
        await s.add_venta("stripe", "p1", "beto", "", 1000, "MXN", {}, ahora + 1)
        await s.add_venta("manual", "m1", "eva", "", 800, "MXN", {}, ahora + 1)
        await s.add_venta("manual", "m2", "fer", "", 100, "MXN", {}, ahora + 1)  # chat sin post

    client.portal.call(armar)
    html = client.get("/panel/resultados?ver=ventas").text.split("<main>")[1]
    assert "$3,300 MXN" in html and "Vendido desde tu contenido" in html
    assert ">3</b><small>de 2 publicaciones" in html and "75%" in html and "3 de 4" in html
    assert "Post · Bolsa tote" in html and "Hecha a mano" not in html and "2,140 de alcance" in html
    assert "$2,500 MXN</b><small>2 ventas" in html and "comentario «LA QUIERO» → DM (2)" in html
    assert "Historia · Buen Fin" in html and "respuesta a tu historia (1)" in html and "1 venta<" in html
    assert html.index("Bolsa tote") < html.index("Buen Fin")  # la que más vendió, primero
    assert 'style="width:100%"' in html and 'style="width:32%"' in html
    assert "https://www.instagram.com/p/Y/" in html
    assert sorted(pedidos) == ["hist9", "post1"]
