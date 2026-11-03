"""Etapas de cada contacto: se guardan por persona, avanzan solas con lo que anota el asistente o una venta,
el dueño las cambia en el chat, se filtran en Conversaciones y se descargan en el CSV de contactos."""
import asyncio
import time

from fastapi.testclient import TestClient

import app as bot
import test_contenido
from igbot import conversaciones
from igbot.cuenta import usar
from igbot.store import Store
from test_contenido import ORIGEN

cliente = test_contenido.cliente


def test_la_etapa_avanza_sola_y_nunca_retrocede_sola(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    s = c.store

    async def main():
        for u in ("ana", "beto", "caro", "dani"):
            await s.add_incoming(u, f"m-{u}", "hola")
        await s.add_event("ana", "interes", {"tipo": "visita"})
        await s.add_event("beto", "interes", {"tipo": "cotizacion"})
        await s.add_event("caro", "cotizacion", {"total": 100})
        await s.add_event("caro", "cita", {"id": "ev1"})
        await s.add_event("caro", "interes", {"tipo": "datos"})  # después de la cita: no la baja
        await s.add_event("dani", "humano", {"razon": "persona"})  # pasar a tu equipo no es etapa
        await s.add_event("admin:1", "interes", {"tipo": "visita"})  # los administradores no tienen etapa
        return await s.etapas_de(["ana", "beto", "caro", "dani", "admin:1"])

    assert correr(main()) == {"ana": "interesado", "beto": "cotizo", "caro": "cita", "dani": "nuevo",
                              "admin:1": "nuevo"}


def test_una_venta_lleva_a_compro_y_al_quitarla_vuelve_a_lo_que_dicen_sus_eventos(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    s = c.store

    async def main():
        await s.add_incoming("ana", "m1", "hola")
        await s.add_event("ana", "interes", {"tipo": "cotizacion"})
        await s.add_venta("manual", "v1", "ana", "", 500, "MXN", {}, time.time())
        compro = (await s.etapas_de(["ana"]))["ana"]
        await s.add_venta("manual", "v1", "ana", "", 500, "MXN", {}, time.time())  # repetida: no cambia nada
        await s.borrar_venta("manual", "v1")
        sin_venta = (await s.etapas_de(["ana"]))["ana"]
        # Si "Compró" la puso el dueño, quitar una venta no la cambia.
        await s.add_venta("manual", "v2", "ana", "", 500, "MXN", {}, time.time())
        await s.set_etapa("ana", "compro")
        await s.borrar_venta("manual", "v2")
        return compro, sin_venta, (await s.etapas_de(["ana"]))["ana"]

    assert correr(main()) == ("compro", "cotizo", "compro")


def test_la_etapa_a_mano_se_respeta_y_sigue_avanzando(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    s = c.store

    async def main():
        await s.add_incoming("ana", "m1", "hola")
        await s.add_event("ana", "cita", {"id": "ev1"})
        await s.set_etapa("ana", "perdido")  # No compró
        perdido = (await s.etapas_de(["ana"]))["ana"]
        await s.add_event("ana", "interes", {"tipo": "visita"})  # vuelve a interesarse
        return perdido, (await s.etapas_de(["ana"]))["ana"]

    assert correr(main()) == ("perdido", "interesado")


def test_las_bases_de_antes_toman_su_etapa_una_sola_vez(tmp_path):
    ruta = str(tmp_path / "bot.db")

    async def main():
        s = Store(ruta)
        await s.open()
        # Como estaba antes de las etapas: eventos y ventas guardados sin tabla de etapas.
        ahora = time.time()
        await s.db.executemany("INSERT INTO events (user_id, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                               [("ana", "interes", '{"tipo": "visita"}', ahora),
                                ("beto", "interes", '{"tipo": "cotizacion"}', ahora),
                                ("beto", "cita", '{"id": "e1"}', ahora)])
        await s.db.execute("INSERT INTO ventas (origen, externo, user_id, monto, created_at)"
                           " VALUES ('shopify', 'p1', 'caro', 10, ?)", (ahora,))
        await s.db.execute("DELETE FROM etapas")
        await s.db.execute("DELETE FROM kv WHERE key = 'etapas_iniciales'")
        await s.db.commit()
        await s.close()
        s = Store(ruta)
        await s.open()
        antes = await s.etapas_de(["ana", "beto", "caro"])
        await s.set_etapa("ana", "perdido")
        await s.close()
        s = Store(ruta)
        await s.open()  # ya migrada: no la vuelve a calcular
        despues = (await s.etapas_de(["ana"]))["ana"]
        await s.close()
        return antes, despues

    antes, despues = asyncio.run(main())
    assert antes == {"ana": "interesado", "beto": "cita", "caro": "compro"} and despues == "perdido"


def test_la_etapa_dura_mas_que_los_eventos_y_se_va_con_el_contacto(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    s = c.store

    async def main():
        viejo = time.time() - 400 * 86400
        await s.add_incoming("ana", "m1", "hola")
        await s.add_incoming("beto", "m2", "hola")
        await s.add_event("ana", "interes", {"tipo": "visita"})
        await s.add_event("beto", "interes", {"tipo": "visita"})
        await s.db.execute("UPDATE events SET created_at = ?", (viejo,))
        await s.db.execute("UPDATE etapas SET updated_at = ?", (viejo,))
        await s.db.execute("UPDATE messages SET created_at = ? WHERE user_id = 'beto'", (viejo,))
        await s.db.commit()
        borrado = await s.purgar(365)
        return borrado, await s.etapas_de(["ana", "beto"])

    borrado, etapas = correr(main())
    assert borrado["events"] == 2 and borrado["etapas"] == 1
    assert etapas == {"ana": "interesado", "beto": "nuevo"}  # ana sigue escribiendo: su etapa se queda


def test_conversaciones_muestra_la_etapa_y_filtra_por_ella(cuenta_de_prueba):
    c, correr = cuenta_de_prueba

    async def main():
        s = c.store
        for u in ("ana", "beto"):
            await s.add_incoming(u, f"m-{u}", "hola")
        await s.add_event("ana", "interes", {"tipo": "cotizacion"})
        with usar(c):
            todas = await conversaciones.partes(c, ver="curso")
            cotizo = await conversaciones.partes(c, ver="curso", etapa="cotizo")
            nuevos = await conversaciones.partes(c, ver="curso", etapa="nuevo")
            abierto = await conversaciones.partes(c, ver="curso", chat="ana", etapa="cotizo")
            rara = await conversaciones.partes(c, ver="curso", etapa="'; DROP")
        return todas, cotizo, nuevos, abierto, rara

    todas, cotizo, nuevos, abierto, rara = correr(main())
    assert '<span class="razon etapa">Cotizó</span>' in todas["lista"] and "@beto" in todas["lista"]
    assert "@ana" in cotizo["lista"] and "@beto" not in cotizo["lista"] and cotizo["etapa"] == "cotizo"
    assert '<option value="cotizo" selected>' in cotizo["busqueda"] and "/panel/contactos.csv?etapa=cotizo" in cotizo["busqueda"]
    assert "etapa=cotizo" in cotizo["lista"]  # los enlaces de la lista conservan el filtro
    assert "@beto" in nuevos["lista"] and "@ana" not in nuevos["lista"]
    assert "Etapa: Cotizó" in abierto["hilo"] and 'value="cotizo" aria-pressed="true"' in abierto["hilo"]
    assert '<input type="hidden" name="etapa" value="cotizo">' in abierto["hilo"]  # volver a la bandeja filtrada
    assert rara["etapa"] == "" and "@ana" in rara["lista"]  # una etapa que no existe no filtra
    e = conversaciones.enlace("bot", "", 1, "ana", etapa="compro")
    assert "etapa=compro" in e and "chat=ana" in e


def test_cambiar_la_etapa_desde_el_chat(cliente):
    client, c, *_ = cliente
    client.portal.call(c.store.add_incoming, "u1", "m1", "hola")
    assert client.post("/panel/etapa", data={"usuario": "u1", "nueva": "compro"}).status_code == 403  # sin Origin
    sin_sesion = TestClient(bot.app, base_url="https://testserver")
    assert sin_sesion.post("/panel/etapa", headers=ORIGEN, data={"usuario": "u1", "nueva": "compro"},
                           follow_redirects=False).headers["location"] == "/panel"
    r = client.post("/panel/etapa", headers=ORIGEN, data={"usuario": "u1", "nueva": "perdido", "etapa": "nuevo"})
    assert "la etapa quedó en «No compró»" in r.text
    assert client.portal.call(c.store.etapas_de, ["u1"]) == {"u1": "perdido"}
    assert "Elige una etapa" in client.post("/panel/etapa", headers=ORIGEN, data={"usuario": "u1", "nueva": "vip"}).text
    assert "No encontramos ese chat" in client.post("/panel/etapa", headers=ORIGEN,
                                                    data={"usuario": "otro", "nueva": "compro"}).text
    assert client.portal.call(c.store.etapas_de, ["otro"]) == {"otro": "nuevo"}
    # La página de Conversaciones carga con el filtro.
    assert '<option value="perdido" selected>' in client.get("/panel/asistente?ver=bot&etapa=perdido").text


def test_descargar_contactos_en_csv(cliente):
    client, c, *_ = cliente
    sin_sesion = TestClient(bot.app, base_url="https://testserver")
    assert sin_sesion.get("/panel/contactos.csv", follow_redirects=False).headers["location"] == "/panel"
    for u in ("u1", "u2"):
        client.portal.call(c.store.add_incoming, u, f"m-{u}", "hola")
    client.portal.call(c.store.set, "username:u1", "@ana.lu")
    client.portal.call(c.store.add_event, "u1", "interes", {"tipo": "cotizacion"})
    client.portal.call(c.store.add_venta, "manual", "v1", "u1", "", 1500, "MXN", {}, time.time())
    client.portal.call(c.store.set_fact, "u1", "correo", "ana@correo.mx")
    client.portal.call(c.store.set_fact, "u2", "nota", "=HYPERLINK(\"http://x\")")  # texto ajeno: no es fórmula
    client.portal.call(c.store.add_incoming, "admin:1", "m-a", "hola")  # los administradores no salen
    r = client.get("/panel/contactos.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="contactos-todos-' in r.headers["content-disposition"]
    texto = r.content.decode("utf-8")
    assert texto.startswith("﻿Usuario de Instagram,Id,Etapa,")
    filas = texto.strip().splitlines()
    assert len(filas) == 3 and "admin:1" not in texto
    assert any(f.startswith("ana.lu,u1,Compró,") and ",1500.0,MXN,correo: ana@correo.mx" in f for f in filas)
    assert "'nota: =HYPERLINK" not in texto and "nota: =HYPERLINK" in texto  # empieza con "nota", no con =
    solo = client.get("/panel/contactos.csv?etapa=nuevo").content.decode("utf-8")
    assert ",u2,Nuevo," in solo and ",u1," not in solo


def test_una_celda_que_empieza_como_formula_no_se_ejecuta():
    from igbot.resultados import csv_de
    texto = csv_de([["=1+1", "+52", "-x", "@ana", "hola", 5, None]], list("abcdefg")).decode("utf-8")
    assert texto.splitlines()[1] == "'=1+1,'+52,'-x,'@ana,hola,5,"
