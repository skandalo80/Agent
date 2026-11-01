"""Salud del servicio (/panel/salud): números medidos en el proceso, sin red."""
import asyncio
import os
import time

for k, v in dict(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:",
                 DEBOUNCE_SECONDS="0.2").items():
    os.environ.setdefault(k, v)

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


def test_las_alertas_se_mandan_una_vez_por_hora_y_la_cola_solo_si_sigue_alta(monkeypatch, tmp_path):
    avisos = []

    async def avisar(asunto, texto):
        avisos.append(asunto)

    monkeypatch.setattr(salud, "avisar", avisar)
    monkeypatch.setattr(salud, "memoria", lambda: {"proceso_mb": 300, "hijos": 4, "hijos_mb": 600, "libre_mb": 50, "total_mb": 954})
    monkeypatch.setattr(salud, "ultimo_respaldo", lambda carpeta=None: "2026-10-20")
    monkeypatch.setattr(salud, "fuentes", {"cuentas": lambda: [], "dispatchers": lambda: {}, "trabajos": lambda: {}})
    salud._avisado.clear()
    t0 = time.mktime((2026, 11, 1, 12, 0, 0, 0, 0, -1))
    assert asyncio.run(salud.revisar(t0)) == ["poca memoria", "respaldo atrasado"]
    assert asyncio.run(salud.revisar(t0 + 60)) == []  # una hora de silencio
    assert asyncio.run(salud.revisar(t0 + 3601)) == ["poca memoria", "respaldo atrasado"]
    assert avisos == ["Skandar: poca memoria", "Skandar: respaldo atrasado"] * 2


def test_la_cola_atascada_avisa_a_la_segunda_revision_seguida(monkeypatch):
    import app as bot
    from igbot.store import Store
    avisos = []

    async def avisar(asunto, texto):
        avisos.append(asunto)

    async def main():
        store = Store(":memory:")
        await store.open()
        for i in range(25):
            await store.add_incoming(f"u{i}", f"m{i}", "hola")
        c = bot.principal
        antes, c.store = c.store, store
        monkeypatch.setattr(salud, "avisar", avisar)
        monkeypatch.setattr(salud, "memoria", lambda: {"proceso_mb": 1, "hijos": 0, "hijos_mb": 0, "libre_mb": 900, "total_mb": 954})
        monkeypatch.setattr(salud, "ultimo_respaldo", lambda carpeta=None: "")
        monkeypatch.setattr(salud, "fuentes", {"cuentas": lambda: [c], "dispatchers": lambda: {}, "trabajos": lambda: {}})
        salud._avisado.clear()
        salud._cola_alta = False
        primera = await salud.revisar()
        segunda = await salud.revisar()
        await store.close()
        c.store = antes
        return primera, segunda

    primera, segunda = asyncio.run(main())
    assert primera == [] and segunda == ["cola atascada"] and avisos == ["Skandar: cola atascada"]


def test_tres_arranques_en_una_hora_se_cuentan_en_la_base():
    from igbot.store import Store

    async def main():
        store = Store(":memory:")
        await store.open()
        t = 1_700_000_000.0
        n = [await salud.anotar_arranque(store, t), await salud.anotar_arranque(store, t + 600),
             await salud.anotar_arranque(store, t + 1200), await salud.anotar_arranque(store, t + 4000)]
        await store.close()
        return n

    assert asyncio.run(main()) == [1, 2, 3, 3]  # a los 4000 s el primero ya no cuenta; los otros dos sí
