"""Horario de atención de tu equipo en Configura tu asistente > Tu equipo (citas y "fuera de horario")."""
from igbot import ajustes  # noqa: E402


def test_revisa_dias_y_horas():
    assert ajustes.horario_error({}, []) == ""  # el formulario no lo traía: se deja lo guardado
    assert ajustes.horario_error({"desde": "09:00", "hasta": "18:00"}, []) == "Elige al menos un día de atención."
    assert "cierre" in ajustes.horario_error({"desde": "18:00", "hasta": "09:00"}, ["1"])
    assert "inicio" in ajustes.horario_error({"desde": "", "hasta": "09:00"}, ["1"])
    assert ajustes.horario_error({"desde": "09:00", "hasta": "18:00"}, ["1", "6"]) == ""


def test_se_configura_en_tu_equipo(monkeypatch, tmp_path):
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
        url = "/panel/asistente/configura?paso=equipo"
        assert client.get(url, follow_redirects=False).status_code == 303  # sin sesión
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        client.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        c = bot.buscar("1784")
        r = client.get(url)
        assert "Horario de atención de tu equipo" in r.text
        assert 'name="dias" value="1" checked' in r.text and 'name="dias" value="6">' in r.text
        assert 'name="desde" aria-label="Desde" step="900" value="09:00"' in r.text
        campos_con_nombre(r.text)
        datos = {"paso": "equipo", "dias": ["6", "7"], "desde": "14:00", "hasta": "10:00"}
        assert client.post("/panel/asistente/paso", data=datos).status_code == 403  # sin Origin
        r = client.post("/panel/asistente/paso", headers=ORIGEN, data=datos)
        assert "La hora de cierre debe ser después de la de inicio." in r.text
        assert c.business_hours == "09:00-18:00"
        r = client.post("/panel/asistente/paso", headers=ORIGEN, follow_redirects=False,
                        data=datos | {"desde": "10:00", "hasta": "14:00"})
        assert r.status_code == 303
        assert (c.business_days, c.business_hours) == ([6, 7], "10:00-14:00")
        assert "Atiende tu equipo: sáb, dom 10:00–14:00" in client.get(
            "/panel/asistente/configura?paso=resumen").text
        # El asistente lo sabe y las citas lo respetan (tools.business_window lee la cuenta).
        assert "sáb, dom 10:00–14:00" in ajustes.prompt_horario(c)
