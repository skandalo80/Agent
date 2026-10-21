"""Contenido > Campañas: proponer el calendario sin imágenes, moverlo, editarlo, generar y aprobar."""
import json
import os
import re
from datetime import date, timedelta

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from test_contenido import ORIGEN, cliente  # noqa: E402

from igbot import campanas, contenido  # noqa: E402
from igbot.cuenta import usar  # noqa: E402

assert cliente  # el fixture de test_contenido, que usan estas pruebas

LUNES = date(2099, 1, 5)  # un lunes
assert LUNES.weekday() == 0


def claude_de_campana(llamadas):
    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system))
        if "Armas una mini campaña" in system:
            dias = re.findall(r"^- (\d{4}-\d{2}-\d{2})", prompt, re.M)
            tipos = ["marca", "dato", "producto"]
            return json.dumps({"posts": [
                {"dia": d, "tipo": tipos[i % 3], "hora": "09:00" if i else "nada", "titulo": f"Post {i + 1}",
                 "subtitulo": "", "caption": f"Texto {i + 1} #cafe", "idea": f"Foto {i + 1}"}
                for i, d in enumerate(dias)]})
        if "otra versión" in system:
            return json.dumps({"titulo": "Otra idea", "subtitulo": "", "caption": "Texto nuevo", "idea": "Foto nueva"})
        return "{}"
    return fake


def leer(client, c):
    return client.portal.call(campanas.leer, c)


def cuando_sale(client, c, dia, hora):
    def calcular():
        with usar(c):
            return campanas.momento(dia, hora)
    return calcular()


def test_campana_de_punta_a_punta(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    monkeypatch.setattr(campanas.config, "CONTENT_MODEL", "modelo")
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    r = client.get("/panel/contenido/campanas")
    assert 'href="/panel/contenido/campanas" class="on">Campañas' in r.text
    assert "Mezcla de contenido" in r.text and "No usa imágenes" in r.text

    # Paso 1: lunes, miércoles y viernes de una semana; tres tipos.
    datos = {"accion": "proponer", "nombre": "Otoño", "inicio": LUNES.isoformat(), "semanas": "1",
             "publicar": "lmv", "tema": "Latte <b>de calabaza</b>", "mezcla_marca": "1", "mezcla_dato": "1",
             "mezcla_producto": "1"}
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos)
    assert "Armando tu campaña" in r.text
    prompt, sistema = llamadas[-1]
    assert "&lt;b&gt;de calabaza" in prompt and "- marca (Conoce la marca): 1" in prompt
    camp = leer(client, c)
    lun, mie, vie = ((LUNES + timedelta(days=d)).isoformat() for d in (0, 2, 4))
    assert [(p["dia"], p["titulo"]) for p in camp["posts"]] == [(lun, "Post 1"), (mie, "Post 2"), (vie, "Post 3")]
    assert camp["posts"][0]["hora"] == campanas.HORA_INICIAL  # la hora que no sirve, a la de siempre
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)  # proponer no gasta imágenes

    r = client.get("/panel/contenido/campanas")
    assert "Otoño" in r.text and r.text.count("sin imagen") == 3 and 'draggable="true"' in r.text

    # Arrastrar el del lunes al miércoles: se intercambian.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": "1", "dia": mie})
    assert r.status_code == 204
    assert {p["titulo"]: p["dia"] for p in leer(client, c)["posts"]} == {"Post 1": mie, "Post 2": lun, "Post 3": vie}
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": "1", "dia": "2099-03-01"})
    assert r.status_code == 409
    assert client.post("/panel/contenido/campanas", data={"accion": "mover", "k": "1", "dia": vie}).status_code == 403

    # El panel del post: cambiar hora y texto; otra idea.
    r = client.get("/panel/contenido/campanas?post=3")
    assert 'id="detalle"' in r.text and "Texto 3 #cafe" in r.text and "Generar imagen · usa 1" in r.text
    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data={"accion": "guardar", "k": "3", "dia": vie, "hora": "10:30", "caption": "Texto editado"})
    p3 = campanas.buscar_post(leer(client, c), "3")
    assert (p3["hora"], p3["caption"]) == ("10:30", "Texto editado")
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "otra", "k": "2"})
    assert campanas.buscar_post(leer(client, c), "2")["titulo"] == "Otra idea"

    # Generar la imagen de uno: queda en borrador en su día y hora; luego aprobarlo.
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar", "k": "3"})
    p3 = campanas.buscar_post(leer(client, c), "3")
    post = client.portal.call(c.store.post, p3["post_id"])
    assert post["status"] == "borrador" and post["caption"] == "Texto editado"
    assert post["publish_at"] == cuando_sale(client, c, vie, "10:30")
    assert client.portal.call(contenido.pieza, c.store, post["id"])["campana"] == 3
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "aprobar", "k": "3"})
    assert "Listo: sale el" in r.text
    assert client.portal.call(c.store.post, post["id"])["status"] == "programada"

    # Moverlo después cambia también el post programado.
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": "3", "dia": lun})
    assert client.portal.call(c.store.post, post["id"])["publish_at"] == cuando_sale(client, c, lun, "10:30")

    # Las demás de un jalón y aprobar todas.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar_todas"})
    assert "Imagen 2 de 2" in r.text
    camp = leer(client, c)
    assert all(p["post_id"] for p in camp["posts"])
    assert client.portal.call(contenido.imagenes.usadas, c.store)[0] == 3
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "aprobar_todas"})
    assert "2 posts programados" in r.text

    # Proponer de nuevo deja los que tienen imagen; una campaña nueva empieza en blanco.
    assert "Nueva campaña" in client.get("/panel/contenido/campanas").text
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={**datos, "nueva": "1", "nombre": "Navidad"})
    camp = leer(client, c)
    assert camp["nombre"] == "Navidad" and not any(p["post_id"] for p in camp["posts"])


def test_cupo_extra_y_sin_plan(cliente, monkeypatch):
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    assert "solo incluye el asistente" in client.get("/panel/contenido/campanas").text
    monkeypatch.setattr(c, "imagenes_mes", 1)
    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data={"accion": "proponer", "nombre": "X", "inicio": LUNES.isoformat(), "semanas": "1",
                      "publicar": "lmv", "mezcla_marca": "3"})
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar_todas"})
    assert "Ya usaste las imágenes de tu plan" in r.text  # 3 no caben en 1: confirma antes
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "proponer", "nombre": "X",
                                                                          "mezcla_marca": "0"})
    assert "Elige al menos un post" in r.text


def test_dias_de_la_campana():
    camp = {"inicio": LUNES.isoformat(), "semanas": 2, "publicar": "lun-sab"}
    assert len(campanas.dias_de(camp)) == 12 and len(campanas.rango(camp)) == 14
    assert campanas.del_form({"semanas": "9", "inicio": "mal", "mezcla_dato": "99"})["semanas"] == 2
    assert campanas.del_form({"mezcla_dato": "99"})["mezcla"]["dato"] == 14
