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
