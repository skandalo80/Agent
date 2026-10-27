"""Contenido > Campañas: explorar temas, formatos y horarios; proponer sin imágenes, moverlo, editarlo,
generar al final y aprobar."""
import io
import json
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
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


CONCEPTO = {"titulo": "Idea", "resumen": "El latte de temporada sale del tueste de esta semana.",
            "pilar_fuerte": "lo que te hace distinto", "insight": "Quien trabaja cerca toma café de cadena por prisa.",
            "mensaje_clave": "Un latte hecho con café que tostamos esta semana.",
            "pilares": [{"nombre": "El tueste del lunes", "detalle": "Granos frescos de la casa."},
                        {"nombre": "Para llevar", "detalle": "Listo en tres minutos."}],
            "arco": "Semana 1 presenta; semana 2 empuja la compra.", "direccion_visual": "Luz de mañana, barra de madera.",
            "no_decir": "Nada de descuentos que no estén en tu perfil."}


def claude_de_campana(llamadas):
    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        llamadas.append((prompt, system))
        if "director creativo" in system:  # el plan de cada post, por su número
            n = len(re.findall(r"^\d+\. ", prompt.split("<posts>")[-1], re.M))
            return json.dumps({"plan": [{"n": i + 1, "objetivo": f"Objetivo {i + 1}", "angulo": f"Ángulo {i + 1}",
                                         "titular": f"Titular {i + 1}", "visual": f"Visual {i + 1}", "cta": "Escríbenos",
                                         "distinto": "Otra escena"} for i in range(n)]})
        if "estratega de contenido" in system:  # el brief: tres ideas de campaña
            return json.dumps({"conceptos": [{**CONCEPTO, "titulo": f"Idea {i + 1}"} for i in range(3)]})
        if "Armas una campaña" in system or "ya tiene su brief" in system:
            return json.dumps({"posts": [
                {"dia": d, "titulo": f"Post {i + 1}", "subtitulo": "", "caption": f"Texto {i + 1} #cafe",
                 "idea": "" if formato.split(" | ")[0] == "Solo texto" else f"Foto {i + 1}",
                 "prompt": "" if formato.split(" | ")[0] == "Solo texto" else f"Photo {i + 1}",
                 "responde": "Horario de fin de semana" if i == 0 and "<lo_que_preguntan>" in prompt else ""}
                for i, (d, formato) in enumerate(LINEA.findall(prompt.split("<posts>")[-1]))]})
        if "otra versión" in system:
            return json.dumps({"titulo": "Otra idea", "subtitulo": "", "caption": "Texto nuevo", "idea": "Foto nueva"})
        return "{}"
    return fake


BASE = "/panel/contenido/campanas"


def leer(client, c):
    """La última campaña que se armó."""
    return max(client.portal.call(campanas.todas_las, c), key=lambda x: x["creada"])


def url(client, c):
    return campanas.url_de(leer(client, c))


def cuando_sale(client, c, dia, hora):
    def calcular():
        with usar(c):
            return campanas.momento(dia, hora)
    return calcular()


def listo(client, c):
    """Perfil de empresa y Tu marca llenos: sin ellos, Crear campaña pide completarlos primero."""
    client.portal.call(c.store.set, "empresa", json.dumps({"nombre": "Café Lunar", "giro": "Restaurante o cafetería",
                                                           "descripcion": "Café de especialidad"}))
    marca = json.loads(client.portal.call(c.store.get, "marca") or "{}")
    marca.setdefault("estilo", "calido")
    marca["pasos"] = sorted({*marca.get("pasos", []), "estilo", "colores"})  # lo mínimo de Tu marca (marca.lista)
    client.portal.call(c.store.set, "marca", json.dumps(marca))


def datos_campana(**cambios):
    datos = {"accion": "proponer", "nombre": "Otoño", "inicio": LUNES.isoformat(), "semanas": "1",
             "publicar": "lmv", "tema": "Latte <b>de calabaza</b>", "t_dato": "1", "t_producto": "1",
             "f_titular": "1", "f_texto": "1", "f_foto": "1", "h_manana": "1", "h_noche": "1"}
    return {**datos, **cambios}


def test_campana_de_punta_a_punta(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    monkeypatch.setattr(campanas.config, "CONTENT_MODEL", "modelo")
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    r = client.get("/panel/contenido/campanas")
    assert 'href="/panel/contenido/campanas" class="on">Campañas' in r.text
    assert "Necesitamos conocer tu negocio" in r.text and "Completar tu empresa" in r.text  # sin empresa ni marca

    # Con un solo tema no se puede: vuelve con lo que marcó.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(t_producto=""))
    assert "Elige de 2 a 6 temas." in r.text
    assert not llamadas

    # Lunes, miércoles y viernes de una semana; dos temas, tres formatos, dos horarios.
    r = client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana())
    assert "Armando tu campaña" in r.text
    prompt, sistema = llamadas[-1]
    assert "&lt;b&gt;de calabaza" in prompt and "tema: Dato curioso" in prompt
    camp = leer(client, c)
    u = campanas.url_de(camp)
    lun, mie, vie = ((LUNES + timedelta(days=d)).isoformat() for d in (0, 2, 4))
    assert camp["modo"] == "explorar" and [p["dia"] for p in camp["posts"]] == [lun, mie, vie]
    assert {p["formato"] for p in camp["posts"]} == {"titular", "texto", "foto"}
    assert {p["tipo"] for p in camp["posts"]} == {"dato", "producto"}
    assert {p["hora"] for p in camp["posts"]} <= {"09:00", "20:00"}
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)  # proponer no gasta imágenes

    r = client.get(u)
    assert "Otoño" in r.text and r.text.count("sin imagen") == 3 and 'draggable="true"' in r.text
    assert f'data-dia="{lun}" data-publica>' in r.text and 'data-dia="2099-01-06">' in r.text  # martes no publica
    assert "Se prueba:" in r.text and "3 formatos" in r.text
    # Las tarjetas dicen si es post o historia, sin el tema ni el formato en gris.
    assert '<span class="medio-cam m-post">Post</span>' in r.text and '<span class="fmt-cam">' not in r.text
    assert "Arma tu campaña" not in r.text and "↓ Descargar planeación" in r.text  # sin los tres pasos arriba
    assert "Generar las 3 que faltan · usa 2" in r.text  # el de solo texto no usa cupo

    # Arrastrar el del lunes al miércoles: se intercambian.
    k_lun, k_mie = camp["posts"][0]["k"], camp["posts"][1]["k"]
    r = client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(k_lun), "dia": mie},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#d-{mie}")
    camp = leer(client, c)
    assert campanas.buscar_post(camp, k_lun)["dia"] == mie and campanas.buscar_post(camp, k_mie)["dia"] == lun
    # Desde el arrastre (suave): panel.js ya movió la tarjeta, así que 204 y la página no se recarga.
    r = client.post(u, headers=ORIGEN, follow_redirects=False,
                    data={"accion": "mover", "k": str(k_lun), "dia": lun, "suave": "1"})
    assert r.status_code == 204
    assert campanas.buscar_post(leer(client, c), k_lun)["dia"] == lun
    client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(k_lun), "dia": mie})
    r = client.post(u, headers=ORIGEN, data={"accion": "mover", "k": "1", "dia": "2099-03-01"})
    assert "Ese día no es de la campaña." in r.text
    assert client.post(u, data={"accion": "mover", "k": "1", "dia": vie}).status_code == 403

    # El panel del post: cambiar hora, formato y texto; otra idea.
    r = client.get(f"{u}?post=3")
    assert 'id="detalle"' in r.text and "Texto 3 #cafe" in r.text and 'name="formato"' in r.text
    client.post(u, headers=ORIGEN,
                data={"accion": "guardar", "k": "3", "dia": vie, "hora": "10:30", "formato": "texto",
                      "titulo": "El espresso tiene menos cafeína", "subtitulo": "Que un americano",
                      "caption": "Texto editado"})
    p3 = campanas.buscar_post(leer(client, c), "3")
    assert (p3["hora"], p3["formato"], p3["subtitulo"], p3["caption"]) == ("10:30", "texto", "Que un americano",
                                                                          "Texto editado")
    r = client.get(f"{u}?post=3")
    assert "Generar no usa tu cupo" in r.text and 'name="idea"' not in r.text
    client.post(u, headers=ORIGEN, data={"accion": "otra", "k": "2"})
    assert campanas.buscar_post(leer(client, c), "2")["titulo"] == "Otra idea"

    # Solo texto se arma aquí: queda en borrador en su día y hora sin usar el cupo; luego aprobarlo.
    r = client.post(u, headers=ORIGEN, data={"accion": "generar", "k": "3"})
    assert "Armando la pieza" in r.text
    p3 = campanas.buscar_post(leer(client, c), "3")
    post = client.portal.call(c.store.post, p3["post_id"])
    assert post["status"] == "borrador" and post["caption"] == "Texto editado"
    assert post["publish_at"] == cuando_sale(client, c, vie, "10:30")
    pieza = client.portal.call(contenido.pieza, c.store, post["id"])
    assert (pieza["campana"], pieza["formato"]) == (3, "texto")
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)
    # "Otra imagen" va junto a la imagen (envía el mismo form) y no entre los botones de abajo, junto a Aprobar.
    r = client.get(f"{u}?post=3")
    assert 'name="accion" value="generar">↻ Volver a generar imagen' in r.text and 'class="modal-cam"' in r.text
    assert r.text.index("↻ Volver a generar imagen") < r.text.index('value="aprobar"')
    assert "Abrir en el editor" not in r.text
    if os.environ.get("GUARDAR_HTML"):
        open(os.environ["GUARDAR_HTML"], "w").write(r.text)
    r = client.post(u, headers=ORIGEN, data={"accion": "aprobar", "k": "3"})
    assert "Listo: sale el" in r.text
    assert client.portal.call(c.store.post, post["id"])["status"] == "programada"

    # El post de campaña se edita en su ventana de la campaña: el editor manda ahí.
    r = client.get(f"/panel/contenido/post/{post['id']}", follow_redirects=False)
    assert r.headers["location"] == f"{u}?post=3#detalle"
    r = client.post(f"/panel/contenido/post/{post['id']}", headers=ORIGEN,
                    data={"accion": "guardar", "caption": "Desde el editor", "cuando": "2099-01-09T10:30"})
    assert "El título no puede quedar vacío" not in r.text
    assert client.portal.call(c.store.post, post["id"])["caption"] == "Desde el editor"
    assert campanas.buscar_post(leer(client, c), "3")["caption"] == "Desde el editor"
    # Volver a generar desde el editor: la campaña la arma de nuevo y se quedan el texto y la hora del editor.
    antes = client.portal.call(c.store.post, post["id"])
    r = client.post(f"/panel/contenido/post/{post['id']}", headers=ORIGEN,
                    data={"accion": "otra", "caption": "Desde el editor", "cuando": "2099-01-09T10:30"})
    ahora = client.portal.call(c.store.post, post["id"])
    assert "Generando otra imagen" in r.text and ahora["media_ids"] != antes["media_ids"]
    assert (ahora["caption"], ahora["publish_at"]) == ("Desde el editor", antes["publish_at"])
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)

    # Moverlo después cambia también el post programado.
    client.post(u, headers=ORIGEN, data={"accion": "mover", "k": "3", "dia": lun})
    assert client.portal.call(c.store.post, post["id"])["publish_at"] == cuando_sale(client, c, lun, "10:30")

    # La propuesta para el dueño de la marca: un PDF con el calendario.
    assert f"{u}/propuesta" in client.get(u).text
    r = client.get(f"{u}/propuesta")
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="propuesta-' in r.headers["content-disposition"]

    # Las demás de un jalón y aprobar todas: solo las fotos usan cupo.
    pendientes = [p for p in leer(client, c)["posts"] if not p["post_id"]]
    r = client.post(u, headers=ORIGEN, data={"accion": "generar_todas"})
    assert f"Pieza {len(pendientes)} de {len(pendientes)}" in r.text
    camp = leer(client, c)
    assert all(p["post_id"] for p in camp["posts"])
    assert client.portal.call(contenido.imagenes.usadas, c.store)[0] == sum(campanas.costo(p) for p in pendientes)
    r = client.post(u, headers=ORIGEN, data={"accion": "aprobar_todas"})
    assert "3 posts programados" in r.text  # con su imagen nueva, el de solo texto volvió a borrador

    # Con la imagen hecha, la idea se queda para irla puliendo: "Otra imagen" usa la nueva.
    foto = next(p for p in camp["posts"] if p["formato"] in ("foto", "titular"))
    r = client.get(f"{u}?post={foto['k']}")
    assert 'name="idea"' in r.text and "Puedes editarlo antes de generar" in r.text
    assert '<details class="prompt-img"><summary>Prompt de la imagen</summary>' in r.text  # con imagen: cerrado
    client.post(u, headers=ORIGEN,
                data={"accion": "generar", "k": str(foto["k"]), "idea": "Laptop en un escritorio de madera"})
    assert campanas.buscar_post(leer(client, c), foto["k"])["idea"] == "Laptop en un escritorio de madera"
    assert "Laptop en un escritorio de madera" in proveedor.llamadas[-1][0]

    # Tu marca va como contexto de la imagen, con la casilla marcada de inicio; sin ella, solo la idea.
    monkeypatch.setattr(c, "imagenes_mes", 10)  # que no pida confirmar imágenes extra
    client.portal.call(c.store.set, "marca", json.dumps({"nombre": "Café Lunar", "frase": "Café de especialidad",
                                                         "principal": "#112233"}))
    assert 'name="con_marca" value="1" checked> Usar Tu marca como contexto' in r.text
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]),
                                                                   "con_marca_en_form": "1", "con_marca": "1"})
    assert "- Name: Café Lunar" in proveedor.llamadas[-1][0] and "main #112233" in proveedor.llamadas[-1][0]
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]),
                                                                   "con_marca_en_form": "1"})
    assert "Brand context" not in proveedor.llamadas[-1][0] and "Photo style" not in proveedor.llamadas[-1][0]
    r = client.get(f"{u}?post={foto['k']}")
    assert 'name="con_marca" value="1"> Usar Tu marca como contexto' in r.text

    # Si llega por el editor del post (formulario de antes), el prompt editado también se usa.
    pid = campanas.buscar_post(leer(client, c), foto["k"])["post_id"]
    client.post(f"/panel/contenido/post/{pid}", headers=ORIGEN,
                data={"accion": "otra", "caption": "x", "prompt": "A ceramic mug on a marble table"})
    assert "A ceramic mug on a marble table" in proveedor.llamadas[-1][0]
    assert campanas.buscar_post(leer(client, c), foto["k"])["idea"] == "A ceramic mug on a marble table"

    # Proponer de nuevo deja los que tienen imagen; una campaña nueva empieza en blanco y la otra sigue ahí.
    con_imagen = {p["k"] for p in leer(client, c)["posts"] if p["post_id"]}
    r = client.post(u, headers=ORIGEN, data=datos_campana(nombre="Otoño 2"))
    assert u in r.text and {p["k"] for p in leer(client, c)["posts"] if p["post_id"]} == con_imagen
    assert "+ Nueva campaña" in client.get(BASE).text
    client.post(BASE, headers=ORIGEN, data=datos_campana(nombre="Navidad"))
    camp = leer(client, c)
    assert camp["nombre"] == "Navidad" and not any(p["post_id"] for p in camp["posts"]) and campanas.url_de(camp) != u
    assert len(client.portal.call(campanas.todas_las, c)) == 2


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
    listo(client, c)
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(clientes="1"))
    prompt = llamadas[-1][0]
    assert "<lo_que_preguntan>" in prompt and "Horario de fin de semana (9 personas)" in prompt
    assert "&lt;domingo&gt;" in prompt and "<lo_que_dicen>\n- El latte está buenísimo" in prompt
    camp = leer(client, c)
    assert camp["clientes"] and camp["posts"][0]["responde"] == "Horario de fin de semana"
    r = client.get(url(client, c))
    assert r.text.count('class="cli-cam">De tus clientes') == 1
    assert "Responde a lo que te preguntan: Horario de fin de semana" in client.get(
        f"{url(client, c)}?post={camp['posts'][0]['k']}").text

    # Sin la casilla no se manda.
    client.post(BASE, headers=ORIGEN, data=datos_campana())
    assert "<lo_que_preguntan>" not in llamadas[-1][0] and not leer(client, c)["clientes"]


def test_cupo_extra_y_sin_plan(cliente, monkeypatch):
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    assert "Tu plan actual es el Asistente virtual" in client.get("/panel/contenido/campanas").text
    monkeypatch.setattr(c, "imagenes_mes", 1)
    client.post("/panel/contenido/campanas", headers=ORIGEN, data=datos_campana(f_texto="", f_foto=""))
    r = client.post(url(client, c), headers=ORIGEN, data={"accion": "generar_todas"})
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
    listo(client, c)
    r = client.get(f"{BASE}/nueva?paso=4")
    assert 'value="collage"' in r.text and 'value="collage" disabled' not in r.text

    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data=datos_campana(f_titular="", f_texto="", f_foto="", f_collage="1"))
    camp = leer(client, c)
    assert {p["formato"] for p in camp["posts"]} == {"collage"}
    client.post(url(client, c), headers=ORIGEN, data={"accion": "generar", "k": "1"})
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
    """La campaña anterior pasa a Tus campañas y el reporte ordena sus posts por tasa de interacción."""
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
    client.post(url(client, c), headers=ORIGEN, data={"accion": "generar_todas"})
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

    r = client.get(BASE)  # empieza en 2099: es próxima
    assert 'href="/panel/contenido/campanas?ver=proxima" class="on">Próximas<small>1</small>' in r.text
    assert "Otoño" in r.text and " · empieza en " in r.text and "Explorar" in r.text

    # Otra campaña junto a la de Otoño; cuando las dos terminan, están en Pasadas, con su reporte.
    client.post(BASE, headers=ORIGEN, data=datos_campana(nombre="Navidad"))
    despues = campanas.tools.now() + timedelta(days=(LUNES - date.today()).days + 30)
    monkeypatch.setattr(campanas.tools, "now", lambda: despues)
    pasadas = [x for x in client.portal.call(informe.todas, c, client.portal.call(campanas.todas_las, c))
               if x["nombre"] == "Otoño"]
    assert len(pasadas[0]["posts"]) == 3
    r = client.get(BASE)
    assert 'class="on">Pasadas<small>2</small>' in r.text
    assert "Otoño" in r.text and "Navidad" in r.text and f'value="{pasadas[0]["id"]}" form="tus-campanas" aria-label="Incluir en el reporte" checked' in r.text
    assert f'href="{BASE}/{pasadas[0]["id"]}">Abrir' in r.text and '<div class="camp apagada">' in r.text  # Navidad
    assert ">12.5%</b><span>Interacción" in r.text and ">200</b><span>Alcance" in r.text  # sus números en miniatura
    # El filtro de Pasadas: un rango donde no terminó ninguna.
    r = client.get(f"{BASE}?ver=pasada&periodo=rango&desde=2000-01-01&hasta=2000-02-01")
    assert "No hay campañas que hayan terminado en esas fechas." in r.text and 'value="rango" checked' in r.text

    def filas():
        with usar(c):
            return informe.filas(c, pasadas)
    renglones = client.portal.call(filas)
    assert [f["tasa"] for f in renglones] == [15.0, 10.0, None]  # (10 + 2 + 2 + 1) / 100; sin link, sin números
    assert paginas == ["id,permalink"] and client.portal.call(c.store.get, "media_de:https://www.instagram.com/p/COD2/") == "m2"
    assert all(f["maduro"] for f in renglones) and informe.por(renglones, "formato")[0][1] == 15.0

    r = client.get(f"/panel/contenido/campanas/reporte?c={pasadas[0]['id']}")
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="reporte-otono.pdf"' in r.headers["content-disposition"]
    assert "Foto" in llamadas[-1][0] or "titular" in llamadas[-1][0].lower()  # las conclusiones parten de los números
    n = len(llamadas)
    client.get(f"/panel/contenido/campanas/reporte?c={pasadas[0]['id']}")
    assert len(llamadas) == n  # mismos números: no se le vuelve a preguntar a Claude

    assert "Elige al menos una campaña" in client.get("/panel/contenido/campanas/reporte?c=999").text


def test_tasa_de_interaccion():
    from igbot import informe
    assert informe.tasa({"reach": 200, "likes": 10, "comments": 5, "saved": 2, "shares": 1, "views": 900}) == 9.0
    assert informe.tasa({"reach": 0, "likes": 3}) is None and informe.tasa(None) is None
    assert [informe.franja(h) for h in ("09:00", "14:00", "20:00", "")] == ["manana", "mediodia", "noche", "noche"]


def test_clases_sin_choque():
    """Cada clase de CSS de Campañas se define en un solo bloque."""
    from igbot import panel
    for clase in ("fila-cam", "camp", "modal-cam", "wiz"):
        assert len(re.findall(rf"^\.{clase}\{{", panel.ESTILO, re.M)) == 1, clase


def test_repetir_reparte_con_la_mezcla():
    gano = {"tema": [("dato", 6.8, 3), ("pregunta", 5.9, 3), ("ensena", 4.1, 3), ("marca", 9.9, 1)],
            "formato": [("foto", 6.2, 4), ("texto", 5.5, 4)], "franja": [("noche", 6.0, 6), ("manana", 3.9, 6)]}
    universo = campanas.universo(0, gano)
    plan, resumen = campanas.repetir(18, gano, universo, "equilibrado", 7)
    temas = Counter(t for t, *_ in plan)
    assert temas["dato"] == 13 and temas["pregunta"] == 4 and sum(temas.values()) == 18
    assert "marca" not in resumen["repite"].values()  # 9.9 pero con 1 post: no cuenta como ganador
    assert resumen["repite"] == {"tema": "dato", "formato": "foto", "franja": "noche"}
    assert all(k not in ("dato", "pregunta") for k in resumen["prueba"]["tema"])
    assert "collage" not in universo["formato"]  # sin fotos en Tu marca
    nuevos = [p for p in plan if p[3] == "prueba"]
    assert nuevos and len(nuevos) < 18 // 2

    # Más seguro casi no prueba; en una dimensión sin ganador se sigue explorando todo.
    plan, resumen = campanas.repetir(18, {**gano, "franja": [("noche", 6.0, 2)]}, universo, "seguro", 7)
    assert Counter(t for t, *_ in plan)["dato"] == round(18 * .8)
    assert "franja" not in resumen["repite"] and all(p[3] == "prueba" for p in plan)
    assert {h for *_, h, _ in plan} == set(campanas.HORARIOS)


def test_repetir_lo_que_funciono(cliente, monkeypatch):
    """Con 12 posts con resultados se puede repetir: la forma que ganó, sin repetir asuntos ni preguntas."""
    from igbot import informe

    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(c, "imagenes_mes", 40)
    monkeypatch.setattr(informe, "_insights", {})
    llamadas = []
    fake = claude_de_campana(llamadas)

    async def claude(prompt, system, **kw):  # todos dicen responder a una pregunta: solo 1 de cada 4 cuenta
        texto = json.loads(await fake(prompt, system, **kw))
        for p in texto.get("posts", []):
            p["responde"] = "Leche vegetal" if "<lo_que_preguntan>" in prompt else ""
        return json.dumps(texto)

    monkeypatch.setattr(bot.agent, "run_claude", claude)

    async def temas(c):
        return {"temas": [{"tema": "Horario de fin de semana", "personas": 9, "objetivo": "", "ejemplo": "", "idea": ""},
                          {"tema": "Leche vegetal", "personas": 5, "objetivo": "", "ejemplo": "", "idea": ""}],
                "elogios": [], "mensajes": 30}

    monkeypatch.setattr(campanas.semana, "temas", temas)

    # Una campaña anterior con 12 posts publicados hace una semana: gana Dato curioso, en foto sola, de noche.
    hace = time.time() - 7 * 86400
    posts, numeros = [], {}
    for i in range(12):
        tema, formato, hora = (("dato", "foto", "20:00") if i % 2 else ("producto", "titular", "09:00"))
        post_id = client.portal.call(c.store.add_post, "cafe", [], f"Post {i}", hace + i)
        link = f"https://www.instagram.com/p/OLD{i}/"
        client.portal.call(c.store.set_post, post_id, "publicada", link)
        client.portal.call(c.store.set, f"media_de:{link}", f"m{i}")
        numeros[f"m{i}"] = {"reach": 100, "likes": 8 if i % 2 else 2, "comments": 0, "saved": 0, "shares": 0}
        posts.append({"post_id": post_id, "tipo": tema, "formato": formato, "hora": hora, "titulo": f"Asunto viejo {i}",
                      "responde": "Horario de fin de semana" if i == 0 else ""})
    client.portal.call(c.store.set, "campanas_pasadas", json.dumps(
        [{"id": 1, "nombre": "Otoño", "inicio": "2026-09-07", "semanas": 2, "modo": "explorar", "posts": posts}]))

    async def media_insights(media_id):
        return numeros[media_id]

    monkeypatch.setattr(c.instagram, "media_insights", media_insights, raising=False)

    listo(client, c)

    client.post("/panel/contenido/campanas", headers=ORIGEN,
                data={**datos_campana(semanas="2", publicar="lun-sab", clientes="1"), "modo": "repetir",
                      "mezcla": "equilibrado"})
    prompt, sistema = llamadas[-1]
    assert "<ya_publicado>\n- Asunto viejo 0" in prompt and "como máximo 3 posts" in sistema
    assert "Leche vegetal" in prompt and "Horario de fin de semana" not in prompt  # ya la respondió
    camp = leer(client, c)
    assert camp["modo"] == "repetir" and len(camp["posts"]) == 12
    assert Counter(p["tipo"] for p in camp["posts"])["dato"] == round(12 * .7)
    assert {p["rol"] for p in camp["posts"]} == {"repite", "prueba"}
    assert sum(1 for p in camp["posts"] if p["responde"]) == 3  # 12 posts: máximo 1 de cada 4
    r = client.get(url(client, c))
    assert "Repite:</span>" in r.text and "Sigue probando" in r.text and 'class="rol-cam rep">Repite' in r.text
    assert '<p class="antetitulo">Contenido · Campañas · ' in r.text and 'class="bajada"' not in r.text

    # Sin resultados suficientes no se puede repetir aunque lo pidan.
    client.portal.call(c.store.set, "campanas_pasadas", "[]")
    r = client.post("/panel/contenido/campanas", headers=ORIGEN,
                    data={**datos_campana(), "modo": "repetir"})
    assert "Todavía no hay resultados suficientes" in r.text


def test_repetir_completa_lo_poco_probado():
    gano = {"tema": [("dato", 6.8, 3), ("pregunta", 5.9, 3), ("marca", 9.9, 1)], "formato": [("foto", 6.2, 4)],
            "franja": [("noche", 6.0, 6)]}
    plan, resumen = campanas.repetir(18, gano, campanas.universo(0, gano), "equilibrado", 3)
    assert resumen["prueba"]["tema"] == ["marca"]  # 1 post: va antes que los nunca probados


def test_otro_collage_cambia():
    import random
    azar = random.Random(1)
    antes = ["a", "b"]
    for fotos in (["a", "b"], ["a", "b", "c", "d", "e"]):
        for _ in range(20):
            nuevas = campanas.otro_collage([fotos], antes, azar)
            assert nuevas != antes and 2 <= len(nuevas) <= 4 and set(nuevas) <= set(fotos)
            antes = nuevas


def test_otro_collage_empieza_por_lo_mas_parecido():
    import random
    azar = random.Random(2)
    grupos = [["a"], ["b", "c"], ["d"], ["e", "f", "g"]]
    for _ in range(30):
        nuevas = campanas.otro_collage(grupos, [], azar)
        assert nuevas[0] == "a" and set(nuevas[1:3]) <= {"b", "c"} and nuevas[3:] in ([], ["d"])


def test_collage_usa_fotos_de_posts_pasados(cliente):
    client, c, *_ = cliente
    from igbot.admin import PANEL

    def guardar_foto(nombre):
        contenido.archivo(c, nombre).parent.mkdir(parents=True, exist_ok=True)
        out = io.BytesIO()
        Image.new("RGB", (300, 400), "gray").save(out, "JPEG")
        contenido.archivo(c, nombre).write_bytes(out.getvalue())

    for nombre in ("marca0", "marca1", "pub1", "pub1.base", "pub2", "pub3", "pub4.base"):
        guardar_foto(nombre)

    async def preparar():
        ids = []
        for token, estado in (("pub1", "publicada"), ("pub2", "publicada"), ("pub3", "publicada"),
                              ("pub4", "cancelada"), ("pub5", "publicada")):
            post_id = await c.store.add_post(PANEL, [await c.store.add_media(PANEL, token, "x")], "t", 1)
            await c.store.set_post(post_id, estado)
            ids.append(post_id)
        await c.store.set("campanas_pasadas", json.dumps([{"id": 1, "nombre": "Antes", "inicio": "2099-01-05",
            "posts": [{"post_id": ids[0], "tipo": "dato", "formato": "titular"},  # mismo tema: su foto sin texto
                      {"post_id": ids[1], "tipo": "producto", "formato": "foto"},  # nada en común
                      {"post_id": ids[2], "tipo": "dato", "formato": "collage", "collage": ["marca0"]},  # los dos
                      {"post_id": ids[3], "tipo": "dato", "formato": "titular"},  # lo descartaron
                      {"post_id": ids[4], "tipo": "dato", "formato": "texto"}]}]))  # sin foto
        with usar(c):
            return await campanas.fotos_para_collage(c, {}, {"k": 1, "tipo": "dato", "post_id": None},
                                                     {"fotos": ["marca0", "marca1"]})

    grupos = client.portal.call(preparar)
    assert grupos == [["marca0"], [], ["pub1.base"], ["pub2", "marca1"]]


def test_varias_campanas(cliente, monkeypatch):
    """Varias campañas a la vez: activas, próximas y pasadas por fecha; editar nombre y fechas; estirarla al soltar
    un post fuera; y el mes en Contenido › Calendario."""
    client, c, *_ = cliente
    import app as bot
    monkeypatch.setattr(c, "imagenes_mes", 10)
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))

    # La campaña de antes (una sola, en el kv "campana") sigue ahí, con su id.
    vieja = {"nombre": "De antes", "inicio": LUNES.isoformat(), "semanas": 1, "publicar": "lmv", "creada": 5.0,
             "modo": "explorar", "posts": [{"k": 1, "tipo": "dato", "formato": "texto", "dia": LUNES.isoformat(),
                                            "hora": "09:00", "titulo": "Viejo", "caption": "c", "idea": "",
                                            "post_id": None}]}
    client.portal.call(c.store.set, "campana", json.dumps(vieja))
    assert [x["id"] for x in client.portal.call(campanas.todas_las, c)] == [5000]
    assert "De antes" in client.get(f"{BASE}/5000").text

    # Otra que se cruza con ella dos semanas después.
    client.post(BASE, headers=ORIGEN, data=datos_campana(nombre="Invierno", semanas="2"))
    inv = leer(client, c)
    u = campanas.url_de(inv)

    def invierno():
        return client.portal.call(campanas.leer, c, inv["id"])
    hoy = datetime(2099, 1, 7, 12, tzinfo=campanas.tools.tz())
    monkeypatch.setattr(campanas.tools, "now", lambda: hoy)
    client.post(BASE, headers=ORIGEN, data=datos_campana(nombre="Febrero", inicio="2099-01-12"))
    r = client.get(BASE)
    assert 'class="on">Activas<small>2</small>' in r.text and "día 3 de 14" in r.text and "<b>0 de 6</b>" in r.text
    r = client.get(f"{BASE}?ver=proxima")
    assert "Febrero" in r.text and "Se cruza 7 días con «Invierno»" in r.text and "empieza en 5 días" in r.text

    # Editar: un post no puede quedar fuera; si no, solo cambia el rango y los posts se quedan en su día.
    dias = [p["dia"] for p in inv["posts"]]
    r = client.post(u, headers=ORIGEN, data={"accion": "editar", "nombre": "Invierno", "inicio": "2099-01-06",
                                             "fin": "2099-01-18"})
    assert "quedaría fuera de la campaña" in r.text and invierno()["inicio"] == LUNES.isoformat()
    r = client.post(u, headers=ORIGEN, data={"accion": "editar", "nombre": "Invierno <b>", "inicio": "2099-01-05",
                                             "fin": "2099-01-25"})
    camp = invierno()
    assert (camp["nombre"], camp["fin"]) == ("Invierno <b>", "2099-01-25") and [p["dia"] for p in camp["posts"]] == dias
    assert "Invierno &lt;b&gt;" in r.text and 'value="2099-01-25"' in r.text
    assert client.post(u, headers=ORIGEN, data={"accion": "editar", "inicio": "2099-01-25",
                                                "fin": "2099-01-05"}).text.count("antes de empezar") == 1

    # El domingo de antes está fuera (rayado) pero recibe posts: soltar uno ahí estira la campaña.
    r = client.get(u)
    assert 'class="dia-cam fuera" id="d-2099-01-04" data-dia="2099-01-04"' in r.text and f'data-camp="{u}"' in r.text
    k = camp["posts"][0]["k"]
    r = client.post(u, headers=ORIGEN, follow_redirects=False, data={"accion": "mover", "k": str(k), "dia": "2099-01-04"})
    assert r.status_code == 303
    camp = invierno()
    assert camp["inicio"] == "2099-01-04" and campanas.buscar_post(camp, k)["dia"] == "2099-01-04"
    assert 'id="d-2099-01-04" data-dia="2099-01-04" data-publica' not in client.get(u).text  # domingo: no publica
    assert "Ese día no es de la campaña." in client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(k),
                                                                                  "dia": "2098-12-01"}).text

    # Contenido › Calendario: cada post en su día, con el color de su estado y su campaña.
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(k)})
    post_id = campanas.buscar_post(invierno(), k)["post_id"]
    r = client.get("/panel/contenido?mes=2099-01")
    assert "Enero de 2099" in r.text and 'class="cal-mes"' in r.text and "fecha=2099-02-01" in r.text
    # Con miniatura, el título arriba y abajo la hora y si es post o historia; la campaña, en el título flotante.
    assert f'class="ev e-borrador con-img" href="/panel/contenido/post/{post_id}"' in r.text
    assert f'href="{u}?post=' in r.text and " · Invierno &lt;b&gt; · Borrador" in r.text  # las propuestas sin imagen
    assert '<span class="m-post">Post</span></small>' in r.text
    assert "Próximos posts" not in r.text and "Ya publicados" not in r.text  # sin listas debajo
    r = client.get("/panel/contenido?vista=semana&fecha=2099-01-05")
    assert "5 al 11 de enero de 2099" in r.text and 'class="cal-horas" style="--dias:7"' in r.text
    assert " · Invierno &lt;b&gt; · Borrador" in r.text
    r = client.get("/panel/contenido?vista=dia&fecha=2099-01-05&ver=lista")
    assert "Lunes 5 de enero" in r.text and "Borradores" in r.text and "Próximos posts" not in r.text
    r = client.get("/panel/contenido?fecha=2099-01-05&estado=publicado")
    assert 'e-borrador" href' not in r.text and 'class="ev e-publicado con-img" href="https://www.instagram.com/p/DESDEAPP/"' in r.text

    # Arrastrar en el calendario: el post con imagen pasa a otro día con su misma hora, y en Semana/Día a otra hora.
    assert f'draggable="true" data-mover="p:{post_id}"' in client.get("/panel/contenido?mes=2099-01").text
    assert 'DESDEAPP/" target="_blank" rel="noopener" draggable' not in r.text  # lo publicado no se arrastra
    antes = campanas.buscar_post(invierno(), k)
    mover = "/panel/contenido/mover"
    r = client.post(mover, headers=ORIGEN, follow_redirects=False,
                    data={"ref": f"p:{post_id}", "dia": "2099-01-20", "suave": "1"})
    assert r.status_code == 204
    p = campanas.buscar_post(invierno(), k)
    assert p["dia"] == "2099-01-20" and p["hora"] == antes["hora"]
    post = client.portal.call(c.store.post, post_id)
    assert post["publish_at"] == cuando_sale(client, c, "2099-01-20", antes["hora"])
    r = client.post(mover, headers=ORIGEN, follow_redirects=False,
                    data={"ref": f"p:{post_id}", "dia": "2099-01-21", "h": "8", "suave": "1"})
    assert r.status_code == 204 and campanas.buscar_post(invierno(), k)["hora"] == "08" + antes["hora"][2:]
    # Una propuesta sin imagen se mueve en su campaña.
    otra = next(x for x in invierno()["posts"] if not x.get("post_id"))
    r = client.post(mover, headers=ORIGEN, follow_redirects=False,
                    data={"ref": f"c:{invierno()['id']}:{otra['k']}", "dia": "2099-01-22", "h": "20", "suave": "1"})
    assert r.status_code == 204 and campanas.buscar_post(invierno(), otra["k"])["dia"] == "2099-01-22"
    # A un día que ya pasó no: vuelve al calendario y dice por qué (con un código, no con el texto).
    r = client.post(mover, headers=ORIGEN, follow_redirects=False,
                    data={"ref": f"p:{post_id}", "dia": "2020-01-01", "suave": "1", "volver": "/panel/contenido?vista=semana#cal"})
    assert r.headers["location"] == "/panel/contenido?vista=semana&error=pasado#cal"
    assert "Elige un día y una hora que no hayan pasado." in client.get(r.headers["location"]).text
    r = client.post(mover, headers=ORIGEN, follow_redirects=False,
                    data={"ref": "p:999", "dia": "2099-01-20", "volver": "https://otro.sitio/"})
    assert r.headers["location"] == "/panel/contenido?error=no#cal"  # nunca a otro sitio
    assert client.post(mover, data={"ref": f"p:{post_id}", "dia": "2099-01-25"}).status_code == 403  # sin Origin
    client.post(mover, headers=ORIGEN, data={"ref": f"p:{post_id}", "dia": "2099-01-05", "h": antes["hora"][:2]})

    # El editor del post abre su ventana en la campaña de la que viene.
    p = campanas.buscar_post(invierno(), k)
    r = client.get(f"/panel/contenido/post/{post_id}")
    assert 'class="modal-cam"' in r.text and campanas.FORMATOS[p["formato"]][0] in r.text
    # Un id que no existe vuelve a la lista.
    assert client.get(f"{BASE}/123", follow_redirects=False).headers["location"] == BASE


def test_la_campana_se_escribe_con_la_empresa_la_marca_y_sus_parametros(cliente, monkeypatch):
    client, c, *_ = cliente
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    async def empresa_texto(cuenta):
        return "- Descripción: Café de especialidad en Puebla\n- Latte de calabaza: desde 75 MXN"
    monkeypatch.setattr(contenido, "empresa_texto", empresa_texto)
    client.portal.call(c.store.set, "marca", json.dumps({"estilo": "calido", "acento": "#E8590C"}))
    camp = {**campanas.del_form(datos_campana()), "id": "x", "creada": 1, "posts": [],
            "objetivo": "Vender más <lattes>", "producto": "Latte de calabaza", "visual": "Personas",
            "estilo": "Poco texto"}
    client.portal.call(campanas.proponer, c, camp)
    prompt, sistema = llamadas[-1]
    negocio = prompt.split("</negocio>")[0]
    assert negocio.index("Perfil de la empresa") < negocio.index("Lo que sabe su asistente")
    assert "- Descripción: Café de especialidad en Puebla" in negocio and "desde 75 MXN" in negocio
    assert "<marca_visual>\nEstilo visual (cómo se diseña la imagen, no objetos que deban salir siempre): Artesanal." in prompt and "acento #E8590C" in prompt
    assert "<objetivo>Vender más &lt;lattes&gt;</objetivo>" in prompt and "<producto>Latte de calabaza</producto>" in prompt
    assert "<lo_que_se_ve>Personas</lo_que_se_ve>" in prompt and "<estilo_de_campana>Poco texto</estilo_de_campana>" in prompt
    assert "de 120 a 180 palabras" in sistema and "nunca algo genérico del ramo" in sistema
