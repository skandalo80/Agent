"""Botones de inicio (ice breakers) desde el panel, sin red."""
import asyncio
import json
from types import SimpleNamespace

from igbot import botones  # noqa: E402
from igbot.store import Store  # noqa: E402


class FakeIG:
    def __init__(self, puestos=None, falla=False):
        self.calls, self.puestos, self.falla = [], puestos or [], falla

    async def set_ice_breakers(self, questions):
        if self.falla:
            raise RuntimeError("caído")
        self.calls.append(questions)

    async def delete_ice_breakers(self):
        self.calls.append("quitar")

    async def ice_breakers(self):
        return self.puestos

    async def close(self):
        pass


def cuenta_falsa(ig, slug="cafe_sin_archivo"):
    return SimpleNamespace(slug=slug, store=Store(":memory:"), instagram=ig)


def test_limpia_vacios_repetidos_largo_y_maximo_cuatro():
    largo = "x" * 120
    assert botones.limpiar(["  ¿Precios? ", "", "¿Precios?", largo, "a", "b", "c"]) == [
        "¿Precios?", "x" * 80, "a", "b"]


def test_se_mandan_al_guardar_y_solo_si_cambiaron():
    async def main():
        ig = FakeIG()
        c = cuenta_falsa(ig)
        await c.store.open()
        await botones.guardar(c, ["¿Precios?", "¿Horario?"])
        await botones.sync(c)  # reinicio sin cambios: no se vuelve a mandar
        await botones.guardar(c, ["¿Precios?"])
        await botones.guardar(c, [])  # los borró todos: se quitan de Instagram
        guardados = await botones.de(c.store)
        await c.store.close()
        return ig.calls, guardados

    assert asyncio.run(main()) == ([["¿Precios?", "¿Horario?"], ["¿Precios?"], "quitar"], [])


def test_la_primera_vez_toma_los_que_instagram_ya_tiene_sin_volver_a_mandarlos():
    async def main():
        ig = FakeIG(puestos=["¿Cuánto cuesta?", "Quiero una demo"])
        c = cuenta_falsa(ig)
        await c.store.open()
        await botones.sync(c)
        guardados = await botones.de(c.store)
        await c.store.close()
        return ig.calls, guardados

    assert asyncio.run(main()) == ([], ["¿Cuánto cuesta?", "Quiero una demo"])


def test_la_primera_vez_toma_los_del_archivo_de_antes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clientes/viejo").mkdir(parents=True)
    (tmp_path / "clientes/viejo/botones.yaml").write_text("preguntas:\n  - ¿Envíos?\n", encoding="utf-8")

    async def main():
        ig = FakeIG(puestos=["otra"])
        c = cuenta_falsa(ig, slug="viejo")
        await c.store.open()
        await botones.sync(c)
        guardados = await botones.de(c.store)
        await c.store.close()
        return ig.calls, guardados

    assert asyncio.run(main()) == ([["¿Envíos?"]], ["¿Envíos?"])


def test_sin_botones_nunca_no_se_llama_a_instagram_y_si_falla_se_reintenta():
    async def main():
        ig = FakeIG(falla=True)
        c = cuenta_falsa(ig)
        await c.store.open()
        await botones.sync(c)  # Instagram no tiene ninguno: se guarda la lista vacía sin llamar
        await botones.guardar(c, ["¿Precios?"])  # falla: no se anota como mandado
        ig.falla = False
        await botones.sync(c)  # el siguiente arranque lo vuelve a intentar
        hash_ok = await c.store.get("botones_hash")
        await c.store.close()
        return ig.calls, hash_ok

    calls, hash_ok = asyncio.run(main())
    assert calls == [["¿Precios?"]] and hash_ok


def test_el_panel_muestra_y_guarda_los_botones(monkeypatch, tmp_path):
    from pathlib import Path
    from urllib.parse import parse_qs, urlparse

    from fastapi.testclient import TestClient

    import app as bot
    from test_conectar import setup
    from test_cuenta import FakeInstagram
    from test_panel import ORIGEN, campos_con_nombre

    monkeypatch.chdir(tmp_path)
    Path("prompts").mkdir()
    setup(monkeypatch)
    monkeypatch.setattr(bot, "Instagram", lambda store: FakeInstagram())
    monkeypatch.setattr(bot, "cuentas", list(bot.cuentas))
    monkeypatch.setattr(bot, "dispatchers", dict(bot.dispatchers))
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")

    with TestClient(bot.app, base_url="https://testserver") as client:
        url = "/panel/asistente/configura?paso=reglas"
        assert client.get(url, follow_redirects=False).status_code == 303  # sin sesión
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        c.instagram = FakeIG()
        r = client.get(url)
        assert "Botones al abrir un chat nuevo" in r.text and 'name="b_4"' in r.text
        campos_con_nombre(r.text)
        datos = {"paso": "reglas", "bi": "1", "b_1": "¿Precios?", "b_3": "¿Envíos?", "accion": "aplicar"}
        assert client.post("/panel/asistente/paso", data=datos).status_code == 403  # sin Origin
        r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False, data=datos)
        assert r.status_code == 303
        assert json.loads(client.portal.call(c.store.get, "botones")) == ["¿Precios?", "¿Envíos?"]
        assert c.instagram.calls == [["¿Precios?", "¿Envíos?"]]
        r = client.get(url)
        assert 'value="¿Precios?"' in r.text and 'value="¿Envíos?"' in r.text
        assert "Botones al abrir un chat: ¿Precios?, ¿Envíos?" in client.get(
            "/panel/asistente/configura?paso=resumen").text
