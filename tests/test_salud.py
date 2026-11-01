"""Salud del servicio (/panel/salud): números medidos en el proceso, sin red."""
import asyncio
import os
import time

os.environ.setdefault("DEBOUNCE_SECONDS", "0.2")

from igbot import lentitud, salud  # noqa: E402


def test_percentiles_y_ventana_de_24_h():
    ahora = time.time()
    salud._respuestas.clear()
    for seg in (1, 2, 3, 4, 100):
        salud.anotar_respuesta("cafe", seg, ahora)
    salud.anotar_respuesta("cafe", 500, ahora - 25 * 3600)  # de ayer: ya no cuenta
    t = salud.tiempos("cafe", ahora)
    assert t == [1, 2, 3, 4, 100]
    assert salud.percentil(t, 50) == 3
    assert salud.percentil(t, 95) == 100
    assert salud.percentil([], 50) == 0.0


def test_la_memoria_sale_de_proc_e_incluye_a_los_subprocesos():
    import subprocess
    hijo = subprocess.Popen(["sleep", "5"])
    try:
        m = salud.memoria()
        assert m["proceso_mb"] > 0 and m["total_mb"] > 0 and m["libre_mb"] > 0
        assert hijo.pid in salud.descendientes(os.getpid())
        assert m["hijos"] >= 1
    finally:
        hijo.kill()
        hijo.wait()


def test_los_contadores_de_lentitud_se_reinician_al_cambiar_el_dia(monkeypatch):
    lentitud.CONTADORES.update(dia="2000-01-01", lentos=7, bloqueos=2)
    lentitud.contar("bloqueos")
    assert lentitud.CONTADORES["lentos"] == 0 and lentitud.CONTADORES["bloqueos"] == 1
    lentitud.contar("lentos")
    assert lentitud.CONTADORES["lentos"] == 1


def test_la_pagina_junta_cuentas_colas_y_trabajos(tmp_path):
    import app as bot
    from igbot.store import Store

    class Trabajo:
        def __init__(self, hecho, error=""):
            self.hecho, self.error = hecho, error

    async def main():
        store = Store(":memory:")
        await store.open()
        c = bot.principal
        antes, c.store = c.store, store
        await store.add_incoming("ana", "m1", "hola")
        salud._respuestas.clear()
        salud.anotar_respuesta("", 12.0)
        salud.fuentes.update(cuentas=lambda: [c], dispatchers=lambda: {"": bot.dispatchers[""]},
                             trabajos=lambda: {"a": Trabajo(False), "b": Trabajo(True, "falló"), "c": Trabajo(True)})
        (tmp_path / "2026-10-31").mkdir()
        (tmp_path / "2026-11-01").mkdir()
        monkeypatch_respaldo = salud.ultimo_respaldo(tmp_path)
        d = await salud.datos()
        await store.close()
        c.store = antes
        return d, monkeypatch_respaldo

    d, respaldo = asyncio.run(main())
    fila = d["cuentas"][0]
    assert fila["slug"] == "principal" and fila["esperando"] == 1 and fila["respuestas"] == 1 and fila["p50"] == 12.0
    assert d["trabajos"] == {"en_curso": 1, "fallidos": 1}
    assert respaldo == "2026-11-01"
    pagina = salud.html(d)
    assert "12.0 s" in pagina and "trabajos fallidos" in pagina and "principal" in pagina
