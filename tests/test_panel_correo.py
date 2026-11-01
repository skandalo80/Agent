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
        monkeypatch.setattr(panel, "buscar", lambda ig_id: SimpleNamespace(ig_account_id="222") if ig_id == "222" else None)
        assert "Entrar con mi correo" in client.get("/panel").text
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
        b64, resto = t.split(".", 1)
        falso = acceso._b64("i:111") + "." + resto
        assert "ya no sirve" in otro.get(f"/panel/correo/entrar?t={falso}").text


def test_correo_desconocido_y_limite(monkeypatch):
    with TestClient(bot.app, base_url="https://testserver") as client:
        google = preparar(monkeypatch)
        r = client.post("/panel/correo", headers=ORIGEN, data={"correo": "nadie@x.com"})
        assert "Revisa tu correo" in r.text  # la misma respuesta: no dice si existe
        (para, cuerpo), = google.correos  # le llega el enlace para crear su cuenta
        assert para == "nadie@x.com" and "crear tu cuenta" in cuerpo
        google.correos.clear()
        assert "le falta algo" in client.post("/panel/correo", headers=ORIGEN, data={"correo": "nada"}).text
        for _ in range(5):
            client.post("/panel/correo", headers=ORIGEN, data={"correo": "ana@cafe.mx"})
        assert len(google.correos) == acceso.MAX_POR_CORREO
        assert client.post("/panel/correo", data={"correo": "ana@cafe.mx"}).status_code == 403  # otro sitio


def test_enlace_vencido(monkeypatch):
    preparar(monkeypatch)
    t = acceso.nuevo_enlace("i:222", ahora=0)
    assert acceso.de_enlace(t) is None
    assert acceso.de_enlace(acceso.nuevo_enlace("i:222")) == "i:222"
    assert acceso.de_enlace(acceso.nuevo_enlace("c:ana.b@cafe.mx")) == "c:ana.b@cafe.mx"


class FakeStore:
    def __init__(self):
        self.kv = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v):
        self.kv[k] = v


def test_correo_nuevo_enlaza_su_instagram(monkeypatch):
    """Correo sin cuenta: tras el enlace, el paso de enlazar Instagram; al volver, el correo queda en su cuenta."""
    with TestClient(bot.app, base_url="https://testserver") as client:
        google = preparar(monkeypatch)
        client.post("/panel/correo", headers=ORIGEN, data={"correo": "nuevo@tienda.mx"})
        (_, cuerpo), = google.correos
        t = re.search(r"/panel/correo/entrar\?t=(\S+)", cuerpo).group(1)
        assert "Crea tu cuenta" in client.get(f"/panel/correo/entrar?t={t}").text
        r = client.post("/panel/correo/entrar", headers=ORIGEN, data={"t": t}, follow_redirects=False)
        assert r.headers["location"] == "/panel/correo/conecta" and panel.COOKIE not in r.cookies
        pagina = client.get("/panel/correo/conecta").text
        assert "Conecta tu Instagram" in pagina and "/conectar/instagram" in pagina
        # En el iPhone, Instagram abre su app y se atora: no se ofrece "Solo con Instagram", queda la nota de Meta.
        iphone = client.get("/panel/correo/conecta", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"})
        assert "/conectar/instagram" not in iphone.text and "Con Meta entras con tu Instagram" in iphone.text

        # Enlazó su Instagram (cuenta nueva sin correo propio: tiene el tuyo por omisión).
        nueva = SimpleNamespace(slug="tienda", owner_email="yo@gmail.com", ig_account_id="444", store=FakeStore(),
                                human_pause_hours=12)
        monkeypatch.setattr(panel, "buscar", lambda ig_id: nueva if ig_id == "444" else None)

        async def inicio(c):
            return "/panel/bienvenida"

        monkeypatch.setattr(panel, "inicio", inicio)
        r = client.get("/panel", cookies={panel.COOKIE: _cookie("444")}, follow_redirects=False)
        assert r.status_code == 303 and nueva.owner_email == "nuevo@tienda.mx"
        assert "nuevo@tienda.mx" in nueva.store.kv["ajustes"]


def _cookie(ig_id):
    from fastapi.responses import Response
    r = Response()
    panel.iniciar_sesion(r, ig_id)
    return r.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def test_pantalla_de_entrar_sin_instagram(monkeypatch):
    from igbot import agenda_google, facebook
    monkeypatch.setattr(agenda_google, "enabled", lambda: True)
    monkeypatch.setattr(facebook, "enabled", lambda: True)
    with TestClient(bot.app, base_url="https://testserver") as client:
        html = client.get("/panel", cookies={acceso.COOKIE_ULTIMO: "google"}).text
        assert "Entrar con Google" in html and "Entrar con Facebook" in html and "Entrar con mi correo" in html
        assert "/conectar/instagram" not in html
        assert html.count("Último usado") == 1


def test_entrar_con_apple(monkeypatch, tmp_path):
    import base64 as b64
    import json as js

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    llave = ec.generate_private_key(ec.SECP256R1())
    p8 = tmp_path / "apple.p8"
    p8.write_bytes(llave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    monkeypatch.setattr(config, "APPLE_SERVICES_ID", "online.skandar.console")
    monkeypatch.setattr(config, "APPLE_TEAM_ID", "EQUIPO")
    monkeypatch.setattr(config, "APPLE_KEY_ID", "LLAVE")
    monkeypatch.setattr(config, "APPLE_KEY_PATH", str(p8))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    secreto = acceso.apple_secreto()
    datos = jwt.decode(secreto, llave.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com")
    assert datos["iss"] == "EQUIPO" and datos["sub"] == "online.skandar.console"
    assert jwt.get_unverified_header(secreto)["kid"] == "LLAVE"

    def token(d):
        return "x." + b64.urlsafe_b64encode(js.dumps(d).encode()).decode().rstrip("=") + ".y"

    assert acceso._correo_del_token(token({"email": "Ana@Cafe.mx", "email_verified": "true"})) == "ana@cafe.mx"
    assert acceso._correo_del_token(token({"email": "a@b.mx", "email_verified": False})) == ""
    with TestClient(bot.app, base_url="https://testserver") as client:
        assert "Entrar con Apple" in client.get("/panel").text
        r = client.get("/panel/correo/apple", follow_redirects=False)
        assert r.headers["location"].startswith("https://appleid.apple.com/auth/authorize?")
        # Sin el state de su cookie no entra.
        r = client.post("/panel/correo/apple/vuelta", data={"code": "c", "state": "apple.1.x.y"},
                        follow_redirects=False)
        assert panel.COOKIE not in r.cookies and "No pudimos entrar con Apple" in r.text
