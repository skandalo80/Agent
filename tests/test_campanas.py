"""Contenido > Campañas: explorar temas, formatos y horarios; proponer sin imágenes, moverlo, editarlo,
generar al final y aprobar."""
import io
import json
import os
import re
import time
from collections import Counter
from datetime import date, timedelta
from functools import partial

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from PIL import Image  # noqa: E402
from test_contenido import ORIGEN, cliente  # noqa: E402

from igbot import campanas, contenido, imagenes  # noqa: E402
from igbot.cuenta import usar  # noqa: E402

assert cliente  # el fixture de test_contenido, que usan estas pruebas

LUNES = date(2099, 1, 5)  # un lunes
assert LUNES.weekday() == 0
LINEA = re.compile(r"^- (\d{4}-\d{2}-\d{2}) .*\| formato: (.+)$", re.M)


def claude_de_campana(llamadas):
    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system))
        if "Armas una campaña" in system:
            return json.dumps({"posts": [
                {"dia": d, "titulo": f"Post {i + 1}", "subtitulo": "", "caption": f"Texto {i + 1} #cafe",
                 "idea": "" if formato == "Solo texto" else f"Foto {i + 1}",
                 "responde": "Horario de fin de semana" if i == 0 and "<lo_que_preguntan>" in prompt else ""}
                for i, (d, formato) in enumerate(LINEA.findall(prompt))]})
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


def datos_campana(**cambios):
    datos = {"accion": "proponer", "nombre": "Otoño", "inicio": LUNES.isoformat(), "semanas": "1",
             "publicar": "lmv", "tema": "Latte <b>de calabaza</b>", "t_dato": "1", "t_producto": "1",
             "f_titular": "1", "f_texto": "1", "f_foto": "1", "h_manana": "1", "h_noche": "1"}
    return {**datos, **cambios}


def test_campana_de_punta_a_punta(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    monkeypatch.setattr(campanas.config, "CONTENT_MODEL", "modelo")
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    r = client.get("/panel/contenido/campanas")
    assert 'href="/panel/contenido/campanas" class="on">Campañas' in r.text
    assert "Temas que quieres probar" in r.text and "No usa imágenes" in r.text
    assert 'value="repetir" disabled' in r.text and "Te faltan 12 posts con resultados" in r.text
    assert "Sube 2 fotos en Tu marca" in r.text and 'name="f_collage" value="1" disabled' in r.text
    assert 'option value="3" selected' in r.text  # 3 semanas de inicio

    # Con un solo tema no se puede: vuelve con lo que marcó.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(t_producto=""))
    assert "Elige de 2 a 6 temas." in r.text and 'name="t_dato" value="1" checked' in r.text
    assert not llamadas

    # Lunes, miércoles y viernes de una semana; dos temas, tres formatos, dos horarios.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana())
    assert "Armando tu campaña" in r.text
    prompt, sistema = llamadas[-1]
    assert "&lt;b&gt;de calabaza" in prompt and "tema: Dato curioso" in prompt
    camp = leer(client, c)
    lun, mie, vie = ((LUNES + timedelta(days=d)).isoformat() for d in (0, 2, 4))
    assert camp["modo"] == "explorar" and [p["dia"] for p in camp["posts"]] == [lun, mie, vie]
    assert {p["formato"] for p in camp["posts"]} == {"titular", "texto", "foto"}
    assert {p["tipo"] for p in camp["posts"]} == {"dato", "producto"}
    assert {p["hora"] for p in camp["posts"]} <= {"09:00", "20:00"}
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)  # proponer no gasta imágenes

    r = client.get("/panel/contenido/campanas")
    assert "Otoño" in r.text and r.text.count("sin imagen") == 3 and 'draggable="true"' in r.text
    assert "Se prueba:" in r.text and "3 formatos" in r.text and "Solo texto" in r.text
    assert "Generar las 3 que faltan · usa 2" in r.text  # el de solo texto no usa cupo

    # Arrastrar el del lunes al miércoles: se intercambian.
    k_lun, k_mie = camp["posts"][0]["k"], camp["posts"][1]["k"]
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": str(k_lun), "dia": mie})
    assert r.status_code == 204
    camp = leer(client, c)
    assert campanas.buscar_post(camp, k_lun)["dia"] == mie and campanas.buscar_post(camp, k_mie)["dia"] == lun
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": "1", "dia": "2099-03-01"})
    assert r.status_code == 409
    assert client.post("/panel/contenido/campanas", data={"accion": "mover", "k": "1", "dia": vie}).status_code == 403

    # El panel del post: cambiar hora, formato y texto; otra idea.
    r = client.get("/panel/contenido/campanas?post=3")
    assert 'id="detalle"' in r.text and "Texto 3 #cafe" in r.text and 'name="formato"' in r.text
    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data={"accion": "guardar", "k": "3", "dia": vie, "hora": "10:30", "formato": "texto",
                      "titulo": "El espresso tiene menos cafeína", "subtitulo": "Que un americano",
                      "caption": "Texto editado"})
    p3 = campanas.buscar_post(leer(client, c), "3")
    assert (p3["hora"], p3["formato"], p3["subtitulo"], p3["caption"]) == ("10:30", "texto", "Que un americano",
                                                                          "Texto editado")
    r = client.get("/panel/contenido/campanas?post=3")
    assert "Generar imagen · no usa imágenes" in r.text and 'name="idea"' not in r.text
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "otra", "k": "2"})
    assert campanas.buscar_post(leer(client, c), "2")["titulo"] == "Otra idea"

    # Solo texto se arma aquí: queda en borrador en su día y hora sin usar el cupo; luego aprobarlo.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar", "k": "3"})
    assert "Armando la pieza" in r.text
    p3 = campanas.buscar_post(leer(client, c), "3")
    post = client.portal.call(c.store.post, p3["post_id"])
    assert post["status"] == "borrador" and post["caption"] == "Texto editado"
    assert post["publish_at"] == cuando_sale(client, c, vie, "10:30")
    pieza = client.portal.call(contenido.pieza, c.store, post["id"])
    assert (pieza["campana"], pieza["formato"]) == (3, "texto")
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "aprobar", "k": "3"})
    assert "Listo: sale el" in r.text
    assert client.portal.call(c.store.post, post["id"])["status"] == "programada"

    # En el editor, una pieza fija guarda su texto y hora sin pedir título ni rotular de nuevo.
    r = client.get(f"/panel/contenido/post/{post['id']}")
    assert "viene de tu campaña" in r.text and "data-vista" not in r.text
    r = client.post(f"/panel/contenido/post/{post['id']}", headers=ORIGEN,
                    data={"accion": "guardar", "caption": "Desde el editor", "cuando": "2099-01-09T10:30"})
    assert "El título no puede quedar vacío" not in r.text
    assert client.portal.call(c.store.post, post["id"])["caption"] == "Desde el editor"

    # Moverlo después cambia también el post programado.
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "mover", "k": "3", "dia": lun})
    assert client.portal.call(c.store.post, post["id"])["publish_at"] == cuando_sale(client, c, lun, "10:30")

    # Las demás de un jalón y aprobar todas: solo las fotos usan cupo.
    pendientes = [p for p in leer(client, c)["posts"] if not p["post_id"]]
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar_todas"})
    assert f"Pieza {len(pendientes)} de {len(pendientes)}" in r.text
    camp = leer(client, c)
    assert all(p["post_id"] for p in camp["posts"])
    assert client.portal.call(contenido.imagenes.usadas, c.store)[0] == sum(campanas.costo(p) for p in pendientes)
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "aprobar_todas"})
    assert "2 posts programados" in r.text

    # Proponer de nuevo deja los que tienen imagen; una campaña nueva empieza en blanco.
    assert "Nueva campaña" in client.get("/panel/contenido/campanas").text
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(nueva="1", nombre="Navidad"))
    camp = leer(client, c)
    assert camp["nombre"] == "Navidad" and not any(p["post_id"] for p in camp["posts"])


def test_lo_que_preguntan_tus_clientes(cliente, monkeypatch):
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    async def temas(c):
        return {"temas": [{"tema": "Horario de fin de semana", "personas": 9, "objetivo": "explicar",
                           "ejemplo": "¿Abren el <domingo>?", "idea": ""}],
                "elogios": ["El latte está buenísimo"], "mensajes": 30}

    monkeypatch.setattr(campanas.semana, "temas", temas)
    assert 'name="clientes" value="1" checked' in client.get("/panel/contenido/campanas").text
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(clientes="1"))
    prompt = llamadas[-1][0]
    assert "<lo_que_preguntan>" in prompt and "Horario de fin de semana (9 personas)" in prompt
    assert "&lt;domingo&gt;" in prompt and "<lo_que_dicen>\n- El latte está buenísimo" in prompt
    camp = leer(client, c)
    assert camp["clientes"] and camp["posts"][0]["responde"] == "Horario de fin de semana"
    r = client.get("/panel/contenido/campanas")
    assert r.text.count('class="cli-cam">De tus clientes') == 1
    assert "Responde a lo que te preguntan: Horario de fin de semana" in client.get(
        f"/panel/contenido/campanas?post={camp['posts'][0]['k']}").text

    # Sin la casilla no se manda.
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(nueva="1"))
    assert "<lo_que_preguntan>" not in llamadas[-1][0] and not leer(client, c)["clientes"]


def test_cupo_extra_y_sin_plan(cliente, monkeypatch):
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    assert "solo incluye el asistente" in client.get("/panel/contenido/campanas").text
    monkeypatch.setattr(c, "imagenes_mes", 1)
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(f_texto="", f_foto=""))
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar_todas"})
    assert "Ya usaste las imágenes de tu plan" in r.text  # 3 fotos no caben en 1: confirma antes
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(f_titular="", f_texto="",
                                                                                     f_foto=""))
    assert "Elige al menos un formato." in r.text


def test_collage_con_fotos_de_tu_marca(cliente, monkeypatch):
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(c, "imagenes_mes", 4)
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))

    def foto(color):
        out = io.BytesIO()
        Image.new("RGB", (300, 400), color).save(out, "JPEG")
        return out.getvalue()

    tokens = []
    for i, color in enumerate(("red", "blue", "green")):
        token = f"foto{i}"
        (contenido.archivo(c, token)).parent.mkdir(parents=True, exist_ok=True)
        contenido.archivo(c, token).write_bytes(foto(color))
        tokens.append(token)
    client.portal.call(c.store.set, "marca", json.dumps({"fotos": tokens}))
    assert 'name="f_collage" value="1" checked' in client.get("/panel/contenido/campanas").text

    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data=datos_campana(f_titular="", f_texto="", f_foto="", f_collage="1"))
    camp = leer(client, c)
    assert {p["formato"] for p in camp["posts"]} == {"collage"}
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar", "k": "1"})
    post = client.portal.call(c.store.post, campanas.buscar_post(leer(client, c), "1")["post_id"])
    token = client.portal.call(c.store.media, post["media_ids"][0])[0]
    assert Image.open(contenido.archivo(c, token)).size == (imagenes.ANCHO, imagenes.ALTO)
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)


def test_repartir_parejo():
    plan = campanas.repartir(18, ["ensena", "dato", "producto", "detras", "marca", "pregunta"],
                             ["foto", "titular", "texto", "collage", "vs"], ["manana", "mediodia", "noche"], 7)
    temas, formatos, horas = (Counter(x[i] for x in plan) for i in range(3))
    assert set(temas.values()) == {3} and set(horas.values()) == {6}
    assert sorted(formatos.values()) == [3, 3, 4, 4, 4]
    assert all(plan[i][0] != plan[i - 1][0] for i in range(1, 18))  # el mismo tema no sale dos días seguidos


def test_piezas_sin_generador():
    jpg = imagenes.solo_texto(imagenes.Texto("El espresso tiene menos cafeína", "Que un americano", firma="@cafe"),
                              imagenes.Marca(), "Dato curioso")
    assert Image.open(io.BytesIO(jpg)).size == (imagenes.ANCHO, imagenes.ALTO)
    fotos = []
    for color in ("red", "blue"):
        out = io.BytesIO()
        Image.new("RGB", (200, 200), color).save(out, "JPEG")
        fotos.append(out.getvalue())
    for n in (2, 3, 4):
        img = Image.open(io.BytesIO(imagenes.collage((fotos * 2)[:n])))
        assert img.size == (imagenes.ANCHO, imagenes.ALTO)


def test_del_form():
    camp = {"inicio": LUNES.isoformat(), "semanas": 3, "publicar": "lun-sab"}
    assert len(campanas.dias_de(camp)) == 18 and len(campanas.rango(camp)) == 21
    datos = campanas.del_form({"semanas": "9", "inicio": "mal", "t_dato": "1", "f_texto": "1", "f_carrusel": "1"})
    assert datos["semanas"] == 3 and datos["temas"] == ["dato"] and datos["formatos"] == ["texto"]
    assert campanas.falta_en(datos) == "Elige de 2 a 6 temas."


def test_tus_campanas_y_reporte(cliente, monkeypatch):
    """La campaña anterior pasa a Tus campañas y el reporte ordena sus posts por Impacto."""
    from igbot import informe

    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    monkeypatch.setattr(informe, "_insights", {})
    llamadas = []
    import app as bot
    fake = claude_de_campana(llamadas)

    async def claude(prompt, system, **kw):
        if "analista de redes" in system:
            llamadas.append((prompt, system))
            return json.dumps({"conclusiones": ["La foto sola ganó.", "Publica de noche.", "Aún es una señal."]})
        return await fake(prompt, system, **kw)

    monkeypatch.setattr(bot.agent, "run_claude", claude)
    assert "Tus campañas" not in client.get("/panel/contenido/campanas").text

    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana())
    client.post("/panel/contenido/campanas", headers=ORIGEN, data={"accion": "generar_todas"})
    camp = leer(client, c)
    # Se publicaron hace una semana; el de en medio, sin link (Instagram no lo regresó).
    ids = [p["post_id"] for p in camp["posts"]]
    hace = time.time() - 7 * 86400
    for i, post_id in enumerate(ids):
        client.portal.call(partial(c.store.update_post, post_id, publish_at=hace + i))
        client.portal.call(c.store.set_post, post_id, "publicada", f"https://www.instagram.com/p/COD{i}/" if i != 1 else "")
    client.portal.call(c.store.set, "media_de:https://www.instagram.com/p/COD0/", "m0")  # guardado al publicar

    paginas = []

    async def media_page(limit, after="", fields=""):  # COD2 es viejo: se busca en Instagram
        paginas.append(fields)
        return [{"id": "m2", "permalink": "https://www.instagram.com/p/COD2/"}], ""

    async def media_insights(media_id):
        return {"m0": {"reach": 100, "likes": 10, "comments": 0, "saved": 0, "shares": 0},
                "m2": {"reach": 100, "likes": 10, "comments": 2, "saved": 2, "shares": 1}}[media_id]

    monkeypatch.setattr(c.instagram, "media_page", media_page)
    monkeypatch.setattr(c.instagram, "media_insights", media_insights, raising=False)

    r = client.get("/panel/contenido/campanas")
    assert "Tus campañas" in r.text and "3 posts publicados" in r.text and "Actual" in r.text

    # Una campaña nueva: la de Otoño queda en el historial, con lo que se probó.
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(nueva="1", nombre="Navidad"))
    pasadas = client.portal.call(informe.historial, c)
    assert [x["nombre"] for x in pasadas] == ["Otoño"] and len(pasadas[0]["posts"]) == 3
    r = client.get("/panel/contenido/campanas")
    assert "Otoño" in r.text and "Navidad" in r.text and f'value="{pasadas[0]["id"]}" checked' in r.text
    assert "Aún sin posts publicados" in r.text

    def filas():
        with usar(c):
            return informe.filas(c, pasadas)
    renglones = client.portal.call(filas)
    assert [f["impacto"] for f in renglones] == [24.0, 10.0, None]  # (10 + 2·2 + 3·2 + 4·1) / 100 × 100; sin link, sin números
    assert paginas == ["id,permalink"] and client.portal.call(c.store.get, "media_de:https://www.instagram.com/p/COD2/") == "m2"
    assert all(f["maduro"] for f in renglones) and informe.por(renglones, "formato")[0][1] == 24.0

    r = client.get(f"/panel/contenido/campanas/reporte?c={pasadas[0]['id']}")
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="reporte-otono.pdf"' in r.headers["content-disposition"]
    assert "Foto" in llamadas[-1][0] or "titular" in llamadas[-1][0].lower()  # las conclusiones parten de los números
    n = len(llamadas)
    client.get(f"/panel/contenido/campanas/reporte?c={pasadas[0]['id']}")
    assert len(llamadas) == n  # mismos números: no se le vuelve a preguntar a Claude

    assert "Elige al menos una campaña" in client.get("/panel/contenido/campanas/reporte?c=999").text


def test_impacto():
    from igbot import informe
    assert informe.impacto({"reach": 200, "likes": 10, "comments": 5, "saved": 2, "shares": 1}) == 15.0
    assert informe.impacto({"reach": 0, "likes": 3}) is None and informe.impacto(None) is None
    assert [informe.franja(h) for h in ("09:00", "14:00", "20:00", "")] == ["manana", "mediodia", "noche", "noche"]
