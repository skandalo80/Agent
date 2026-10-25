"""Crear campaña por pasos: objetivo, mensaje (con producto), ideas, formatos, qué se ve, estilo, cantidad y
resumen; y en la campaña, el editor del post con su regla, subir su imagen y dos posts en un día."""
import io
import json
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from PIL import Image  # noqa: E402
from test_campanas import BASE, LUNES, claude_de_campana, leer, listo  # noqa: E402
from test_contenido import ORIGEN, cliente  # noqa: E402

from igbot import campanas, contenido, nueva_campana  # noqa: E402

assert cliente  # el fixture de test_contenido, que usan estas pruebas

URL = f"{BASE}/nueva"
PRODUCTO = {"id": "p1", "nombre": "Latte de calabaza", "descripcion": "Con espresso doble", "precio": "75",
            "moneda": "MXN", "imagenes": [], "tipo": "producto"}


def paso(client, n, ir=None, **datos):
    return client.post(URL, headers=ORIGEN, follow_redirects=False,
                       data={"paso": str(n), "ir": str(ir or n + 1), **datos})


def llenar(client, objetivo="interaccion"):
    """Los siete pasos, con lo mínimo de cada uno."""
    assert paso(client, 1, objetivo=objetivo).headers["location"] == f"{URL}?paso=2"
    paso(client, 2, mensajes=["producto"], producto="p1", accion="promo", descuento="20", desde="2099-01-05",
         hasta="2099-01-18")
    paso(client, 3)
    paso(client, 4, formatos=["texto", "titular"])
    paso(client, 5, visual=["productos"])
    paso(client, 6, apariencia="natural", texto_img="minimo", variedad="coherencia", armado="plantilla")
    return paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="6",
                dias=["0", "2", "4"], horarios=["manana"])


def test_el_asistente_de_punta_a_punta(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))

    # Sin empresa ni marca no se puede: pide completarlas.
    r = client.get(URL)
    assert "Necesitamos conocer tu negocio" in r.text and 'href="/panel/empresa"' in r.text
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    r = client.get(URL)
    assert "Paso 1 de 7" in r.text and 'name="objetivo" value="interaccion"' in r.text
    assert 'class="enter-cam" name="ir" value="2"' in r.text  # Enter sigue al paso 2, no salta en el riel

    # Adelante no se pasa sin elegir; atrás o a otro paso, sí (y lo que estaba se guarda).
    r = paso(client, 1)
    assert "Elige qué quieres lograr." in r.text
    assert paso(client, 2, ir=1).headers["location"] == f"{URL}?paso=1"
    r = paso(client, 2, mensajes=["producto"], producto="p1", accion="promo")
    assert "Escribe el porcentaje de descuento" in r.text
    r = client.get(f"{URL}?paso=2")
    assert "Latte de calabaza · $75 MXN" in r.text and 'name="accion" value="promo" checked' in r.text

    # Cantidad: no caben más de dos por día.
    llenar(client)
    r = paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="7", dias=["0", "2", "4"],
             horarios=["manana"])
    assert "caben hasta 6 posts" in r.text
    llenar(client)
    r = client.get(f"{URL}?paso=8")
    assert "Revisa tu campaña antes de generarla" in r.text and "Latte de calabaza · Promoción con descuento 20 %" in r.text
    assert r.text.count("Editar</button>") == 8 and "Generar campaña" in r.text

    # Generar: 6 posts en 3 días (dos por día, a distinta hora), con el producto y su regla.
    r = client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "proponer"})
    assert "Armando tu campaña" in r.text
    prompt, sistema = llamadas[-1]
    assert "<producto>Latte de calabaza ($75 MXN)" in prompt and "20 % de descuento" in prompt and "<regla>sí</regla>" in prompt
    camp = leer(client, c)
    assert camp["nombre"] == "Otoño" and len(camp["posts"]) == 6
    dias = sorted(p["dia"] for p in camp["posts"])
    assert len(set(dias)) == 3 and all(dias.count(d) == 2 for d in dias)
    for d in set(dias):
        assert len({p["hora"] for p in camp["posts"] if p["dia"] == d}) == 2
    assert {p["formato"] for p in camp["posts"]} == {"texto", "titular"}
    assert all(p["regla"]["activa"] and p["regla"]["clave"] for p in camp["posts"])
    assert camp["producto_id"] == "p1" and camp["regla"] and json.loads(
        client.portal.call(c.store.get, nueva_campana.BORRADOR)) == {}

    # Editar estrategia: vuelve al resumen con lo que eligió, y generar de nuevo es la misma campaña.
    r = client.get(f"{URL}?editar={camp['id']}", follow_redirects=False)
    assert r.headers["location"] == f"{URL}?paso=8"
    r = client.get(r.headers["location"])
    assert "Revisa la estrategia de tu campaña" in r.text and "Volver a generar" in r.text
    client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "proponer"})
    assert len(client.portal.call(campanas.todas_las, c)) == 1


def test_sin_interaccion_no_hay_regla(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))
    listo(client, c)
    llenar(client, objetivo="vender")
    client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "proponer"})
    assert "<regla>" not in llamadas[-1][0]
    assert not any(p.get("regla") for p in leer(client, c)["posts"])


def test_huecos_reparte_y_dobla():
    camp = {"inicio": LUNES.isoformat(), "semanas": 1, "dias_semana": [0, 2, 4]}
    lun, mie, vie = ((LUNES.toordinal() + d) for d in (0, 2, 4))
    assert campanas.huecos({**camp, "n_posts": 2}) == [LUNES.fromordinal(lun).isoformat(), LUNES.fromordinal(mie).isoformat()]
    assert len(campanas.huecos({**camp, "n_posts": 5})) == 5
    assert len(campanas.huecos({**camp, "n_posts": 9})) == 6  # hasta dos por día
    assert len(campanas.huecos(camp)) == 3  # sin n_posts, uno por día
    assert LUNES.fromordinal(vie).isoformat() in campanas.huecos({**camp, "n_posts": 3})


def test_editor_regla_subir_y_dos_en_un_dia(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    llenar(client)
    client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "proponer"})
    camp = leer(client, c)
    u = campanas.url_de(camp)
    p1 = next(p for p in camp["posts"] if p["formato"] == "titular")
    p2 = next(p for p in camp["posts"] if p["dia"] != p1["dia"])

    # El editor centrado: texto, prompt, generar o subir, y la regla encendida (la campaña busca interacción).
    r = client.get(f"{u}?post={p1['k']}")
    assert 'class="modal-cam"' in r.text and 'class="velo-cam"' in r.text and "Texto que se publica" in r.text
    assert "Prompt de la imagen" in r.text and 'value="subir">Subir imagen' in r.text
    assert 'name="regla_activa" value="1" checked' in r.text and "esta campaña busca interacción" in r.text
    assert "Notas de producción" not in r.text

    # La regla se edita; sin palabra no se puede encender.
    r = client.post(u, headers=ORIGEN, data={"accion": "guardar", "k": str(p1["k"]), "regla_en_form": "1",
                                             "regla_activa": "1", "clave": ""})
    assert "Escribe la palabra que van a comentar" in r.text
    client.post(u, headers=ORIGEN, data={"accion": "guardar", "k": str(p1["k"]), "regla_en_form": "1",
                                         "regla_activa": "1", "clave": "precio!", "dm": "Cuesta 75"})
    regla = campanas.buscar_post(leer(client, c), p1["k"])["regla"]
    assert (regla["activa"], regla["clave"], regla["dm"]) == (True, "PRECIO", "Cuesta 75")

    # Subir su imagen: no usa el cupo y la regla llega a la pieza del post.
    sin_foto = client.post(u, headers=ORIGEN, data={"accion": "subir", "k": str(p1["k"])})
    assert "Elige primero la foto" in sin_foto.text
    foto = io.BytesIO()
    Image.new("RGB", (900, 1200), "teal").save(foto, "JPEG")
    r = client.post(u, headers=ORIGEN, data={"accion": "subir", "k": str(p1["k"])},
                    files={"imagen": ("mia.jpg", foto.getvalue(), "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"?post={p1['k']}#detalle")
    post_id = campanas.buscar_post(leer(client, c), p1["k"])["post_id"]
    assert client.portal.call(c.store.post, post_id)["status"] == "borrador"
    assert client.portal.call(contenido.imagenes.usadas, c.store) == (0, 0)
    pieza = client.portal.call(contenido.pieza, c.store, post_id)
    assert pieza["reglas"][0]["clave"] == "PRECIO" and pieza["reglas"][0]["dm"] == "Cuesta 75"
    r = client.post(u, headers=ORIGEN, data={"accion": "subir", "k": str(p2["k"])},
                    files={"imagen": ("mal.jpg", b"no es imagen", "image/jpeg")})
    assert "No pudimos leer esa imagen" in r.text

    # Apagar la regla en un post que ya existe la quita de su pieza.
    client.post(u, headers=ORIGEN, data={"accion": "guardar", "k": str(p1["k"]), "regla_en_form": "1",
                                         "clave": "PRECIO"})
    assert client.portal.call(contenido.pieza, c.store, post_id)["reglas"] == []

    # Arrastre: soltarlo en el espacio libre de un día con dos ya no cabe; sobre una tarjeta, se intercambian.
    otro = next(p for p in leer(client, c)["posts"] if p["dia"] == p2["dia"] and p["k"] != p2["k"])
    r = client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(p1["k"]), "dia": p2["dia"], "juntar": "1"})
    assert "Un día puede tener hasta 2 posts." in r.text
    client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(p1["k"]), "dia": p2["dia"], "con": str(otro["k"])})
    camp = leer(client, c)
    assert campanas.buscar_post(camp, p1["k"])["dia"] == p2["dia"] and campanas.buscar_post(camp, otro["k"])["dia"] == p1["dia"]
    assert campanas.buscar_post(camp, p2["k"])["dia"] == p2["dia"]
    # Soltarlo en el espacio libre de un día con lugar deja los dos ahí.
    martes = LUNES.fromordinal(LUNES.toordinal() + 1).isoformat()
    for k in (p1["k"], p2["k"]):
        client.post(u, headers=ORIGEN, data={"accion": "mover", "k": str(k), "dia": martes, "juntar": "1"})
    assert sorted(p["k"] for p in leer(client, c)["posts"] if p["dia"] == martes) == sorted([p1["k"], p2["k"]])
