"""Lote 2 de la auditoría: sesiones con secreto propio y cierre total, enlaces de un solo uso en la base,
firma de Mercado Pago, topes de generación y de correos, script de borrado."""
import asyncio
import hashlib
import hmac
import importlib.util
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("IG_VERIFY_TOKEN", "vt")
os.environ.setdefault("IG_APP_SECRET", "secreto")
os.environ.setdefault("IG_ACCESS_TOKEN", "tok")
os.environ.setdefault("DB_PATH", ":memory:")

import pytest  # noqa: E402

from igbot import cobros, config, limites, unavez  # noqa: E402
from igbot.store import Store  # noqa: E402
from igbot import marco  # noqa: E402


def corre(coro):
    return asyncio.run(coro)


def cookie(valor: str):
    return SimpleNamespace(cookies={marco.COOKIE: valor})


def test_sesion_nueva_y_vieja(monkeypatch):
    monkeypatch.setattr(config, "SESSION_SECRET", "propio")
    monkeypatch.setattr(config, "APP_SECRET", "meta")
    monkeypatch.setattr(marco, "VERSIONES", {})
    r = SimpleNamespace(cookies={})

    def set_cookie(k, v, **kw):
        r.cookies[k] = v
    r.set_cookie = set_cookie
    marco.iniciar_sesion(r, "222")
    assert marco.sesion(cookie(r.cookies[marco.COOKIE])) == "222"
    assert r.cookies[marco.COOKIE].count(".") == 3
    # Cookie de antes (3 partes, firmada con el secreto de Meta): sigue valiendo hasta que se cierren las sesiones.
    vence = int(time.time()) + 1000
    vieja_key = hmac.new(b"meta", b"panel-sesion", hashlib.sha256).digest()
    firma = hmac.new(vieja_key, f"222.{vence}".encode(), hashlib.sha256).hexdigest()
    assert marco.sesion(cookie(f"222.{vence}.{firma}")) == "222"
    # Firmada con otro secreto, o alterada: no.
    assert marco.sesion(cookie(f"333.{vence}.{firma}")) is None
    v = r.cookies[marco.COOKIE]
    assert marco.sesion(cookie(v[:-1] + ("1" if v[-1] == "0" else "0"))) is None
    # Cerrar sesiones: sube la versión y ya no vale ni la nueva ni la vieja.
    marco.VERSIONES["222"] = "1"
    assert marco.sesion(cookie(r.cookies[marco.COOKIE])) is None
    assert marco.sesion(cookie(f"222.{vence}.{firma}")) is None


def test_cerrar_sesiones_guarda_la_version(tmp_path, monkeypatch):
    async def _t():
        monkeypatch.setattr(marco, "VERSIONES", {})
        store = Store(str(tmp_path / "a.db"))
        await store.open()
        c = SimpleNamespace(ig_account_id="222", store=store)
        await marco.cargar_version(c)
        assert marco.version_de("222") == "0"
        await marco.cerrar_sesiones(c)
        assert marco.version_de("222") == "1" and await store.get("sesion_version") == "1"
        marco.VERSIONES.clear()
        await marco.cargar_version(c)  # al reiniciar se vuelve a leer
        assert marco.version_de("222") == "1"
        await store.close()
    corre(_t())


def test_unavez_gastar_y_cabe(tmp_path):
    async def _t():
        store = Store(str(tmp_path / "a.db"))
        await store.open()
        assert await unavez.gastar(store, "enlace:x", 100, ahora=1000)
        assert not await unavez.gastar(store, "enlace:x", 100, ahora=1050)  # ya usado
        assert await unavez.gastar(store, "enlace:x", 100, ahora=1200)  # venció: se puede volver a gastar (otro enlace igual)
        for i in range(3):
            assert await unavez.cabe(store, "gen", 3, 3600, ahora=2000 + i)
        assert not await unavez.cabe(store, "gen", 3, 3600, ahora=2010)
        assert await unavez.cabe(store, "gen", 3, 3600, ahora=2000 + 3601)  # pasó la ventana
        assert await unavez.gastar(None, "sin base", 10)  # sin base no frena nada
        await store.close()
    corre(_t())


def test_tope_de_generaciones(tmp_path, monkeypatch):
    async def _t():
        store = Store(str(tmp_path / "a.db"))
        await store.open()
        monkeypatch.setattr(config, "GENERACIONES_HORA", 2)
        assert await limites.cabe_generacion(store, ahora=10) and await limites.cabe_generacion(store, ahora=11)
        assert not await limites.cabe_generacion(store, ahora=12)
        monkeypatch.setattr(config, "GENERACIONES_HORA", 0)
        assert await limites.cabe_generacion(store, ahora=13)
        await store.close()
    corre(_t())


def test_firma_de_mercado_pago(monkeypatch):
    monkeypatch.setattr(config, "MP_WEBHOOK_SECRET", "clave-mp")
    ts = "1700000000"
    v1 = hmac.new(b"clave-mp", f"id:555;request-id:req-1;ts:{ts};".encode(), hashlib.sha256).hexdigest()
    cab = f"ts={ts},v1={v1}"
    assert cobros.firma_mp_valida("555", cab, "req-1", ahora=1700000100)
    assert not cobros.firma_mp_valida("556", cab, "req-1", ahora=1700000100)  # otro pago
    assert not cobros.firma_mp_valida("555", cab, "req-2", ahora=1700000100)  # otro request-id
    assert not cobros.firma_mp_valida("555", cab, "req-1", ahora=1700009000)  # vieja
    assert not cobros.firma_mp_valida("555", "", "req-1", ahora=1700000100)


def test_tope_de_avisos_de_mercado_pago(monkeypatch):
    monkeypatch.setattr(cobros, "_avisos_mp", [])
    monkeypatch.setattr(cobros, "MAX_AVISOS_MP_MINUTO", 2)
    assert cobros._cabe_aviso_mp(ahora=100) and cobros._cabe_aviso_mp(ahora=101)
    assert not cobros._cabe_aviso_mp(ahora=102)
    assert cobros._cabe_aviso_mp(ahora=170)


def test_correos_totales(tmp_path):
    async def _t():
        store = Store(str(tmp_path / "a.db"))
        await store.open()
        await store.log_email("ana", "ana@x.com", "hola")
        await store.log_email("bob", "bob@x.com", "hola")
        assert await store.emails_total_since(time.time() - 60) == 2
        assert await store.emails_since("ana", time.time() - 60) == 1
        await store.close()
    corre(_t())


def test_borrar_cuenta(tmp_path):
    spec = importlib.util.spec_from_file_location("borrar_cuenta", Path(__file__).resolve().parent.parent / "scripts" / "borrar_cuenta.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "data" / "cafe" / "media").mkdir(parents=True)
    (tmp_path / "clientes" / "cafe").mkdir(parents=True)
    (tmp_path / "clientes" / "cafe" / ".env").write_text("IG_ACCESS_TOKEN=x\n")
    with sqlite3.connect(tmp_path / "data" / "bot.db") as db:
        db.execute("CREATE TABLE conexiones (ig_id TEXT, usuario TEXT, token TEXT, expires_at REAL, created_at REAL, slug TEXT)")
        db.execute("INSERT INTO conexiones VALUES ('1', 'cafe.lunar', 't', 0, 0, 'cafe')")
        db.execute("INSERT INTO conexiones VALUES ('2', 'otro', 't', 0, 0, 'otro')")
    assert len(mod.borrar("cafe", tmp_path, ver=True)) == 3
    assert (tmp_path / "clientes" / "cafe" / ".env").exists()
    assert len(mod.borrar("cafe", tmp_path)) == 3
    assert not (tmp_path / "data" / "cafe").exists() and not (tmp_path / "clientes" / "cafe").exists()
    with sqlite3.connect(tmp_path / "data" / "bot.db") as db:
        assert db.execute("SELECT slug FROM conexiones").fetchall() == [("otro",)]
    assert mod.borrar("cafe", tmp_path) == []
    with pytest.raises(SystemExit):
        mod.borrar("../x", tmp_path)
