"""Crear campaña por pasos: objetivo, mensaje (con producto), ideas, formatos, qué se ve, estilo, cantidad y
resumen; y en la campaña, el editor del post con su regla, subir su imagen y dos posts en un día."""
import io
import json
import os
import re

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
    assert 'class="cruce"' not in r.text  # Qué se ve está oculto: no hay cruce que mostrar

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
    prompt, sistema = next((p, s) for p, s in reversed(llamadas) if "Escribes los posts" in s)
    assert "<producto>Latte de calabaza ($75 MXN)" in prompt and "20 % de descuento" in prompt and "<regla>sí</regla>" in prompt
    assert "Concepto: Idea 2." in prompt and "Mensaje clave: Tostado &lt;b&gt;esta&lt;/b&gt; semana" in prompt
    assert "- El tueste:" in prompt and "ya tiene su brief" in sistema and "| pilar: " in prompt
    plan_prompt, plan_sistema = next((p, s) for p, s in llamadas if "director creativo" in s)  # los 6 posts juntos
    assert "<estilos_disponibles>" in plan_prompt and "<colores_disponibles>" in plan_prompt
    assert "director creativo" in plan_sistema and "6. 2099-" in plan_prompt and "Concepto: Idea 2." in plan_prompt
    assert "| plan: objetivo: Objetivo 1; ángulo: Ángulo 1; titular: Titular 1" in prompt
    camp = leer(client, c)
    assert camp["brief"]["titulo"] == "Idea 2" and not any(p.get("ve") for p in camp["posts"])
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
    assert "Idea de la imagen" in r.text and 'value="subir">Subir imagen' in r.text
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
    # La IA diseña la pieza completa con su texto dentro (sin plantilla encima).
    assert f"Design brief: {foto['prompt']}" in prompt and "infographic-style" in prompt
    assert f'Headline (largest): "{foto["titulo"]}"' in prompt
    assert "Photo style" not in prompt and "Brand context" not in prompt


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
    assert f'href="{u}/brief">Brief (PDF)' in r.text
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


def test_el_plan_va_en_tandas_y_pide_otra_vez_lo_que_falta(monkeypatch):
    import asyncio
    from igbot import agent

    llamadas, revisiones, falla = [], [], {"una": True}

    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        if "coherencia" in system:  # la revisión marca el post 2 de los que tienen foto
            revisiones.append(prompt)
            return json.dumps({"revisar": [{"n": 2, "motivo": "La imagen no muestra el asistente"}]})
        llamadas.append(prompt)
        n = len(re.findall(r"^\d+\. ", prompt.split("<posts>")[-1], re.M))
        if falla.pop("una", False):  # la primera respuesta llega cortada: JSON inválido
            return '{"plan": [{"n": 1, "angulo": "A"'
        return json.dumps({"plan": [{"n": i + 1, "angulo": f"A{i}", "foto": {"protagonista": f"P{i}",
                                                                              "escenario": "E"}} for i in range(n)]})

    monkeypatch.setattr(agent, "run_claude", fake)
    fichas = [{"dia": f"2099-01-{i + 1:02d}", "tipo": "ensena", "formato": "foto", "ve": "", "pilar": "", "dato": ""}
              for i in range(23)]
    asyncio.run(campanas.planear("<brief>b</brief>\n", {"nombre": "X", "inicio": "2099-01-01"}, fichas))
    assert all(f["plan"]["foto"]["protagonista"] for f in fichas)
    assert len(llamadas) == 5  # 10 + 10 + 3, otra vez los 10 de la tanda que falló y el post que no cuadró
    assert "<ya_planeados>" not in llamadas[1]  # la primera tanda no salió: no hay nada planeado
    assert llamadas[2].count("| plan: ") == 10  # la tercera ve lo que ya planeó la segunda
    assert "10. 2099-01-10" in llamadas[3] and llamadas[3].count("| plan: ") == 13
    assert len(revisiones) == 1 and revisiones[0].count("| imagen: ") == 23
    assert "<corregir>" in llamadas[4] and "motivo: La imagen no muestra el asistente" in llamadas[4]
    assert "1. 2099-01-02" in llamadas[4].split("<posts>")[-1]  # el segundo post, planeado otra vez
    assert brief.leer_revision({"revisar": [{"n": 9, "motivo": "x"}, {"n": "a"}, {"n": 1, "motivo": " y "}]}, 3) == {0: "y"}


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


def test_historias_de_punta_a_punta(cliente, monkeypatch):
    """Número de historias en Cantidad y fechas: Claude las escribe sin caption, salen en 9:16, se distinguen en
    el calendario y se publican como historia."""
    from pathlib import Path

    from igbot import admin

    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 20)
    import app as bot
    base = claude_de_campana([])

    async def con_medio(prompt, system, **kw):
        respuesta = await base(prompt, system, **kw)
        if "<posts>" in prompt and '"posts"' in respuesta:  # cada post dice su medio, como pide el sistema
            datos = json.loads(respuesta)
            lineas = [x for x in prompt.split("<posts>")[-1].splitlines() if x.startswith("- ")]
            for x, linea in zip(datos["posts"], lineas):
                x["medio"] = "historia" if "medio: historia" in linea else "post"
            respuesta = json.dumps(datos)
        return respuesta

    monkeypatch.setattr(bot.agent, "run_claude", con_medio)
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    r = client.get(f"{URL}?paso=7")
    assert 'name="n_historias"' in r.text and "Número de historias" in r.text
    r = paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="6", n_historias="10",
             dias=["0", "2", "4"], horarios=["manana"])
    assert "caben hasta 9 historias" in r.text  # tres por día
    paso(client, 7, nombre="Otoño", inicio=LUNES.isoformat(), semanas="1", n_posts="6", n_historias="3",
         dias=["0", "2", "4"], horarios=["manana"])
    assert "6 posts y 3 historias" in client.get(f"{URL}?paso=8").text
    generar(client)

    camp = leer(client, c)
    historias = [p for p in camp["posts"] if campanas.es_historia(p)]
    assert camp["n_historias"] == 3 and len(historias) == 3 and len(camp["posts"]) == 9
    assert all(p["caption"] == "" and not p.get("regla") for p in historias)  # sin texto publicado ni regla
    assert all(p.get("regla") for p in camp["posts"] if not campanas.es_historia(p))  # busca interacción
    u = campanas.url_de(camp)
    r = client.get(u)
    assert r.text.count('class="medio-cam m-historia">Historia</span>') == 3 and "6 posts y 3 historias" in r.text

    # La ventana de una historia: sin el texto que se publica ni la regla de comentarios.
    h = next(p for p in historias if p["formato"] == "titular") if any(
        p["formato"] == "titular" for p in historias) else historias[0]
    r = client.get(f"{u}?post={h['k']}")
    assert "Es una historia" in r.text and "Texto que se publica" not in r.text and 'name="regla_activa"' not in r.text

    # Su imagen sale en 9:16 y el post queda como historia.
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(h["k"])})
    post_id = campanas.buscar_post(leer(client, c), h["k"])["post_id"]
    post = client.portal.call(c.store.post, post_id)
    assert post["tipo"] == "historia" and post["caption"] == ""
    token = client.portal.call(c.store.media, post["media_ids"][0])[0]
    assert Image.open(Path(c.media_dir) / f"{token}.jpg").size == (1080, 1920)

    # En Contenido › Calendario dice que es historia, con su miniatura.
    r = client.get(f"/panel/contenido?vista=semana&fecha={LUNES.isoformat()}")
    assert '<span class="m-historia">Historia</span>' in r.text and f'href="/panel/contenido/post/{post_id}"' in r.text

    # Al publicarse va como historia (media_type=STORIES), no como post.
    class IG:
        def __init__(self):
            self.historias, self.posts = [], []

        async def publish_story(self, url):
            self.historias.append(url)
            return ""

        async def publish(self, urls, caption):
            self.posts.append(urls)
            return "https://www.instagram.com/p/X/"

    ig = IG()

    async def publicar():
        from igbot.cuenta import usar
        with usar(c):
            return await admin.publish_post(c.store, ig, await c.store.post(post_id))

    monkeypatch.setattr(admin, "save_photo", lambda *a: _nada())
    assert client.portal.call(publicar) == ("", "")
    assert len(ig.historias) == 1 and ig.posts == []
    assert client.portal.call(c.store.post, post_id)["status"] == "publicada"


async def _nada():
    return None


def test_sugeridos_del_lunes(cliente, monkeypatch):
    """Cada lunes en la mañana se agregan objetivos y mensajes sugeridos (una vez por semana), con brillitos lima."""
    from datetime import datetime

    from igbot import agent, empresa, sugeridos, tools
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    listo(client, c)
    base = {"version": empresa.VERSION_OPCIONES, "huella": "h", "mensajes": [{"k": "general", "titulo": "General"}],
            "objetivos": [{"k": "vender-latte", "titulo": "Vender el latte", "descripcion": "d",
                           "mensajes": [{"k": "precio", "titulo": "El precio", "descripcion": "x", "ejemplo": "e"}]}]}
    client.portal.call(c.store.set, "campana_opciones", json.dumps(base))
    llamadas = []

    async def fake(prompt, system, **kw):
        llamadas.append(prompt)
        return json.dumps({
            "objetivos": [{"k": "llenar-tardes", "titulo": "Llenar las tardes", "descripcion": "Más gente de 4 a 7",
                           "mensajes": [{"k": "promo-tarde", "titulo": "La hora de la tarde", "descripcion": "x"},
                                        {"k": "ambiente", "titulo": "El ambiente tranquilo", "descripcion": "x"}]},
                          {"k": "repetido", "titulo": "Vender el latte", "descripcion": "ya lo tiene",
                           "mensajes": [{"k": "a", "titulo": "A"}, {"k": "b", "titulo": "B"}]}],
            "mensajes": {"vender-latte": [{"k": "receta", "titulo": "La receta de la casa", "descripcion": "x"},
                                          {"k": "precio-otra", "titulo": "El precio", "descripcion": "repetido"}],
                         "no-existe": [{"k": "z", "titulo": "Z"}]}})

    monkeypatch.setattr(agent, "run_claude", fake)
    # Domingo: todavía no; lunes antes de las 8: tampoco.
    for dia in (datetime(2099, 1, 4, 10), datetime(2099, 1, 5, 7)):
        monkeypatch.setattr(tools, "now", lambda d=dia: d)
        client.portal.call(sugeridos.tick, c)
    assert not llamadas
    monkeypatch.setattr(tools, "now", lambda: datetime(2099, 1, 5, 8, 30))
    client.portal.call(sugeridos.tick, c)
    client.portal.call(sugeridos.tick, c)  # una sola vez por semana
    assert len(llamadas) == 1 and "<ya_tiene>" in llamadas[0] and "Vender el latte" in llamadas[0]
    sug = client.portal.call(sugeridos.de, c)
    assert [o["k"] for o in sug["objetivos"]] == ["sug-llenar-tardes"]  # el repetido no entra
    assert [m["titulo"] for m in sug["mensajes"]["vender-latte"]] == ["La receta de la casa"]
    assert "no-existe" not in sug["mensajes"]

    # En el asistente: al final y con los brillitos de "Sugerido".
    r = client.get(f"{URL}?paso=1")
    assert r.text.index("Vender el latte") < r.text.index("Llenar las tardes")
    assert r.text.count("class=sug-ia") == 1 and "Sugerido" in r.text
    paso(client, 1, objetivo="vender-latte")
    r = client.get(f"{URL}?paso=2")
    assert "El precio" in r.text and "La receta de la casa" in r.text and r.text.count("class=sug-ia") == 1
    paso(client, 1, objetivo="sug-llenar-tardes")
    r = client.get(f"{URL}?paso=2")
    assert "La hora de la tarde" in r.text and "El precio" not in r.text and r.text.count("class=sug-ia") == 2

    # Sin contenido en su plan no corre.
    monkeypatch.setattr(c, "imagenes_mes", 0)
    monkeypatch.setattr(tools, "now", lambda: datetime(2099, 1, 12, 9))
    client.portal.call(sugeridos.tick, c)
    assert len(llamadas) == 1


def prompts_para(datos: str, texto) -> dict:
    """La respuesta del director de arte: un prompt por cada <post n="…"> que le llegó."""
    return {"prompts": [{"n": int(n), "prompt": texto(n) if callable(texto) else texto}
                        for n in re.findall(r'<post n="(\d+)">', datos)]}


def openai_de_campana(llamadas, fake_claude):
    """OpenAI de texto falso: los posts como los escribe el Claude falso, y el prompt de cada imagen."""
    async def fake(sistema, prompt, max_tokens=16000):
        llamadas.append((sistema, prompt))
        if "director de arte" in sistema:
            return prompts_para(prompt, "A detailed art-directed prompt")
        return json.loads(await fake_claude(prompt, sistema))
    return fake


def test_flujo_brief_y_plan_con_claude_posts_e_imagen_con_openai(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    de_claude, de_openai = [], []
    import app as bot
    from igbot import config, openai_texto
    fake_claude = claude_de_campana(de_claude)
    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-prueba")
    monkeypatch.setattr(openai_texto, "json_de", openai_de_campana(de_openai, claude_de_campana([])))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    client.post(URL, headers=ORIGEN, data={"paso": "8", "accion": "brief"})
    r = client.get(f"{URL}?paso=9")
    assert "Cómo se ve" in r.text and 'name="b0_estetica"' in r.text and 'name="b0_limites_visuales"' in r.text
    client.post(URL, headers=ORIGEN, data={"paso": "9", "accion": "proponer", "b0_estetica": "Fotos cálidas con textura de papel"})
    sistemas_claude = [s for _, s in de_claude]
    assert any("estratega de contenido" in s for s in sistemas_claude)  # 1. concepto + dirección visual
    assert any("director creativo" in s for s in sistemas_claude)  # 2. plan editorial
    assert not any("Escribes los posts" in s for s in sistemas_claude)  # 3. los posts ya no van a Claude
    assert any("Escribes los posts" in s for s, _ in de_openai)
    camp = leer(client, c)
    assert camp["brief"]["estetica"] == "Fotos cálidas con textura de papel"
    assert "Estética y recursos: Fotos cálidas con textura de papel" in brief.texto(camp["brief"])
    # 4. el prompt de la imagen lo escribe OpenAI al pedirla, con la dirección visual; se guarda mientras no cambie la idea.
    foto = next(p for p in camp["posts"] if p["formato"] == "titular")
    u = campanas.url_de(camp)
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]), "idea": "Una taza en la barra"})
    arte = [d for s, d in de_openai if "director de arte" in s]
    # Al cambiar la idea se reescribe solo el de ese post, comparado con los demás y con la idea del dueño mandando
    # (la primera llamada es la de los posts que no traían prompt al crear la campaña).
    assert len(arte) == 2
    arte = arte[1:]
    assert "Fotos cálidas con textura de papel" in arte[0] and arte[0].count('<post n="') == 1
    assert "idea del dueño (manda): Una taza en la barra" in arte[0] and "Protagonista" in arte[0]
    assert "pieza: diseño completo estilo infografía" in arte[0] and "<otros_posts>" in arte[0] and "Café Lunar" in arte[0]
    assert "Design brief: A detailed art-directed prompt" in proveedor.llamadas[-1][0]
    p = campanas.buscar_post(leer(client, c), foto["k"])
    assert p["prompt"] == "A detailed art-directed prompt" and p["idea_base"] == "Una taza en la barra"
    r = client.get(f"{u}?post={foto['k']}")
    assert 'name="prompt"' in r.text and ">A detailed art-directed prompt</textarea>" in r.text
    # El prompt editado a mano es el que va al generador, sin volver a escribirlo.
    llamadas_arte = len([d for s, d in de_openai if "director de arte" in s])
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]), "idea": "Una taza en la barra",
                                         "prompt": "My own prompt, a cup on the bar"})
    assert "Design brief: My own prompt, a cup on the bar" in proveedor.llamadas[-1][0]
    assert len([d for s, d in de_openai if "director de arte" in s]) == llamadas_arte
    # "Escribir el prompt" lo escribe sin generar la imagen.
    otro = next(p for p in leer(client, c)["posts"] if p["formato"] == "titular" and p["k"] != foto["k"])
    n_imagenes = len(proveedor.llamadas)
    client.post(u, headers=ORIGEN, data={"accion": "prompt", "k": str(otro["k"]), "idea": "Granos en la mano"})
    assert len(proveedor.llamadas) == n_imagenes
    assert campanas.buscar_post(leer(client, c), otro["k"])["prompt"] == "A detailed art-directed prompt"


def test_si_openai_falla_el_prompt_lo_escribe_claude(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    from igbot import config, openai_texto
    fake_claude = claude_de_campana([])

    async def claude(prompt, system, **k):
        if "director de arte" in system:
            return json.dumps(prompts_para(prompt, "Prompt from Claude"))
        return await fake_claude(prompt, system, **k)

    async def falla(sistema, prompt, max_tokens=16000):
        if "director de arte" in sistema:
            raise RuntimeError("modelo no disponible")
        return json.loads(await fake_claude(prompt, sistema))

    monkeypatch.setattr(bot.agent, "run_claude", claude)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-prueba")
    monkeypatch.setattr(openai_texto, "json_de", falla)
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    foto = next(p for p in camp["posts"] if p["formato"] == "titular")
    client.post(campanas.url_de(camp), headers=ORIGEN, data={"accion": "generar", "k": str(foto["k"]), "idea": "Otra idea"})
    assert "Design brief: Prompt from Claude" in proveedor.llamadas[-1][0]


def test_lo_que_hay_que_verificar_sale_en_el_post():
    fichas = [{"dia": "2099-01-05", "hora": "09:00", "tipo": "producto", "formato": "texto", "rol": "", "ve": ""}]
    posts = campanas.leer_posts({"posts": [{"dia": "2099-01-05", "titulo": "Hola", "caption": "Texto",
                                            "verificar": ["Precio de 75 MXN", "  "]}]}, fichas, 1, False, 3, 0)
    assert posts[0]["verificar"] == ["Precio de 75 MXN"]
    html = campanas.plan_de_post({**posts[0], "plan": {"angulo": "A", "gancho": "Para"}})
    assert "Revisa antes de publicar" in html and "Precio de 75 MXN" in html and "Gancho" in html


def test_los_documentos_de_cada_etapa_se_descargan(cliente, monkeypatch):
    import io
    import zipfile
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    u = campanas.url_de(camp)
    assert f'href="{u}/documentos"' in client.get(u).text
    r = client.get(f"{u}/documentos")
    assert r.headers["content-type"] == "application/zip" and 'filename="proceso-oto' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.namelist() == ["1-brief.md", "2-plan-editorial.md", "3-posts.md", "4-prompts-de-imagen.md"]
    assert "# Brief: Idea 1" in z.read("1-brief.md").decode() and "Cómo se ve" in z.read("1-brief.md").decode()
    plan = z.read("2-plan-editorial.md").decode()
    assert "**Ángulo:** Ángulo 1" in plan and "**Dato:**" in plan
    assert "Texto 1 #cafe" in z.read("3-posts.md").decode()
    assert "**Prompt:** Photo" in z.read("4-prompts-de-imagen.md").decode()


def test_los_prompts_de_imagen_ya_vienen_escritos_al_crear_la_campana(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    from igbot import config, openai_texto
    fake_claude, arte = claude_de_campana([]), []
    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)

    async def openai(sistema, prompt, max_tokens=16000):
        if "director de arte" in sistema:
            arte.append(prompt)
            return prompts_para(prompt, lambda n: f"Detailed prompt {n}")
        datos = json.loads(await fake_claude(prompt, sistema))
        for x in datos.get("posts", []):
            x.pop("prompt", None)  # los posts ya no traen prompt: lo escribe el último paso
        return datos

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-prueba")
    monkeypatch.setattr(openai_texto, "json_de", openai)
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    fotos = [p for p in camp["posts"] if p["formato"] == "titular"]
    assert fotos and len(arte) == 1 and not proveedor.llamadas  # todos juntos, sin gastar imágenes
    assert arte[0].count('<post n="') == sum(1 for p in camp["posts"] if campanas.con_foto(p))
    assert all(p["prompt"].startswith("Detailed prompt") and p["idea_base"] == p["idea"] for p in fotos)
    assert all(p.get("prompt") for p in camp["posts"] if p["formato"] == "texto")  # también los diseños de texto
    r = client.get(f"{campanas.url_de(camp)}?post={fotos[0]['k']}")
    assert f">{fotos[0]['prompt']}</textarea>" in r.text


def test_la_idea_de_la_imagen_sale_del_plan_visual():
    foto = {"protagonista": "Una barista", "accion": "sirve un latte", "escenario": "La barra de la tienda",
            "encuadre": "plano medio", "luz": "luz de ventana"}
    fichas = [{"dia": "2099-01-05", "hora": "09:00", "tipo": "producto", "formato": "titular", "rol": "", "ve": "",
               "plan": {"angulo": "A", "foto": foto}},
              {"dia": "2099-01-06", "hora": "09:00", "tipo": "producto", "formato": "texto", "rol": "", "ve": "",
               "plan": {"angulo": "B", "foto": foto}}]
    posts = campanas.leer_posts({"posts": [{"dia": "2099-01-05", "titulo": "Hola", "caption": "Texto", "idea": ""},
                                           {"dia": "2099-01-06", "titulo": "Dos", "caption": "Texto", "idea": ""}]},
                                fichas, 1, False, 3, 0)
    assert posts[0]["idea"] == ("Una barista sirve un latte, en la barra de la tienda. plano medio. luz de ventana.")
    assert posts[0]["idea_original"] == posts[0]["idea"] and posts[1]["idea"] == posts[0]["idea"]  # el diseño de texto también
    assert not campanas.idea_editada(posts[0]) and campanas.idea_editada({**posts[0], "idea": "Otra cosa"})
    html = campanas.plan_de_post(posts[0])
    assert "Plan de la imagen" in html and "Protagonista:</b> Una barista" in html


def test_cada_post_usa_su_estilo_y_su_color():
    marca = {"estilos": ["minimalista", "editorial"], "usos_color": ["sutil", "protagonista"]}
    camp = {"brief": {}, "producto": "", "posts": []}
    opciones = campanas.opciones_visuales(camp, marca)
    assert "Minimalista (principal)" in opciones and "Editorial (secundario)" in opciones
    assert "- Sutil:" in opciones and "- Protagonista:" in opciones and "Equilibrado" not in opciones
    p = {"k": 3, "formato": "titular", "idea": "x", "titulo": "Hola",
         "plan": {"foto": {"protagonista": "Una barista", "estilo": "Editorial", "color": "Protagonista"}}}
    bloque = campanas.bloque_imagen(camp, marca, p)
    assert '<post n="3">' in bloque and "estilo: Editorial. High-end editorial" in bloque
    assert "color: Protagonista. Brand colors define the image" in bloque and "Minimalist" not in bloque
    # Si el plan no eligió (o eligió algo que no existe), va el preferido.
    otro = campanas.bloque_imagen(camp, marca, {**p, "plan": {"foto": {"protagonista": "Granos", "estilo": "Otro"}}})
    assert "estilo: Minimalista." in otro and "color: Sutil." in otro
    libre = campanas.opciones_visuales({**camp, "modo_img": "libre"}, {**marca, "estilo_propio": "Fotos con grano"})
    assert "Diseño libre" in libre and "Fotos con grano" in libre


def test_otra_idea_trae_otro_plan_visual_y_otro_prompt(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    from igbot import config, openai_texto
    fake_claude, arte = claude_de_campana([]), []
    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-prueba")
    monkeypatch.setattr(openai_texto, "json_de", openai_de_campana(arte, fake_claude))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    foto = next(p for p in camp["posts"] if p["formato"] == "titular")
    client.post(campanas.url_de(camp), headers=ORIGEN, data={"accion": "otra", "k": str(foto["k"])})
    p = campanas.buscar_post(leer(client, c), foto["k"])
    assert p["plan"]["foto"]["protagonista"] == "Una barista" and p["idea"].startswith("Una barista muele café")
    assert p["idea_original"] == p["idea"] and p["prompt"] == "A detailed art-directed prompt"
    otra = next(d for s, d in arte if "otra versión" in s)
    assert "<estilos_disponibles>" in otra and "<otras_fotos>" in otra


def test_las_piezas_con_texto_y_el_collage_los_genera_la_ia(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana([]))
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    generar(client)
    camp = leer(client, c)
    u = campanas.url_de(camp)
    texto = next(p for p in camp["posts"] if p["formato"] == "texto")
    assert campanas.costo(texto) == 1 and campanas.con_foto(texto)
    texto["plan"]["foto"]["textos"] = ["Tueste de esta semana", "Desde 75 MXN"]
    texto["prompt"], texto["idea_base"] = "A bold typographic layout", texto["idea"]
    collage = {**texto, "k": 99, "formato": "collage", "titulo": "Collage"}
    camp["posts"].append(collage)
    client.portal.call(c.store.set, "campanas", json.dumps([camp]))
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": str(texto["k"])})
    prompt = proveedor.llamadas[-1][0]
    assert "infographic-style" in prompt and f'Headline (largest): "{texto["titulo"]}"' in prompt
    assert '"Tueste de esta semana"' in prompt and '"Desde 75 MXN"' in prompt
    # El collage: fotos nuevas de la IA, sin texto y sin pedir fotos subidas en Tu marca.
    client.post(u, headers=ORIGEN, data={"accion": "generar", "k": "99"})
    prompt = proveedor.llamadas[-1][0]
    assert prompt.startswith("A single image laid out as a photo collage") and "Do not include any text" in prompt


def test_las_fotos_de_referencia_mandan_en_la_direccion_visual(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 10)
    import app as bot
    from igbot import config, openai_texto
    de_claude, de_openai = [], []
    fake_claude = claude_de_campana(de_claude)
    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-prueba")
    vistas = []
    base = openai_de_campana(de_openai, fake_claude)

    async def openai(sistema, prompt, max_tokens=16000, imagenes=()):
        if imagenes:
            vistas.append(len(imagenes))
            return {"luz": "Luz dura de mediodía", "paleta": "Terracota y crema", "copiar": "Sombras marcadas",
                    "no_copiar": "La taza azul"}
        return await base(sistema, prompt, max_tokens)

    monkeypatch.setattr(openai_texto, "json_de", openai)
    listo(client, c)
    client.portal.call(c.store.set, "productos", json.dumps([PRODUCTO]))
    llenar(client)
    foto = io.BytesIO()
    Image.new("RGB", (40, 40), "orange").save(foto, "JPEG")
    r = client.post(URL, headers=ORIGEN, data={"paso": "6", "variedad": "coherencia", "ir": "6"},
                    files=[("ref_nuevas", ("a.jpg", foto.getvalue(), "image/jpeg")),
                           ("ref_nuevas", ("b.jpg", foto.getvalue(), "image/jpeg"))])
    r = client.get(f"{URL}?paso=6")
    assert 'enctype="multipart/form-data"' in r.text and r.text.count('name="referencias"') == 2
    assert r.text.count('name="referencias" value=') == r.text.count("checked><img")  # las subidas quedan elegidas
    generar(client)
    assert vistas == [2]  # el ADN se lee una vez, con las dos fotos
    conceptos = next(p for p, s in de_claude if "estratega de contenido" in s)
    assert "<referencias_visuales>" in conceptos and "Luz: Luz dura de mediodía" in conceptos
    plan = next(p for p, s in de_claude if "director creativo" in s)
    assert "<referencias_visuales>" in plan and "- Referencias (principal)" in plan
    camp = leer(client, c)
    assert len(camp["referencias"]) == 2 and "Paleta: Terracota y crema" in camp["adn_visual"]
    # En cada imagen, las fotos de referencia van al generador con la instrucción de copiar su estilo.
    p = next(p for p in camp["posts"] if p["formato"] == "titular")
    client.post(campanas.url_de(camp), headers=ORIGEN, data={"accion": "generar", "k": str(p["k"])})
    prompt, fotos = proveedor.llamadas[-1][:2]
    assert len(fotos) >= 2 and "The first 2 reference images are the visual style to follow" in prompt


def test_crear_con_ia_solo_pide_objetivo_y_fechas(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 4)
    llamadas = []
    import app as bot
    monkeypatch.setattr(bot.agent, "run_claude", claude_de_campana(llamadas))
    rapida = f"{BASE}/rapida"
    # Sin empresa ni marca, lo mismo que el asistente: pide completarlas.
    assert "Necesitamos conocer tu negocio" in client.get(rapida).text
    listo(client, c)
    assert f'href="{rapida}">' not in client.get("/panel/contenido").text  # en Calendario ya no: es de Campañas

    r = client.get(rapida)
    assert r.text.count('name="objetivo"') >= 2 and 'checked' in r.text  # uno ya marcado
    assert 'name="inicio"' in r.text and 'name="semanas"' in r.text and 'name="texto"' in r.text
    assert 'name="mensajes"' not in r.text and 'name="formatos"' not in r.text

    r = client.post(rapida, headers=ORIGEN, data={"objetivo": "no-existe", "inicio": LUNES.isoformat()})
    assert "Elige qué quieres lograr." in r.text and not llamadas

    r = client.post(rapida, headers=ORIGEN, data={"objetivo": "vender", "inicio": LUNES.isoformat(), "semanas": "1",
                                                  "texto": "20 % de descuento en noviembre"})
    assert "Creando tu campaña con IA" in r.text, r.text[-500:]
    camp = leer(client, c)
    assert camp["brief"] and len(camp["posts"]) == nueva_campana.POSTS_SEMANA
    assert not any(p.get("post_id") for p in camp["posts"])  # sin imágenes
    assert "20 % de descuento en noviembre" in llamadas[0][0]  # lo que escribió llega al brief
    assert camp["asistente"]["objetivo"] == "vender" and camp["asistente"]["mensajes"]
