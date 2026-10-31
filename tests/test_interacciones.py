"""Configura tus interacciones > Comentarios: la tabla de qué contestar a cada tipo de comentario (sin red ni Claude)."""
import asyncio
import json
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from igbot import comentarios, interacciones as it  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_comentarios import ROWS, FakeIG, comment, never_spam  # noqa: E402


def tabla(**cambios) -> dict:
    """Lo que propone la tabla, con `cambios` por tipo ({"pub": {...}} o {"dm": {...}})."""
    base = it.reglas({})
    for k, cambio in cambios.items():
        for canal, valores in cambio.items():
            base[k][canal].update(valores)
    return {"comentarios": base}


def run(values, datos=None, escribe=None, monkeypatch=None, imagenes=40, con_reglas=False):
    llamadas = []

    async def falso(c, texto, usuario, va_dm):
        llamadas.append(texto)
        return escribe(texto) if escribe else {}

    async def main():
        store = Store(":memory:")
        await store.open()
        if datos is not None:
            await store.set("interacciones", json.dumps(datos))
        ig = FakeIG()
        rules = comentarios.parse_rules(ROWS) if con_reglas else []

        async def fake_rules(google):
            return rules

        async def sin_reglas_del_panel(store):
            return []

        async def no_claude(rules, text):
            return []

        monkeypatch.setattr(cuenta(), "imagenes_mes", imagenes)
        monkeypatch.setattr(comentarios, "load_rules", fake_rules)
        monkeypatch.setattr(comentarios, "available", lambda: con_reglas)
        monkeypatch.setattr("igbot.contenido.reglas", sin_reglas_del_panel)
        monkeypatch.setattr("igbot.panel.usuario", lambda c: asyncio.sleep(0, "cafe.lunar"))
        for v in values:
            await comentarios.handle(v, store, ig, "google", "negocio", classify=no_claude, spam_check=never_spam,
                                     escribe=falso)
        resultados = await store.week_comments(0, 2e9)
        await store.close()
        return ig, resultados

    ig, resultados = asyncio.run(main())
    return ig, resultados, llamadas


def test_sin_guardar_la_tabla_todo_sigue_como_antes(monkeypatch):
    ig, _, llamadas = run([comment("c1", "me encanta!", media="m2")], monkeypatch=monkeypatch)
    assert ig.public == [] and ig.dms == [] and llamadas == []


def test_la_ia_clasifica_y_contesta_en_publico_y_por_dm(monkeypatch):
    def escribe(texto):
        return {"tipo": "pregunta", "publica": "¡Te escribimos por DM!", "dm": "Hola, el latte cuesta $55."}

    ig, resultados, llamadas = run([comment("c1", "cuánto cuesta el latte?", media="m2")], tabla(),
                                   escribe, monkeypatch)
    assert llamadas == ["cuánto cuesta el latte?"]
    assert ig.public == [("c1", "¡Te escribimos por DM!")]
    assert ig.dms == [("c1", "Hola, el latte cuesta $55.")]
    assert ("tipo: pregunta", 1, 1) in resultados


def test_emojis_con_predeterminadas_no_llaman_a_claude(monkeypatch):
    datos = tabla(emojis={"pub": {"modo": "fijo", "lista": ["¡Gracias! 💛", "🙌"]}})
    ig, _, llamadas = run([comment("c1", "😍🔥🔥", media="m2"), comment("c2", "❤️", media="m2", author="beto")],
                          datos, None, monkeypatch)
    assert llamadas == []
    assert {t for _, t in ig.public} <= {"¡Gracias! 💛", "🙌"} and len(ig.public) == 2
    assert ig.dms == []  # por DM está apagado


def test_solo_etiquetas_es_etiqueta_y_apagado_no_contesta(monkeypatch):
    datos = tabla(etiqueta={"pub": {"on": False}})
    ig, resultados, llamadas = run([comment("c1", "@ana @beto", media="m2")], datos, None, monkeypatch)
    assert ig.public == [] and llamadas == [] and ("tipo: etiqueta", 0, 0) in resultados


def test_tipo_desconocido_cae_en_otro_que_viene_apagado(monkeypatch):
    ig, _, _ = run([comment("c1", "jaja", media="m2")], tabla(), lambda t: {"tipo": "raro", "publica": "x"},
                   monkeypatch)
    assert ig.public == [] and ig.dms == []


def test_las_palabras_clave_van_primero_y_los_hilos_no_se_contestan(monkeypatch):
    hilo = {**comment("c2", "gracias!", media="m2"), "parent_id": "c0"}
    ig, _, llamadas = run([comment("c1", "precio porfa"), hilo], tabla(), lambda t: {"tipo": "elogio", "publica": "😊"},
                          monkeypatch, con_reglas=True)
    assert ig.dms == [("c1", "Nuestros precios: ...")]  # la palabra clave de la publicación
    assert ig.public == [] and llamadas == []


def test_sin_contenido_en_el_plan_no_aplica(monkeypatch):
    ig, _, llamadas = run([comment("c1", "me encanta", media="m2")], tabla(), lambda t: {"tipo": "elogio", "publica": "😊"},
                          monkeypatch, imagenes=0)
    assert ig.public == [] and llamadas == []


def test_spam_lo_decide_la_tabla(monkeypatch):
    monkeypatch.setattr(cuenta(), "hide_spam", True)
    assert it.ocultar_spam(cuenta(), {}) is True  # sin guardar: lo de su .env
    assert it.ocultar_spam(cuenta(), {"spam": False}) is False


def test_tipos_sin_claude():
    assert it.tipo_sin_claude("😍🔥!!") == "emojis"
    assert it.tipo_sin_claude("@ana.m @beto_2 👀") == "etiqueta"
    assert it.tipo_sin_claude("@ana mira esto") == ""


def test_palabras_clave_generales_sirven_en_cualquier_post(monkeypatch):
    datos = {"claves": [{"palabras": "MENU, carta", "dm": "Aquí la carta: …", "publica": "¡Te mandamos DM!"}]}
    ig, _, llamadas = run([comment("c1", "me pasas el menú?", media="m2")], datos, None, monkeypatch)
    assert ig.dms == [("c1", "Aquí la carta: …")] and ig.public == [("c1", "¡Te mandamos DM!")]
    assert llamadas == []  # va antes que la tabla
    ig, _, _ = run([comment("c1", "menu", media="m2")], {**datos, "activo": False}, None, monkeypatch)
    assert ig.dms == []  # con sus interacciones en pausa


def test_pausadas_la_tabla_no_contesta(monkeypatch):
    ig, _, llamadas = run([comment("c1", "me encanta", media="m2")], {**tabla(), "activo": False},
                          lambda t: {"tipo": "elogio", "publica": "😊"}, monkeypatch)
    assert ig.public == [] and llamadas == []


def test_el_tope_del_dia_sale_de_la_tabla(monkeypatch):
    ig, _, llamadas = run([comment("c1", "me encanta", media="m2"), comment("c2", "qué cool", media="m2", author="b")],
                          {**tabla(), "tope": 50}, lambda t: {"tipo": "elogio", "publica": "😊"}, monkeypatch)
    assert len(ig.public) == 2
    assert it.tope(cuenta(), {"tope": 50}) == 50 and it.tope(cuenta(), {"tope": 7}) == cuenta().max_comments_per_day


class FakeCanal:
    def __init__(self):
        self.textos, self.menciones = [], []

    async def send_text(self, user_id, text):
        self.textos.append((user_id, text))

    async def texto_mencion(self, media_id, comment_id=""):
        return "Miren lo que compré en @cafe.lunar 😍"

    async def contestar_mencion(self, media_id, comment_id, text):
        self.menciones.append((media_id, comment_id, text))


class FakeCuenta:
    def __init__(self, store, imagenes=40):
        self.store, self.instagram, self.imagenes_mes = store, FakeCanal(), imagenes
        self.max_comments_per_day, self.hide_spam = 300, False


def con_cuenta(datos, prueba, monkeypatch, imagenes=40):
    monkeypatch.setattr("igbot.panel.usuario", lambda c: asyncio.sleep(0, "cafe.lunar"))

    async def main():
        store = Store(":memory:")
        await store.open()
        if datos is not None:
            await store.set("interacciones", json.dumps(datos))
        c = FakeCuenta(store, imagenes)
        try:
            return await prueba(c)
        finally:
            await store.close()

    return asyncio.run(main())


MENCION = {"mid": "m1", "attachments": [{"type": "story_mention", "payload": {"url": "https://x"}}]}
RESPUESTA = {"mid": "m2", "text": "🔥", "reply_to": {"story": {"id": "s1", "url": "https://x"}}}


def test_que_historia_es():
    assert it.de_historia(MENCION, "[envió: story_mention]") == ("menciones", "historia")
    assert it.de_historia(RESPUESTA, "🔥") == ("historias", "reaccion")
    assert it.de_historia({**RESPUESTA, "text": "¿cuánto?"}, "¿cuánto?") == ("historias", "mensaje")
    assert it.de_historia({"mid": "m3", "text": "hola"}, "hola") is None


def test_historias_con_la_ia_siguen_el_camino_de_siempre(monkeypatch):
    async def prueba(c):
        return await it.dm_de_historia(c, "ana", RESPUESTA, "🔥"), await c.store.pending("ana")

    assert con_cuenta(None, prueba, monkeypatch) == (False, [])  # lo propuesto: La IA contesta = el asistente


def test_mencion_en_historia_con_mensaje_predeterminado(monkeypatch):
    datos = {"menciones": {"historia": {"dm": {"on": True, "modo": "fijo", "msg": "¡Gracias por compartirnos! 💛"}}}}

    async def prueba(c):
        atendido = await it.dm_de_historia(c, "ana", MENCION, "[envió: story_mention]")
        return atendido, c.instagram.textos, await c.store.pending("ana"), await c.store.recent("ana", 5)

    atendido, textos, pendientes, recientes = con_cuenta(datos, prueba, monkeypatch)
    assert atendido and textos == [("ana", "¡Gracias por compartirnos! 💛")]
    assert pendientes == [] and recientes[-1] == ("assistant", "¡Gracias por compartirnos! 💛")


def test_reaccion_sin_contestar_no_se_contesta(monkeypatch):
    datos = {"historias": {"reaccion": {"dm": {"on": False}}}}

    async def prueba(c):
        return await it.dm_de_historia(c, "ana", RESPUESTA, "🔥"), c.instagram.textos, await c.store.pending("ana")

    assert con_cuenta(datos, prueba, monkeypatch) == (True, [], [])


def test_mencion_en_un_post_se_agradece_ahi(monkeypatch):
    escritos = []

    async def escribe(c, texto, usuario, va_dm, mencion=False):
        escritos.append((texto, mencion))
        return {"publica": "¡Gracias por la mención! 💛"}

    async def prueba(c):
        await it.mencion(c, {"media_id": "77", "comment_id": "88"}, escribe=escribe)
        await it.mencion(c, {"media_id": "77", "comment_id": "88"}, escribe=escribe)  # Meta reintenta
        return c.instagram.menciones

    assert con_cuenta({}, prueba, monkeypatch) == [("77", "88", "¡Gracias por la mención! 💛")]
    assert escritos == [("Miren lo que compré en @cafe.lunar 😍", True)]

    async def apagada(c):
        await it.mencion(c, {"media_id": "77"}, escribe=escribe)
        return c.instagram.menciones

    assert con_cuenta({"menciones": {"post": {"pub": {"on": False}}}}, apagada, monkeypatch) == []
    assert con_cuenta({}, apagada, monkeypatch, imagenes=0) == []
