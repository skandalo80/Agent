"""Banco de recetas de anuncio: se eligen por tipo de producto y estilo de Tu marca, distintas entre sí, y solo
entran al prompt cuando la campaña no trae fotos de referencia."""
import json

from igbot import campanas, recetas
from igbot.cuenta import usar
from test_contenido import cliente  # noqa: E402

assert cliente  # el fixture de test_contenido


def test_recetas_distintas_por_tipo_y_estilo():
    """`para` da n recetas distintas que sirven para ese tipo y alguno de esos estilos; `saltar` da otras."""
    for tipo in recetas.TIPOS:
        for estilo in sorted(recetas.TODOS):
            elegidas = recetas.para(tipo, [estilo], 3, semilla=7)
            assert len(elegidas) == 3 and len({k for k, _ in elegidas}) == 3
            for k, texto in elegidas:
                r = next(r for r in recetas.RECETAS if r[0] == k)
                assert tipo in r[1] and texto == r[3]
            assert all(k in recetas.CLAVES for k, _ in elegidas)
    primeras = recetas.para("fisico", ["pop"], 3, semilla=1)
    assert recetas.para("fisico", ["pop"], 3, semilla=1) == primeras  # mismo orden con la misma semilla
    assert recetas.para("fisico", ["pop"], 3, semilla=1, saltar=1) != primeras
    # pocas recetas para "software" con lujo: se completan con las que sirven para cualquier estilo
    assert len(recetas.para("software", ["lujo"], 3)) == 3
    assert recetas.para("otro", ["inventado"], 2)  # tipo y estilo desconocidos: fisico + minimalista
    # Sin estilo (None) entran las de todo el banco para ese tipo: también las gráficas con una marca sobria.
    todas = {k for k, _ in recetas.para("fisico", None, len(recetas.RECETAS), semilla=3)}
    assert "sticker" in todas and "bodegon" in todas and "pantalla" not in todas


def test_las_fotos_del_producto_son_solo_el_objeto():
    """El generador toma de las fotos del producto el artículo, nunca su fondo, luz o utilería (dueño 2026-11-05);
    y en un anuncio con recetas, las tres versiones siguen receta, no el plan visual."""
    from igbot import imagenes
    prompt = imagenes.prompt_anuncio("idea", imagenes.Texto("T", "S", "", "@x"), con_producto=True)
    assert "Take only the product from those photos: not their background" in prompt
    assert "not their background" not in imagenes.prompt_anuncio("idea", imagenes.Texto("T", "S", "", "@x"))
    assert "ninguna traduce el plan visual tal cual" in campanas.SISTEMA_PROMPTS
    assert "del plan visual se toman solo el protagonista real" in campanas.SISTEMA_PROMPTS


def test_las_recetas_segun_la_variedad_visual():
    """Consistente: las tres del perfil (los estilos de Tu marca). Equilibrado: dos del perfil y una del banco.
    Diverso: una del perfil y dos del banco. Creativo: de todo el banco. `saltar` sigue sin repetir; con n = 1
    cada post de contenido toma la siguiente del mismo ciclo (dueño 2026-11-05)."""
    def perfil(k):
        return "minimalista" in next(r for r in recetas.RECETAS if r[0] == k)[2]

    def claves(**kw):
        return [k for k, _ in recetas.para("fisico", ["minimalista"], 3, semilla=5, **kw)]

    for variedad, patron in (("reconocible", "PPP"), ("coherencia", "PPB"), ("diverso", "PBB")):
        for saltar in (0, 1):
            elegidas = claves(variedad=variedad, saltar=saltar)
            assert len(set(elegidas)) == 3
            assert [perfil(k) for k in elegidas] == [letra == "P" for letra in patron], (variedad, saltar)
        assert not set(claves(variedad=variedad)) & set(claves(variedad=variedad, saltar=1))  # sigue, no repite
    assert len(set(claves(variedad="experimental"))) == 3
    assert claves(variedad="experimental") != claves(variedad="reconocible")
    assert claves() == claves(variedad="coherencia")  # la de siempre
    # n = 1 (posts de contenido): los posts 0, 1 y 2 siguen el mismo ciclo que las versiones de un anuncio.
    uno = [recetas.para("fisico", ["minimalista"], 1, semilla=5, saltar=k, variedad="diverso")[0][0] for k in range(3)]
    assert uno == claves(variedad="diverso")
    # Pocas del perfil (software con lujo): se completa el perfil con el banco y sigue dando tres distintas.
    assert len({k for k, _ in recetas.para("software", ["lujo"], 3, variedad="reconocible")}) == 3


def test_los_posts_de_contenido_llevan_su_receta(cliente, monkeypatch):
    """Una campaña de contenido (sin producto) también lleva receta: una por post con plan visual, ninguna en el
    dato curioso ni en Solo texto, y ninguna con fotos de referencia."""
    client, c, *_ = cliente
    import app as bot
    datos = []

    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        datos.append(prompt)
        return json.dumps({"prompts": [{"n": k, "prompt": "A"} for k in (1, 2, 3, 4)]})

    monkeypatch.setattr(bot.agent, "run_claude", fake)
    foto = {"funcion": "f", "protagonista": "p", "escenario": "e", "accion": "a", "encuadre": "x", "luz": "l",
            "estilo": "Minimalista", "color": "Sutil"}

    def post(k, formato, tipo="producto"):
        return {"k": k, "formato": formato, "tipo": tipo, "idea": "i", "titulo": "T", "subtitulo": "", "caption": "c",
                "dia": f"2099-01-0{k}", "hora": "09:00", "plan": {"foto": foto}}

    def campana():
        return {"id": 1, "brief": {"titulo": "B", "direccion_visual": "luz"}, "semilla": 3, "variedad": "diverso",
                "posts": [post(1, "foto"), post(2, "titular"), post(3, "foto", "dato"), post(4, "texto")]}

    with usar(c):
        camp = campana()
        client.portal.call(campanas.prompts_de, c, camp, camp["posts"], {"estilos": ["lujo"]})
    bloques = datos[-1].split("<post n=")
    assert "receta: " in bloques[1] and "receta: " in bloques[2]
    assert bloques[1].split("receta: ")[1][:40] != bloques[2].split("receta: ")[1][:40]  # distintas por post
    assert "receta" not in bloques[3] and "receta" not in bloques[4]
    with usar(c):
        camp = campana() | {"adn_visual": "luz", "referencias": ["t1"]}
        client.portal.call(campanas.prompts_de, c, camp, camp["posts"], {"estilos": ["lujo"]})
    assert "receta" not in datos[-1]
    assert 'Si el post trae "receta"' in campanas.SISTEMA_PROMPTS


def test_tipo_de_producto():
    assert recetas.tipo_de({"tipo": "producto"}, "Tecnología y software") == "software"
    assert recetas.tipo_de({"tipo": "servicio"}, "Restaurante o cafetería") == "local"
    assert recetas.tipo_de({"tipo": "servicio"}, "Belleza y estética") == "servicio"
    assert recetas.tipo_de({"tipo": "producto"}, "Alimentos y bebidas") == "comida"
    assert recetas.tipo_de(None, "Joyería") == "fisico"


def test_las_recetas_entran_al_prompt_del_anuncio_sin_referencias(cliente, monkeypatch):
    """El anuncio lleva sus recetas (una por versión) cuando la campaña no tiene fotos de referencia; con ADN de
    referencias no las lleva, y el post de foto nunca."""
    client, c, *_ = cliente
    import app as bot
    datos = []

    async def fake(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        datos.append(prompt)
        return json.dumps({"prompts": [{"n": 1, "prompt": "A", "versiones": ["A", "B", "C"]}, {"n": 2, "prompt": "F"}]})

    monkeypatch.setattr(bot.agent, "run_claude", fake)
    monkeypatch.setattr(campanas.config, "OPCIONES_ANUNCIO", 3)
    foto = {"funcion": "f", "protagonista": "p", "escenario": "e", "accion": "a", "encuadre": "x", "luz": "l",
            "estilo": "Minimalista", "color": "Sutil"}

    def campana():
        return {"id": 1, "brief": {"titulo": "B", "direccion_visual": "luz"}, "producto": "Latte", "producto_id": "p1",
                "semilla": 3,
                "posts": [{"k": 1, "formato": "anuncio", "tipo": "producto", "idea": "i1", "titulo": "T", "subtitulo": "",
                           "caption": "c", "dia": "2099-01-05", "hora": "09:00", "plan": {"foto": foto}},
                          {"k": 2, "formato": "foto", "tipo": "producto", "idea": "i2", "titulo": "T2", "caption": "c",
                           "dia": "2099-01-06", "hora": "09:00", "plan": {"foto": foto}}]}

    with usar(c):
        client.portal.call(c.store.set, "productos", json.dumps([{"id": "p1", "nombre": "Latte", "tipo": "producto"}]))
        camp = campana()
        client.portal.call(campanas.prompts_de, c, camp, camp["posts"], {"estilos": ["pop"]})
    anuncio, foto_post = datos[-1].split('<post n="2">')
    assert "recetas (una por versión, en este orden): 1) " in anuncio and " 2) " in anuncio and " 3) " in anuncio
    assert "recetas" not in foto_post
    with usar(c):
        camp = campana() | {"adn_visual": "luz cálida", "referencias": ["t1"]}
        client.portal.call(campanas.prompts_de, c, camp, camp["posts"], {"estilos": ["pop"]})
    assert "recetas" not in datos[-1]
    assert 'Si el <post> trae "recetas"' in campanas.SISTEMA_PROMPTS
