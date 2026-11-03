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
