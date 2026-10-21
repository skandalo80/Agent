"""Entrar al panel con un enlace por correo (en el iPhone, Instagram no deja entrar desde Safari)."""
import os
import re
from types import SimpleNamespace

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app as bot  # noqa: E402
from igbot import acceso, conectar, config, panel  # noqa: E402

ORIGEN = {"Origin": "https://console.skandar.online"}


class FakeGoogle:
    def __init__(self):
        self.correos = []

    async def send_email(self, sender, name, to, cc, subject, body, attachments=()):
        self.correos.append((to, body))


def preparar(monkeypatch):
    principal = SimpleNamespace(slug="", owner_email="yo@gmail.com", ig_account_id="111")
    cliente = SimpleNamespace(slug="cafe", owner_email="Ana@Cafe.mx", ig_account_id="222")
    sin_correo = SimpleNamespace(slug="pan", owner_email="yo@gmail.com", ig_account_id="333")
    cuentas = [principal, cliente, sin_correo]
    google = FakeGoogle()
    monkeypatch.setattr(config, "MAIL_FROM", "yo@gmail.com")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    monkeypatch.setattr(acceso, "todas", lambda: cuentas)
    monkeypatch.setattr(acceso, "_enviados", [])
    monkeypatch.setattr(acceso, "_usados", {})
    monkeypatch.setattr(conectar, "google", google)

    async def usuario(c):
        return c.slug or "principal"

    monkeypatch.setattr(panel, "usuario", usuario)
    return google


def test_cuenta_de_correo(monkeypatch):
    preparar(monkeypatch)
    assert acceso.cuenta_de_correo(" ana@cafe.MX ").ig_account_id == "222"
    # Tu correo entra a tu cuenta, nunca a la de un cliente que no tiene correo propio.
    assert acceso.cuenta_de_correo("yo@gmail.com").ig_account_id == "111"
    assert acceso.cuenta_de_correo("otro@x.com") is None


def test_enlace_por_correo_entra_una_sola_vez(monkeypatch):
    with TestClient(bot.app, base_url="https://testserver") as client:
        google = preparar(monkeypatch)
        monkeypatch.setattr(panel, "buscar", lambda ig_id: SimpleNamespace() if ig_id == "222" else None)
        assert "Entra con tu correo" in client.get("/panel").text
        r = client.post("/panel/correo", headers=ORIGEN, data={"correo": "ana@cafe.mx"})
        assert "Revisa tu correo" in r.text
        (para, cuerpo), = google.correos
        assert para == "ana@cafe.mx"
        t = re.search(r"/panel/correo/entrar\?t=(\S+)", cuerpo).group(1)

        # Abrir el enlace no lo gasta (los correos que revisan enlaces solos no lo usan).
        assert "Entrar</button>" in client.get(f"/panel/correo/entrar?t={t}").text
        assert "Entrar</button>" in client.get(f"/panel/correo/entrar?t={t}").text

        r = client.post("/panel/correo/entrar", headers=ORIGEN, data={"t": t}, follow_redirects=False)
        assert r.headers["location"] == "/panel"
        assert panel.sesion(SimpleNamespace(cookies={panel.COOKIE: r.cookies[panel.COOKIE]})) == "222"

        # Ya usado, o alterado: no entra.
        otro = TestClient(bot.app, base_url="https://testserver")
        r = otro.post("/panel/correo/entrar", headers=ORIGEN, data={"t": t}, follow_redirects=False)
        assert "ya no sirve" in r.text and panel.COOKIE not in r.cookies
        falso = t.replace(".222.", ".111.")
        assert "ya no sirve" in otro.get(f"/panel/correo/entrar?t={falso}").text


def test_correo_desconocido_y_limite(monkeypatch):
    with TestClient(bot.app, base_url="https://testserver") as client:
        google = preparar(monkeypatch)
        r = client.post("/panel/correo", headers=ORIGEN, data={"correo": "nadie@x.com"})
        assert "Revisa tu correo" in r.text and google.correos == []  # no dice si existe
        assert "le falta algo" in client.post("/panel/correo", headers=ORIGEN, data={"correo": "nada"}).text
        for _ in range(5):
            client.post("/panel/correo", headers=ORIGEN, data={"correo": "ana@cafe.mx"})
        assert len(google.correos) == acceso.MAX_POR_CORREO
        assert client.post("/panel/correo", data={"correo": "ana@cafe.mx"}).status_code == 403  # otro sitio


def test_enlace_vencido(monkeypatch):
    preparar(monkeypatch)
    t = acceso.nuevo_enlace("222", ahora=0)
    assert acceso.de_enlace(t) is None
    assert acceso.de_enlace(acceso.nuevo_enlace("222")) == "222"
