"""Comentarios con palabra clave -> DM (sin red ni Claude)."""
import asyncio

from igbot.cuenta import cuenta  # noqa: E402
from igbot import comentarios as c  # noqa: E402
from igbot.store import Store  # noqa: E402

POST = "https://www.instagram.com/p/DAbc123/?igsh=xyz"
RULES = [  # las palabras clave de los posts que el cliente armó en su panel (contenido.reglas)
    c.Rule(post="DAbc123", keywords=["PRECIO"], dm="Nuestros precios: ..."),
    c.Rule(post="DAbc123", keywords=["catálogo", "catalogo"], dm="Aquí el catálogo: ..."),
    c.Rule(post="RRR999", keywords=["info"], dm="Info del reel"),
]


def test_el_shortcode_sale_del_link():
    assert c.shortcode(POST) == "DAbc123"
    assert c.shortcode("https://www.instagram.com/reel/RRR999/") == "RRR999"
    assert c.shortcode("") == ""


def test_solo_reaccionan_las_publicaciones_con_reglas():
    assert len(c.for_post(RULES, "DAbc123")) == 2
    assert c.for_post(RULES, "OTRA") == []
    assert c.for_post(RULES, "") == []


def test_palabras_sin_importar_mayusculas_acentos_ni_plurales():
    rules = c.for_post(RULES, "DAbc123")
    assert [r.keywords[0] for r in c.literal_matches(rules, "Precios porfa!!")] == ["PRECIO"]
    assert [r.keywords[0] for r in c.literal_matches(rules, "me pasas el CATALOGO y precio?")] == [
        "PRECIO", "catálogo"]
    assert c.literal_matches(rules, "qué bonito") == []
    assert c.literal_matches(rules, "aprecio mucho") == []  # no confunde palabras que solo contienen la clave


class FakeIG:
    def __init__(self):
        self.dms, self.public, self.hidden = [], [], []

    async def permalink(self, media_id):
        return {"m1": POST, "m2": "https://www.instagram.com/p/OTRA/"}[media_id]

    async def hide_comment(self, comment_id):
        self.hidden.append(comment_id)

    async def private_reply(self, comment_id, text):
        self.dms.append((comment_id, text))

    async def public_reply(self, comment_id, text):
        self.public.append((comment_id, text))


async def never_spam(text):
    return False


def run(values, classify=None, spam_check=never_spam):
    async def main():
        store = Store(":memory:")
        await store.open()
        ig = FakeIG()

        async def reglas_del_panel(store):
            return RULES

        async def no_claude(rules, text):
            return []

        from igbot import contenido

        orig = contenido.reglas, cuenta().imagenes_mes
        contenido.reglas, cuenta().imagenes_mes = reglas_del_panel, 40
        try:
            for v in values:
                await c.handle(v, store, ig, "negocio", classify=classify or no_claude,
                               spam_check=spam_check)
        finally:
            contenido.reglas, cuenta().imagenes_mes = orig
        recent = await store.recent("ana", 5)
        results = await store.week_comments(0, 2e9)
        await store.close()
        return ig, recent, results

    return asyncio.run(main())


def comment(cid, text, media="m1", author="ana"):
    return {"id": cid, "text": text, "from": {"id": author, "username": author}, "media": {"id": media}}


def test_un_solo_dm_con_todo_lo_que_pidio():
    ig, recent, _ = run([comment("c1", "precio y catálogo porfa")])
    assert ig.dms == [("c1", "Nuestros precios: ...\n\nAquí el catálogo: ...")]
    assert recent and "DM por su comentario" in recent[-1][1]


def test_ignora_repetidos_propios_y_otras_publicaciones():
    ig, _, _ = run([
        comment("c1", "precio"), comment("c1", "precio"),  # Meta reintenta
        comment("c2", "precio", author="negocio"),  # la propia cuenta
        comment("c3", "precio", media="m2"),  # publicación sin palabras clave
    ])
    assert ig.dms == [("c1", "Nuestros precios: ...")]


def test_variantes_las_decide_claude():
    async def classify(rules, text):
        return [r for r in rules if r.keywords[0] == "PRECIO"]

    ig, _, _ = run([comment("c1", "cuánto cuestan?")], classify=classify)
    assert ig.dms == [("c1", "Nuestros precios: ...")]


def test_oculta_spam_en_cualquier_publicacion(monkeypatch):
    monkeypatch.setattr(cuenta(), "hide_spam", True)

    async def is_spam(text):
        return "sigueme" in text

    ig, _, results = run([
        comment("c1", "sigueme y gana dinero", media="m2"),  # publicación sin reglas
        comment("c2", "precio porfa"),  # trae la palabra clave: ni se pregunta
        comment("c3", "pésimo servicio, nunca llegó"),  # una queja no es spam
    ], spam_check=is_spam)
    assert ig.hidden == ["c1"]
    assert ig.dms == [("c2", "Nuestros precios: ...")]
    assert (c.SPAM, 0, 0) in results


def test_sin_filtro_no_se_oculta_nada():
    async def always(text):
        raise AssertionError("no se debe preguntar")

    ig, _, _ = run([comment("c1", "sigueme")], spam_check=always)
    assert ig.hidden == []


def test_pasado_el_tope_del_dia_solo_cuentan_las_palabras_exactas(monkeypatch):
    monkeypatch.setattr(cuenta(), "max_comments_per_day", 1)

    async def classify(rules, text):
        return [r for r in rules if r.keywords[0] == "PRECIO"]

    ig, _, _ = run([
        comment("c1", "cuánto cuestan?"),  # dentro del tope: Claude reconoce la variante
        comment("c2", "cuánto cuestan?", author="beto"),  # pasado el tope ya no se le pregunta
        comment("c3", "precio", author="eva"),  # la palabra exacta sigue funcionando
    ], classify=classify)
    assert ig.dms == [("c1", "Nuestros precios: ..."), ("c3", "Nuestros precios: ...")]
