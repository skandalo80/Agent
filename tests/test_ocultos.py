"""Ocultar spam e insultos por separado, la lista de ocultos con Mostrar y el correo del día (sin red ni Claude)."""
import asyncio
import json
import os
from datetime import datetime

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:",
                  DEBOUNCE_SECONDS="0.2")  # como test_adjuntos: el primero en importar app fija la espera

import test_contenido  # noqa: E402
from igbot import comentarios, fechas, interacciones as it, ocultos  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_comentarios import FakeIG, comment  # noqa: E402
from test_contenido import ORIGEN  # noqa: E402

cliente = test_contenido.cliente


def correr(values, datos, revisa, monkeypatch):
    async def main():
        store = Store(":memory:")
        await store.open()
        await store.set("interacciones", json.dumps(datos))
        ig = FakeIG()

        async def nada(*a):
            return []

        monkeypatch.setattr(cuenta(), "imagenes_mes", 40)
        monkeypatch.setattr("igbot.contenido.reglas", nada)
        for v in values:
            await comentarios.handle(v, store, ig, "negocio", classify=nada, spam_check=revisa)
        filas = await store.week_comments(0, 2e9)
        await store.close()
        return ig, filas

    return asyncio.run(main())


async def revisa(texto):
    return "spam" if "sigueme" in texto else "ofensa" if "rateros" in texto else ""


def test_spam_e_insultos_se_ocultan_por_separado(monkeypatch):
    valores = [comment("c1", "sigueme y gana", media="m2"), comment("c2", "son unos rateros", media="m2"),
               comment("c3", "nunca llegó mi pedido", media="m2")]
    ig, filas = correr(valores, {"spam": True, "ofensas": True}, revisa, monkeypatch)
    assert ig.hidden == ["c1", "c2"]
    assert (comentarios.SPAM, 0, 0) in filas and (comentarios.OFENSA, 0, 0) in filas

    ig, _ = correr(valores, {"spam": True, "ofensas": False}, revisa, monkeypatch)
    assert ig.hidden == ["c1"]  # el insulto queda visible (lo decide la fila "Un insulto o ataque")
    ig, _ = correr(valores, {"spam": False, "ofensas": True}, revisa, monkeypatch)
    assert ig.hidden == ["c2"]


def test_sin_ningun_interruptor_no_se_le_pregunta_a_claude(monkeypatch):
    async def nunca(texto):
        raise AssertionError("no se debe preguntar")

    ig, _ = correr([comment("c1", "son unos rateros", media="m2")], {"spam": False, "ofensas": False}, nunca, monkeypatch)
    assert ig.hidden == []


def test_insultos_siguen_al_spam_mientras_no_se_guarde(monkeypatch):
    monkeypatch.setattr(cuenta(), "hide_spam", True)
    assert it.ocultar_ofensas(cuenta(), {}) is True  # antes iban dentro del spam: no cambia nada al desplegar
    assert it.ocultar_ofensas(cuenta(), {"spam": False}) is False
    assert it.ocultar_ofensas(cuenta(), {"spam": True, "ofensas": False}) is False
    assert "ataque" in it.TIPOS and it.reglas({})["ataque"]["pub"]["on"] is False  # visible: no contesta de inicio


def test_claude_dice_spam_ofensa_u_ok(monkeypatch):
    respuestas = iter(["spam", "Ofensa.", "ok"])

    async def falso(prompt, system, max_turns=1, model=None):
        assert "texto de un desconocido" in system
        return next(respuestas)

    monkeypatch.setattr("igbot.agent.run_claude", falso)
    assert [asyncio.run(comentarios.claude_spam("x")) for _ in range(3)] == ["spam", "ofensa", ""]


def test_ruta_sin_sesion_y_post_sin_origen(cliente):
    client, *_ = cliente
    client.cookies.clear()
    r = client.get("/panel/interacciones/ocultos", follow_redirects=False)
    assert (r.status_code, r.headers.get("location")) == (303, "/panel")
    assert client.post("/panel/interacciones/ocultos", data={"id": "c1"}, follow_redirects=False).status_code == 403


def test_lista_y_mostrar(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    mostrados = []

    async def show(cid):
        mostrados.append(cid)

    monkeypatch.setattr(c.instagram, "show_comment", show, raising=False)

    async def preparar():
        for cid, texto, marca in [("o1", "sigueme " + "x" * 300, comentarios.SPAM), ("o2", "son unos rateros", comentarios.OFENSA),
                                  ("n1", "me encanta", "tipo: elogio")]:
            await c.store.add_comment(cid, "ana", "m1", texto)
            await c.store.comment_result(cid, marca, False, False)
    client.portal.call(preparar)

    r = client.get("/panel/interacciones/ocultos").text
    assert "son unos rateros" in r and "Insulto o ataque" in r and "Spam" in r and "me encanta" not in r
    assert "x" * 200 not in r  # del texto de un tercero solo se ve el principio

    r = client.post("/panel/interacciones/ocultos", headers=ORIGEN, data={"id": "o2"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("ok=1")
    assert mostrados == ["o2"]
    assert "son unos rateros" not in client.get("/panel/interacciones/ocultos").text
    # Uno que no ocultamos nosotros (o que no existe) no llega a Instagram.
    client.post("/panel/interacciones/ocultos", headers=ORIGEN, data={"id": "n1"})
    client.post("/panel/interacciones/ocultos", headers=ORIGEN, data={"id": "zz"})
    assert mostrados == ["o2"]


def test_guardar_los_interruptores(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 5)
    r = client.get("/panel/interacciones?paso=comentarios").text
    assert "Ocultar insultos y ataques" in r and "/panel/interacciones/ocultos" in r
    client.post("/panel/interacciones", headers=ORIGEN, data={"paso": "comentarios", "spam": "1"})
    datos = client.portal.call(it.guardado, c)
    assert (datos["spam"], datos["ofensas"], datos["aviso_ocultos"]) == (True, False, False)


class Google:
    def __init__(self):
        self.correos = []

    async def send_email(self, de, nombre, para, cc, asunto, texto):
        self.correos.append((para, asunto, texto))


def test_correo_del_dia_una_sola_vez(cuenta_de_prueba, monkeypatch):
    c, run = cuenta_de_prueba
    monkeypatch.setattr(c, "imagenes_mes", 5)
    monkeypatch.setattr(c, "owner_email", "dueno@negocio.mx")
    ahora = datetime(2026, 11, 3, 10, 0, tzinfo=fechas.zona(c))
    monkeypatch.setattr(fechas, "ahora", lambda c: ahora)
    monkeypatch.setattr(fechas, "hoy", lambda c: ahora.date())
    ayer = ahora.timestamp() - 12 * 3600
    google = Google()

    async def main():
        assert not await ocultos.tick(c, google)  # nada oculto: no se manda nada
        for cid, marca in [("a", comentarios.SPAM), ("b", comentarios.OFENSA), ("d", comentarios.OFENSA)]:
            await c.store.add_comment(cid, "ana", "m1", "algo feo")
            await c.store.comment_result(cid, marca, False, False)
        await c.store.db.execute("UPDATE comments SET created_at = ?", (ayer,))
        assert await ocultos.tick(c, google)
        assert not await ocultos.tick(c, google)  # uno al día
    run(main())
    para, asunto, texto = google.correos[0]
    assert para == "dueno@negocio.mx" and "3 comentarios" in asunto
    assert "1 de spam" in texto and "2 con insultos" in texto and "/panel/interacciones/ocultos" in texto
    assert "feo" not in texto and "ana" not in texto  # sin el texto ni quién lo escribió


def test_correo_apagado(cuenta_de_prueba, monkeypatch):
    c, run = cuenta_de_prueba
    monkeypatch.setattr(c, "imagenes_mes", 5)
    monkeypatch.setattr(c, "owner_email", "dueno@negocio.mx")
    google = Google()

    async def main():
        await c.store.set("interacciones", json.dumps({"aviso_ocultos": False}))
        await c.store.add_comment("a", "ana", "m1", "texto")
        await c.store.comment_result("a", comentarios.SPAM, False, False)
        return await ocultos.tick(c, google)
    assert run(main()) is False and google.correos == []
