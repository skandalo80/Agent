"""Crear campaña por pasos: objetivo, mensaje (con producto), ideas, formatos, qué se ve, estilo, cantidad y
resumen; y en la campaña, el editor del post con su regla, subir su imagen y dos posts en un día."""
import io
import json
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from PIL import Image  # noqa: E402
from test_campanas import BASE, LUNES, claude_de_campana, leer, listo  # noqa: E402
from test_contenido import ORIGEN, cliente  # noqa: E402

from igbot import brief, campanas, contenido, nueva_campana  # noqa: E402

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
    paso(client, 2, mensajes=["producto"], con_producto="1", producto="p1", accion="promo", descuento="20", desde="2099-01-05",
         hasta="2099-01-18")
    paso(client, 3, enfoques=["consejos", "historias"])
    paso(client, 4, formatos=["texto", "titular"], textos_img=["minimo", "titular"])
    paso(client, 5, visual=["productos"])
    paso(client, 6, variedad="coherencia")
    return paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="6",
                dias=["0", "2", "4"], horarios=["manana"])


def generar(client, **brief):
    """Resumen › Crear el brief › Escribir los posts (con lo que se ajuste del brief)."""
    r = client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "brief"})
    assert "Pensando las ideas de tu campaña" in r.text, r.text[-500:]
    return client.post(URL, headers=ORIGEN, data={"paso": "9", "accion": "proponer", **brief})


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
    assert "Paso 1 de 6" in r.text and 'name="objetivo" value="interaccion"' in r.text
    assert 'class="wiz-img' not in r.text  # el objetivo va sin miniaturas
    assert 'class="enter-cam" name="ir" value="2"' in r.text  # Enter sigue al paso 2, no salta en el riel

    # Adelante no se pasa sin elegir; atrás o a otro paso, sí (y lo que estaba se guarda).
    r = paso(client, 1)
    assert "Elige qué quieres lograr." in r.text
    assert paso(client, 2, ir=1).headers["location"] == f"{URL}?paso=1"
    r = paso(client, 2, mensajes=["producto"], con_producto="1", producto="p1", accion="promo")
    assert "Escribe el porcentaje de descuento" in r.text
    r = client.get(f"{URL}?paso=2")
    assert "Latte de calabaza · $75 MXN" in r.text and 'name="accion" value="promo" checked' in r.text
    assert 'name="con_producto" value="1" checked' in r.text and 'name="con_texto" value="1">' in r.text
    assert r.text.index('name="con_texto"') < r.text.index('name="con_producto"')  # primero tus palabras
    assert "Cómo lo usamos" not in client.get(f"{URL}?paso=1").text + client.get(f"{URL}?paso=3").text
    # Sin el check, el producto y tu texto no cuentan aunque vengan llenos.
    paso(client, 2, ir=1, mensajes=["producto"], producto="p1", texto="Otoño")
    b = json.loads(client.portal.call(c.store.get, "campana_borrador"))
    assert b["producto"] == "" and b["texto"] == ""
    paso(client, 2, ir=1, mensajes=["producto"], con_texto="1", texto="Otoño")
    assert 'name="con_texto" value="1" checked' in client.get(f"{URL}?paso=2").text
    # Enfoque del contenido: tarjetas con imagen, las que van con el objetivo marcadas; al menos una.
    paso(client, 1, ir=1, objetivo="interaccion")
    r = client.get(f"{URL}?paso=3")
    assert "Paso 3 de 6 · Enfoque del contenido" in r.text and r.text.count('name="enfoques"') == 7
    assert "tarjetas/campana/enfoque-consejos.webp" in r.text and r.text.count("Va con tu objetivo") == 3
    assert "Elige al menos un enfoque." in paso(client, 3).text
    # Qué se ve está fuera: de Formatos se sigue a Estilo y variedad.
    r = client.get(f"{URL}?paso=4")
    assert 'name="ir" value="6">Continuar' in r.text and ">Qué se ve<" not in r.text
    assert "Paso 5 de 6 · Estilo y variedad" in client.get(f"{URL}?paso=5").text

    # Cantidad: no caben más de dos por día.
    llenar(client)
    r = paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="7", dias=["0", "2", "4"],
             horarios=["manana"])
    assert "caben hasta 6 posts" in r.text
    llenar(client)
    r = client.get(f"{URL}?paso=8")
    assert "Revisa tu campaña antes de generarla" in r.text and "Latte de calabaza · Promoción con descuento 20 %" in r.text
    assert r.text.count("Editar</button>") == 7 and "Crear el brief" in r.text
    assert "Consejos e ideas útiles · Historias y situaciones cotidianas" in r.text
    assert 'class="cruce"' in r.text and "Con 6 posts cubrimos" in r.text

    # Sin brief no se escriben posts: vuelve al resumen.
    r = client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "proponer"}, follow_redirects=False)
    assert r.headers["location"] == f"{URL}?paso=8" and not llamadas

    # El brief: tres ideas; elige la segunda y ajusta su mensaje clave.
    r = client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "brief"})
    assert "Pensando las ideas de tu campaña" in r.text and f"{URL}?paso=9" in r.text
    assert "estratega de contenido" in llamadas[-1][1] and "<producto>Latte de calabaza" in llamadas[-1][0]
    r = client.get(f"{URL}?paso=9")
    assert "Elige la idea de tu campaña" in r.text and r.text.count('name="concepto"') == 3
    assert 'id="concepto-0" checked' in r.text and 'id="brief-2"' in r.text and "Escribir los posts" in r.text
    assert 'value="9"><b>✓</b>Brief' in r.text  # el riel ya tiene el paso Brief
    r = client.get(f"{URL}?paso=8")
    assert "Ver el brief" in r.text and "Otras ideas de brief" in r.text

    # Escribir los posts: 6 posts en 3 días (dos por día, a distinta hora), con el producto y su regla.
    r = client.post(URL, headers=ORIGEN, data={"paso": "9", "accion": "proponer", "concepto": "1",
                                               "b1_mensaje_clave": "Tostado <b>esta</b> semana", "b1_p0_nombre": "El tueste"})
    assert "Armando tu campaña" in r.text
    prompt, sistema = llamadas[-1]
    assert "<producto>Latte de calabaza ($75 MXN)" in prompt and "20 % de descuento" in prompt and "<regla>sí</regla>" in prompt
    assert "Concepto: Idea 2." in prompt and "Mensaje clave: Tostado &lt;b&gt;esta&lt;/b&gt; semana" in prompt
    assert "- El tueste:" in prompt and "ya tiene su brief" in sistema and "| pilar: " in prompt
    plan_prompt, plan_sistema = llamadas[-2]  # el plan de los 6 posts, juntos, antes de escribirlos
    assert "director creativo" in plan_sistema and "6. 2099-" in plan_prompt and "Concepto: Idea 2." in plan_prompt
    assert "| plan: objetivo: Objetivo 1; ángulo: Ángulo 1; titular: Titular 1" in prompt
    camp = leer(client, c)
    assert camp["brief"]["titulo"] == "Idea 2" and all(p["ve"] == "productos" for p in camp["posts"])
    assert all(p["plan"]["cta"] == "Escríbenos" for p in camp["posts"])
    assert camp["temas"] == ["ensena", "historias"]  # los enfoques que eligió
    assert {p["tipo"] for p in camp["posts"]} <= {"ensena", "historias"}
    r = client.get(f"{campanas.url_de(camp)}?post={camp['posts'][0]['k']}")
    assert "Plan de este post" in r.text and "<b>Ángulo:</b>" in r.text
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
    assert "Revisa la estrategia de tu campaña" in r.text and "Ver el brief" in r.text
    r = client.get(f"{URL}?paso=9")
    assert "Volver a escribir los posts" in r.text and "Idea 2" in r.text
    client.post(URL, headers=ORIGEN, data={"paso": "9", "accion": "proponer"})
    assert len(client.portal.call(campanas.todas_las, c)) == 1


def test_sin_interaccion_no_hay_regla(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))
    listo(client, c)
    llenar(client, objetivo="vender")
    generar(client)
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
    generar(client)
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


def test_cruzar_no_repite_hasta_usar_todas():
    from collections import Counter
    ves, formatos = ["productos", "personas", "graficos"], ["foto", "titular", "texto", "vs"]
    pares = brief.cruzar(15, ves, formatos, semilla=3)
    assert len(set(pares[:12])) == 12  # las 12 combinaciones antes de repetir alguna
    assert max(Counter(v for v, _ in pares).values()) - min(Counter(v for v, _ in pares).values()) <= 1
    assert max(Counter(f for _, f in pares).values()) - min(Counter(f for _, f in pares).values()) <= 1
    assert brief.cruzar(15, ves, formatos, semilla=3) == pares  # misma semilla, mismo cruce (resumen = posts)


def test_pilares_siguen_el_arco_y_temas_sin_respaldo():
    pil = brief.repartir_pilares(15, ["A", "B", "C", "D"])
    assert pil[0] == "A" and pil[-2] == "D" and set(pil) == {"A", "B", "C", "D"}
    assert brief.repartir_pilares(6, ["A", "B", "C", "D"]) == ["A", "B", "C", "D", "A", "B"]
    tipos = brief.sin_respaldo(["clientes", "promo", "ensena"], False, False, ["producto", "promo", "clientes", "ensena"])
    assert "clientes" not in tipos and "promo" not in tipos and tipos[2] == "ensena"
    assert brief.sin_respaldo(["clientes", "promo"], True, True, ["producto"]) == ["clientes", "promo"]


def test_la_imagen_usa_el_prompt_en_ingles_salvo_que_cambie_la_idea():
    p = {"idea": "Un vaso", "idea_base": "Un vaso", "prompt": "A glass"}
    assert campanas.imagen_de(p) == "A glass"
    assert campanas.imagen_de({**p, "idea": "Dos vasos"}) == "Dos vasos"
    assert campanas.imagen_de({"idea": "Un vaso"}) == "Un vaso"


def test_cambiar_un_paso_borra_el_brief(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "brief"})
    assert client.portal.call(nueva_campana.leer, c).get("brief")
    paso(client, 5, visual=["productos"])  # sin cambios: el brief se queda
    assert client.portal.call(nueva_campana.leer, c).get("brief")
    paso(client, 5, visual=["personas"])
    assert not client.portal.call(nueva_campana.leer, c).get("brief")
    assert client.get(f"{URL}?paso=9").text.count('name="concepto"') == 0  # sin brief, vuelve al resumen


def test_con_brief_la_imagen_es_el_prompt_de_claude(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    foto = next(p for p in camp["posts"] if p["formato"] == "titular")
    client.post(campanas.url_de(camp), headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]), "idea": foto["idea"]})
    prompt = proveedor.llamadas[-1][0]
    assert prompt.startswith(foto["prompt"]) and "Photo style" not in prompt and "Brand context" not in prompt
    assert prompt.count("Do not include any text") == 1


def test_el_brief_se_guarda_y_se_descarga(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client, concepto="2", b2_insight="Nadie explica de dónde viene su café")
    camp = leer(client, c)
    u = campanas.url_de(camp)
    r = client.get(u)
    assert "Brief: Idea 3" in r.text and "Nadie explica de dónde viene su café" in r.text
    assert f'href="{u}/brief">↓ Descargar brief' in r.text
    r = client.get(f"{u}/brief")
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="brief-oto' in r.headers["content-disposition"]
    assert f'href="{u}/brief">Brief</a>' in client.get(BASE).text  # también desde la lista
    # En el historial (campañas viejas) el brief se queda.
    from igbot import informe
    assert informe.resumen_de(camp)["brief"]["titulo"] == "Idea 3"


def test_leer_el_plan_por_numero():
    plan = brief.leer_plan({"plan": [{"n": 2, "angulo": "B", "visual": "Vaso"}, {"n": 9, "angulo": "fuera"},
                                     {"n": "x"}, "nada", {"n": 1, "titular": "Sin ángulo ni visual"}]}, 3)
    assert plan[0] == {} and plan[1]["angulo"] == "B" and plan[1]["cta"] == "" and plan[2] == {}
    assert brief.plan_texto({"angulo": "<b>A</b>", "cta": "Ven"}) == "ángulo: &lt;b&gt;A&lt;/b&gt;; llamada a la acción: Ven"


def test_limite_de_propuestas_al_dia(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(campanas, "IA_POR_DIA", 0)  # sin límite (CAMPANA_IA_POR_DIA=0)
    for _ in range(3):
        client.portal.call(campanas.usar_ia, c)
    assert client.portal.call(campanas.ia_restantes, c) == campanas.SIN_LIMITE
    monkeypatch.setattr(campanas, "IA_POR_DIA", 2)
    assert client.portal.call(campanas.ia_restantes, c) == 0


def test_los_mensajes_dependen_del_objetivo(cliente, monkeypatch):
    """Los mensajes del paso 2 son los del objetivo elegido, ya escritos al guardar el Perfil de empresa: abrir el
    asistente no le pregunta nada a Claude. Cambiar de objetivo pide elegir los mensajes de nuevo."""
    from igbot import empresa
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    listo(client, c)

    def objetivo(k, titulo, mensajes):
        return {"k": k, "titulo": titulo, "descripcion": "d",
                "mensajes": [{"k": m, "titulo": f"Comunicar {m}", "descripcion": "x", "ejemplo": "e"} for m in mensajes]}
    opciones = {"version": empresa.VERSION_OPCIONES, "huella": "h",
                "objetivos": [objetivo("vender-latte", "Vender el latte", ["precio", "receta"]),
                              objetivo("mas-visitas", "Más visitas al local", ["ambiente", "horario"])],
                "mensajes": [{"k": "general", "titulo": "Comunicar general", "descripcion": "x"}]}
    client.portal.call(c.store.set, "campana_opciones", json.dumps(opciones))
    programadas = []
    monkeypatch.setattr(empresa, "programar_opciones", lambda c: programadas.append(c))

    paso(client, 1, objetivo="vender-latte")
    r = client.get(f"{URL}?paso=2")
    assert "Comunicar precio" in r.text and "Comunicar ambiente" not in r.text and "«Vender el latte»" in r.text
    paso(client, 2, mensajes=["precio"])
    assert client.portal.call(nueva_campana.leer, c)["mensajes"] == ["precio"]

    # Otro objetivo: sus mensajes, y el paso 2 se vuelve a elegir.
    paso(client, 1, objetivo="mas-visitas")
    b = client.portal.call(nueva_campana.leer, c)
    assert b["mensajes"] == [] and 2 not in b["hechos"]
    r = client.get(f"{URL}?paso=2")
    assert "Comunicar horario" in r.text and "Comunicar precio" not in r.text
    paso(client, 2, mensajes=["precio"])
    assert client.portal.call(nueva_campana.leer, c)["mensajes"] == []
    assert not programadas

    # Las de antes (sin mensajes por objetivo) se rehacen una vez en segundo plano; mientras, las generales.
    viejas = {"huella": "h", "objetivos": [{"k": "vender", "titulo": "Vender", "descripcion": "d"}],
              "mensajes": opciones["mensajes"]}
    client.portal.call(c.store.set, "campana_opciones", json.dumps(viejas))
    paso(client, 1, objetivo="vender")
    assert "Comunicar general" in client.get(f"{URL}?paso=2").text and programadas


def test_cada_foto_tiene_su_escena_segun_la_variedad():
    fichas = [{"formato": f, "ve": v} for f, v in [("foto", "productos"), ("texto", "productos"), ("titular", "personas"),
                                                    ("foto", "graficos"), ("collage", "productos")] * 4]
    assert brief.escenas(fichas, "reconocible") == [""] * len(fichas)  # Consistente: la da la dirección visual
    esc = brief.escenas(fichas, "coherencia", semilla=5)
    con_foto = [e for f, e in zip(fichas, esc) if f["formato"] in ("foto", "titular") and f["ve"] != "graficos"]
    assert all(con_foto) and len(set(con_foto)) == len(con_foto)  # encuadre × luz sin repetir
    assert all(not e for f, e in zip(fichas, esc) if f["formato"] in ("texto", "collage") or f["ve"] == "graficos")
    assert brief.escenas(fichas, "coherencia", semilla=5) == esc
    creativa = brief.escenas(fichas * 4, "experimental")
    assert any(e.split(", ")[0] in brief.EXPERIMENTALES for e in creativa)
    assert "Creativo" in brief.variedad_tag({"variedad": "experimental"})
    assert "Diverso" in brief.variedad_tag({"variedad": "diverso"})


def test_la_variedad_llega_al_brief_al_plan_y_a_los_posts(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    r = llenar(client)
    paso_6 = client.get(f"{URL}?paso=6").text
    assert "Variedad visual" in paso_6 and "Apariencia de las fotos" not in paso_6
    assert all(t in paso_6 for t in ("Consistente", "Equilibrado", "Diverso", "Creativo"))
    assert "tarjetas/campana/variedad-diverso.webp" in paso_6
    assert "Texto dentro de la imagen" not in paso_6 and "Cómo se arman" not in paso_6
    # Texto dentro de la imagen vive en Formatos, para Foto con titular: una, dos o las tres.
    paso_4 = client.get(f"{URL}?paso=4").text
    assert 'class="wiz-si titular"' in paso_4 and "tarjetas/campana/texto-info.webp" in paso_4
    assert 'name="textos_img" value="minimo" checked' in paso_4 and "Sin texto" not in paso_4
    assert "Elige cuánto texto" in paso(client, 4, ir=6, formatos=["titular"]).text
    paso(client, 4, ir=4, formatos=["foto"])  # sin titular no hace falta
    assert "Elige cuánto texto" not in client.get(f"{URL}?paso=4").text
    paso(client, 4, ir=4, formatos=["texto", "titular"], textos_img=["minimo", "info"])
    assert "Diseño de texto · Foto con titular (texto mínimo, más información)" in client.get(f"{URL}?paso=8").text
    generar(client)
    prompts = [p for p, *_ in llamadas]
    assert sum("<variedad_visual>" in p for p in prompts) >= 3  # brief, plan y posts
    camp = leer(client, c)
    assert camp["variedad"] == "coherencia"
    fotos = [p for p in camp["posts"] if p["formato"] == "titular"]
    assert fotos and all(p.get("escena") for p in fotos)
    assert all(not p.get("escena") for p in camp["posts"] if p["formato"] == "texto")
    assert any("| escena: " in p for p in prompts)
