"""Conecta tu Google Calendar: el permiso de cada cuenta y las citas en su calendario."""
import asyncio
import base64
import json
import os
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx  # noqa: E402
import pytest  # noqa: E402

import test_contenido  # noqa: E402
from igbot import agenda_google as ag, config, tools  # noqa: E402
from igbot.cuenta import usar  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_contenido import ORIGEN, bot  # noqa: E402
from test_tools import NOW, FakeGoogle  # noqa: E402

cliente = test_contenido.cliente


def id_token(email):
    cuerpo = base64.urlsafe_b64encode(json.dumps({"email": email, "email_verified": True}).encode()).decode().rstrip("=")
    return f"x.{cuerpo}.firma"


@pytest.fixture
def google(cliente, monkeypatch):
    """La app de Google configurada y un Google de mentira que cambia códigos y revoca."""
    monkeypatch.setattr(config, "GOOGLE_WEB_CLIENT_ID", "cliente.apps.googleusercontent.com")
    monkeypatch.setattr(config, "GOOGLE_WEB_CLIENT_SECRET", "secreto-google")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://console.skandar.online")
    estado = {"scope": f"openid email {ag.EVENTOS} {ag.LIBRE}", "revocados": []}

    def manejar(request: httpx.Request) -> httpx.Response:
        datos = parse_qs(request.content.decode())
        if str(request.url) == ag.REVOCAR:
            estado["revocados"].append(datos["token"][0])
            return httpx.Response(200)
        assert str(request.url) == ag.TOKEN and datos["code"] == ["codigo1"]
        assert datos["redirect_uri"] == ["https://console.skandar.online/panel/google/callback"]
        return httpx.Response(200, json={"access_token": "ya29", "refresh_token": "1//refresco", "scope": estado["scope"],
                                         "id_token": id_token("citas@cafelunar.mx")})

    monkeypatch.setattr(ag, "_transporte", httpx.MockTransport(manejar))
    monkeypatch.setattr(ag, "_calendarios", {})
    return (*cliente, estado)


def conectar(client):
    r = client.post("/panel/google/conectar", headers=ORIGEN, follow_redirects=False)
    destino = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(destino.query).items()}
    assert destino.hostname == "accounts.google.com"
    assert q["access_type"] == "offline" and q["scope"] == ag.SCOPES
    assert q["redirect_uri"] == "https://console.skandar.online/panel/google/callback"
    return client.get("/panel/google/callback?" + urlencode({"code": "codigo1", "state": q["state"]}),
                      follow_redirects=False)


def test_sin_llaves_no_sale(cliente, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_WEB_CLIENT_ID", "")
    client, *_ = cliente
    assert "Muy pronto" in client.get("/panel/google").text
    assert "Google Calendar" not in client.get("/panel/conexion").text


def test_conectar_ajustar_y_desconectar(google):
    client, c, _, _, estado = google
    assert "Sin conectar: tu asistente agendaría citas" in client.get("/panel/conexion").text
    assert "Conectar Google Calendar</button>" in client.get("/panel/google").text
    assert client.post("/panel/google/conectar").status_code == 403  # sin Origin
    assert client.get("/panel/google/callback?code=codigo1&state=falso",
                      follow_redirects=False).headers["location"] == "/panel/google?aviso=fallo"
    assert conectar(client).headers["location"] == "/panel/google?aviso=conectado"
    con = client.portal.call(ag.conexion, c)
    assert con["email"] == "citas@cafelunar.mx" and con["refresh_token"] == "1//refresco"
    assert con["tipo"] == "presencial" and con["minutos"] == 30
    assert "citas@cafelunar.mx · tu asistente agenda citas" in client.get("/panel/conexion").text
    r = client.post("/panel/google/guardar", headers=ORIGEN, data={"tipo": "video", "minutos": "45"})
    assert "Guardado" in r.text
    con = client.portal.call(ag.conexion, c)
    assert con["tipo"] == "video" and con["minutos"] == 45
    assert "Desconectamos tu calendario" in client.post("/panel/google/desconectar", headers=ORIGEN).text
    assert client.portal.call(ag.conexion, c) == {} and estado["revocados"] == ["1//refresco"]
    assert client.get("/panel/google/callback?error=access_denied",
                      follow_redirects=False).headers["location"] == "/panel/google?aviso=cancelado"


def test_sin_permiso_del_calendario_no_conecta(google):
    client, c, _, _, estado = google
    estado["scope"] = f"openid email {ag.LIBRE}"  # desmarcó ver y editar eventos
    assert conectar(client).headers["location"] == "/panel/google?aviso=permisos"
    assert client.portal.call(ag.conexion, c) == {} and estado["revocados"] == ["ya29"]


def test_el_asistente_agenda_en_su_calendario(google, monkeypatch):
    client, c, *_ = google
    conectar(client)
    vistos = {}

    async def fake_claude(prompt, system, mcp=None, allowed=(), max_turns=5, model=None):
        vistos.update(system=system, allowed=list(allowed))
        return "Claro"

    monkeypatch.setattr(bot.agent, "run_claude", fake_claude)

    async def contestar():
        with usar(c):
            return await bot.agent.reply(c.store, None, "ana", ["¿Tienen cita el martes?"])

    client.portal.call(contestar)
    assert "mcp__bot__agendar_reunion" in vistos["allowed"] and "mcp__bot__ver_disponibilidad" in vistos["allowed"]
    assert "mcp__bot__enviar_correo" not in vistos["allowed"]  # su calendario no manda correos
    assert "<agenda>" in vistos["system"] and "calendario del negocio (30 min)" in vistos["system"]
    # Sin calendario no hay herramientas de agenda.
    client.post("/panel/google/desconectar", headers=ORIGEN)
    client.portal.call(contestar)
    assert "mcp__bot__agendar_reunion" not in vistos["allowed"] and "<agenda>" not in vistos["system"]


def test_cita_en_el_negocio_sin_correo(monkeypatch):
    monkeypatch.setattr(tools, "now", lambda: NOW)

    async def probar():
        store = Store(":memory:")
        await store.open()
        cal = FakeGoogle()
        por_nombre = {t.name: t.handler for t in tools.workspace_tools(store, cal, "ana", correo=False,
                                                                       presencial=True, minutos=60)}
        assert "enviar_correo" not in por_nombre
        libres = await por_nombre["ver_disponibilidad"]({"fecha": "2026-10-15"})
        r = await por_nombre["agendar_reunion"]({"fecha": "2026-10-15", "hora": "10:00", "email": "",
                                                  "nombre": "Ana", "motivo": "Corte"})
        otro = await por_nombre["agendar_reunion"]({"fecha": "2026-10-15", "hora": "12:00", "email": "x@y.com",
                                                     "nombre": "Ana", "motivo": ""})
        await store.close()
        return libres, r, otro, cal

    libres, r, otro, cal = asyncio.run(probar())
    assert "(60 min," in libres["content"][0]["text"] and "09:00, 10:00" in libres["content"][0]["text"]
    assert "Cita agendada para el" in r["content"][0]["text"] and cal.created[0][1] == ""
    assert otro.get("is_error")  # un correo que la persona no escribió en el chat no se usa
