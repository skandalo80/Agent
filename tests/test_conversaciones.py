"""La vista de Conversaciones (igbot/conversaciones.py), probada directo sobre una base en memoria."""
import time

import app as bot  # noqa: F401  (carga la app con el entorno de conftest)
from igbot import config, conversaciones
from igbot.cuenta import usar


def test_la_bandeja_lista_los_chats_y_abre_el_hilo(cuenta_de_prueba):
    c, correr = cuenta_de_prueba

    async def main():
        s = c.store
        await s.add_incoming("ana", "m1", "Hola, ¿tienen envíos a Puebla?")
        await s.mark_answered("ana", [1], "Sí, enviamos a todo México.")
        await s.add_incoming("beto", "m2", "¿Precio del combo?")  # pendiente: lo tiene el asistente
        with usar(c):
            todo = await conversaciones.partes(c, ver="curso")
            abierto = await conversaciones.partes(c, ver="curso", chat="ana")
            sin_filtro = await conversaciones.partes(c)
        return todo, abierto, sin_filtro

    todo, abierto, sin_filtro = correr(main())
    assert "@ana" in todo["lista"] and "@beto" in todo["lista"] and todo["hilo"] == "" and todo["chat"] == ""
    assert 'type="search"' in todo["busqueda"] and todo["ver"] == "curso"
    assert "¿tienen envíos a Puebla?" in abierto["hilo"] and "enviamos a todo México" in abierto["hilo"]
    assert abierto["chat"] == "ana"
    assert sin_filtro["ver"] in conversaciones.FILTROS  # sin filtro elige uno válido


def test_un_chat_pasado_a_una_persona_sale_en_tu_lado_con_su_razon(cuenta_de_prueba):
    c, correr = cuenta_de_prueba

    async def main():
        s = c.store
        await s.add_incoming("caro", "m1", "Quiero hablar con alguien")
        await s.mark_answered("caro", [1], "Te paso con el equipo.")
        await s.add_event("caro", "humano", {"razon": "persona"})
        await s.pause("caro", time.time() + 3600)
        with usar(c):
            nuevos = await conversaciones.partes(c, ver="nuevos")
            abierto = await conversaciones.partes(c, ver="nuevos", chat="caro")
        return nuevos, abierto

    nuevos, abierto = correr(main())
    assert "@caro" in nuevos["lista"] and nuevos["pendientes"] >= 1
    assert "caro" in abierto["chat"] and "Quiero hablar con alguien" in abierto["hilo"]


def test_la_busqueda_filtra_y_el_enlace_conserva_filtro_busqueda_y_pagina(cuenta_de_prueba):
    c, correr = cuenta_de_prueba

    async def main():
        s = c.store
        await s.add_incoming("ana", "m1", "Hola")
        await s.add_incoming("beto", "m2", "Busco una mesa de roble")
        with usar(c):
            return await conversaciones.partes(c, ver="curso", buscar="roble")

    p = correr(main())
    assert "@beto" in p["lista"] and "@ana" not in p["lista"] and p["buscar"] == "roble"
    e = conversaciones.enlace("bot", "roble", 2, "beto")
    assert e.startswith("/panel/asistente") and "ver=bot" in e and "q=roble" in e and "chat=beto" in e and "p=2" in e


def test_la_ventana_para_contestar_depende_de_la_etiqueta_human_agent(monkeypatch):
    monkeypatch.setattr(config, "HUMAN_AGENT_TAG", False)
    assert conversaciones.ventana_panel() == conversaciones.VENTANA == 24 * config.HORA
    monkeypatch.setattr(config, "HUMAN_AGENT_TAG", True)
    assert conversaciones.ventana_panel() == conversaciones.VENTANA_HUMANO == 7 * config.DIA
