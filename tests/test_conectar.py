"""Página de conectar clientes: formulario y OAuth de Instagram, sin red."""
import os
import time
from urllib.parse import parse_qs, urlparse

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import conectar  # noqa: E402


class FakeResponse:
    is_error = False

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeHTTP:
    calls = []

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, data=None):
        self.calls.append(("post", url, data))
        return FakeResponse({"data": [{"access_token": "corto", "user_id": 1}]})

    async def get(self, url, params=None):
        self.calls.append(("get", url, params))
        if url.endswith("/access_token"):
            return FakeResponse({"access_token": "largo", "expires_in": 5184000})
        return FakeResponse({"user_id": "1784", "username": "cafe.lunar"})


def setup(monkeypatch):
    monkeypatch.setattr(bot.config, "IG_APP_ID", "123")
    monkeypatch.setattr(bot.config, "PUBLIC_URL", "https://console.skandar.online")
    monkeypatch.setattr(cuenta(), "admin_ig_ids", {"jefe"})
    monkeypatch.setattr(conectar.httpx, "AsyncClient", FakeHTTP)
    avisos = []

    async def send_text(user_id, text):
        avisos.append((user_id, text))

    monkeypatch.setattr(bot.instagram, "send_text", send_text)
    return avisos


def test_formulario_guarda_la_solicitud_y_avisa(monkeypatch):
    avisos = setup(monkeypatch)
    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.post("/conectar/solicitud", follow_redirects=False, data={
            "usuario": "https://www.instagram.com/Cafe.Lunar/", "nombre": "Ana", "correo": "ana@cafe.mx",
        })
        assert r.status_code == 303
        assert r.headers["location"] == "https://skandar.online/conectar?enviado=1#paso-2"
        assert avisos and "cafe.lunar" in avisos[0][1] and avisos[0][0] == "jefe"

        r = client.post("/conectar/solicitud", follow_redirects=False,
                        data={"usuario": "no válido!", "nombre": "Ana", "correo": "ana@cafe.mx"})
        assert "error=datos" in r.headers["location"]
        # Un bot que llena el campo oculto no deja solicitud ni aviso.
        client.post("/conectar/solicitud", data={"usuario": "spam", "nombre": "x", "correo": "a@b.co", "sitio_web": "x"})
        assert len(avisos) == 1


def test_oauth_guarda_el_token_de_la_cuenta(monkeypatch):
    avisos = setup(monkeypatch)

    async def sin_panel(*a):
        return None  # sin cuenta (no se pudo dar de alta): va a la página de "conectado"

    monkeypatch.setattr(bot, "alta", sin_panel)
    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/conectar/instagram", follow_redirects=False)
        url = urlparse(r.headers["location"])
        q = parse_qs(url.query)
        assert url.netloc == "api.instagram.com"
        assert q["redirect_uri"] == ["https://console.skandar.online/conectar/callback"]
        state = q["state"][0]

        # Un state que no firmamos (enlace armado por alguien más) no conecta nada.
        other = TestClient(bot.app, base_url="https://testserver")
        falso = state[:-4] + ("0000" if not state.endswith("0000") else "1111")
        r = other.get(f"/conectar/callback?code=abc&state={falso}", follow_redirects=False)
        assert r.headers["location"].endswith("/conectar-error")

        r = client.get(f"/conectar/callback?code=abc%23_&state={state}", follow_redirects=False)
        assert r.headers["location"] == "https://skandar.online/conectado"
        assert FakeHTTP.calls[0][2]["code"] == "abc"
        assert any("@cafe.lunar conectó" in t for _, t in avisos)

        r = client.get("/conectar/callback?error=access_denied", follow_redirects=False)
        assert r.headers["location"].endswith("/conectar-error")

        # Cada state sirve una vez.
        r = other.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert r.headers["location"].endswith("/conectar-error")


def test_oauth_vuelve_en_otro_navegador(monkeypatch):
    """En el cel Instagram regresa en su propio navegador, sin la cookie: vale el state firmado."""
    setup(monkeypatch)

    async def sin_panel(*a):
        return None

    monkeypatch.setattr(bot, "alta", sin_panel)
    with TestClient(bot.app, base_url="https://testserver") as client:
        r = client.get("/conectar/instagram", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        otro = TestClient(bot.app, base_url="https://testserver")
        r = otro.get(f"/conectar/callback?code=abc&state={state}", follow_redirects=False)
        assert r.headers["location"] == "https://skandar.online/conectado"

        # Uno firmado hace más de 15 minutos ya no sirve.
        viejo = f"{int(time.time()) - 1000}.xyz"
        r = otro.get(f"/conectar/callback?code=abc&state={viejo}.{conectar.firmar(viejo)}",
                     follow_redirects=False)
        assert r.headers["location"].endswith("/conectar-error")


def test_token_queda_en_la_base(monkeypatch):
    setup(monkeypatch)
    import asyncio

    async def main():
        from igbot.store import Store
        store = Store(":memory:")
        await store.open()
        await store.save_conexion("1784", "cafe.lunar", "viejo", 1)
        await store.save_conexion("1784", "cafe.lunar", "nuevo", 2)
        cur = await store.db.execute("SELECT token, expires_at FROM conexiones")
        assert await cur.fetchall() == [("nuevo", 2)]
        await store.close()

    asyncio.run(main())
