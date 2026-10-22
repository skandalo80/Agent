"""Cotizaciones, pasar a humano y pausa cuando contesta el dueño."""
import hashlib
import hmac
import json
import os
import time

os.environ.update(
    IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok",
    DB_PATH=":memory:", DEBOUNCE_SECONDS="0.2",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import catalog, config, tools  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_tools import NOW, FakeGoogle, run, text  # noqa: E402


class FakeInstagram:
    def __init__(self):
        self.files = []

    async def send_file(self, user_id, url):
        self.files.append((user_id, url))


def test_precios_salen_del_catalogo():
    q = catalog.quote("community", "anual", 2)
    # Sin instalación ni mes gratis: el primer pago ya es el servicio,
    # 10 mensualidades de 79 + 2 bloques x 15 x 10 meses
    assert q.primer_pago == q.recurrente == 790 + 300
    assert not any("Instalación" in c for c, _ in q.lineas)
    assert "Después: US$1,090 + IVA al año" in q.texto()
    q = catalog.quote("basico")
    assert (q.primer_pago, q.recurrente) == (29, 29)
    # También por nombre
    assert catalog.quote("Community Manager").paquete == "community"
    assert catalog.quote("Asistente virtual").paquete == "basico"
    with pytest.raises(ValueError):
        catalog.quote("pro_crm")  # ya no existe
    with pytest.raises(ValueError):
        catalog.quote("community", "semanal")
    with pytest.raises(ValueError):
        catalog.quote("community", "mensual", 99)


def test_el_prompt_lleva_el_catalogo(monkeypatch):
    prompt = bot.agent.system_prompt()
    assert "US$79 + IVA al mes" in prompt and "sin costo de instalación" in prompt and "antro, bar o restaurante:" in prompt
    assert "Pro CRM" not in prompt and "Primer mes gratis" not in prompt
    assert "nortex" not in prompt.lower()  # el vendedor no manda a nadie a la cuenta de Nortex
    cat = {**catalog.load(), "demo": {"giros": {"gimnasio": "horarios y membresías"}}}
    monkeypatch.setattr(catalog, "load", lambda: cat)
    assert "gimnasio: horarios y membresías" in bot.agent.system_prompt()


@pytest.fixture
def sales(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "now", lambda: NOW)
    monkeypatch.setattr(config, "MAIL_FROM", "yo@gmail.com")
    monkeypatch.setattr(cuenta(), "mail_cc", "copia@gmail.com")
    monkeypatch.setattr(cuenta(), "owner_email", "yo@gmail.com")
    monkeypatch.setattr(cuenta(), "quotes_dir", str(tmp_path))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")

    async def make():
        store = Store(":memory:")
        await store.open()
        await store.add_incoming("ana", "m1", "mi correo es ana@mail.com")
        google, ig, state = FakeGoogle(), FakeInstagram(), {}
        all_tools = tools.quote_tools(store, google, ig, "ana", state) + tools.human_tools(
            store, google, "ana", state
        )
        return store, google, ig, state, {t.name: t.handler for t in all_tools}

    return make


def test_cotizacion_por_correo_y_dm(sales, tmp_path):
    async def main():
        store, google, ig, state, t = await sales()
        args = {"paquete": "community", "pago": "mensual", "bloques_extra": 0, "nombre": "Ana", "empresa": "",
                "canal": "ambos"}

        r = await t["enviar_cotizacion"]({**args, "email": "otra@mail.com"})
        assert r.get("is_error") and not google.sent  # correo que no escribió

        r = await t["enviar_cotizacion"]({**args, "email": "ana@mail.com"})
        assert "COT-0001" in text(r) and "Primer pago: US$79 + IVA" in text(r)
        assert google.sent == [("yo@gmail.com", "ana@mail.com", "copia@gmail.com", google.sent[0][3])]
        name, pdf = google.attachments[0]
        assert name == "Cotizacion-COT-0001.pdf" and pdf.startswith(b"%PDF")
        user, url = ig.files[0]
        token = url.rsplit("/", 1)[1].removesuffix(".pdf")
        assert user == "ana" and (tmp_path / f"{token}.pdf").read_bytes() == pdf
        assert state["acted"]
        await store.close()
        return token

    token = run(main())
    with TestClient(bot.app) as client:
        assert client.get(f"/cotizaciones/{token}.pdf").content.startswith(b"%PDF")
        assert client.get("/cotizaciones/no-existe-este-archivo-xx.pdf").status_code == 404
        assert client.get("/cotizaciones/..%2F..%2Fbot.pdf").status_code == 404


def test_cotizacion_solo_por_el_chat_sin_correo(sales, tmp_path):
    async def main():
        store, google, ig, state, t = await sales()
        args = {"paquete": "basico", "pago": "anual", "bloques_extra": 0, "nombre": "", "empresa": ""}

        r = await t["enviar_cotizacion"]({**args, "canal": "correo", "email": ""})
        assert r.get("is_error") and "correo" in text(r)  # por correo sin correo: lo pide
        r = await t["enviar_cotizacion"]({**args, "canal": "whatsapp", "email": ""})
        assert r.get("is_error")

        r = await t["enviar_cotizacion"]({**args, "canal": "chat", "email": ""})
        assert "COT-0001" in text(r) and "por este chat" in text(r)
        assert not google.sent and len(ig.files) == 1
        token = ig.files[0][1].rsplit("/", 1)[1].removesuffix(".pdf")
        assert (tmp_path / f"{token}.pdf").read_bytes().startswith(b"%PDF")
        assert state["acted"]
        await store.close()

    run(main())


def test_pasar_a_humano_pausa_y_avisa(sales):
    async def main():
        store, google, ig, state, t = await sales()
        await t["pasar_a_humano"]({"motivo": "quiere descuento"})
        assert await store.paused_until("ana") > time.time()
        assert google.sent[0][1] == "yo@gmail.com"
        await store.close()

    run(main())


def test_si_el_duenio_contesta_el_bot_se_calla(monkeypatch):
    replies = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        replies.append(new_messages)
        return "ok", False

    async def fake_send(user_id, text):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    monkeypatch.setattr(bot, "ECHO_WAIT", 0)

    def post(event):
        body = json.dumps({"entry": [{"messaging": [event]}]}).encode()
        sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
        client.post("/webhook", content=body, headers={"X-Hub-Signature-256": f"sha256={sig}"})

    def echo(user, mid, txt):
        return {"sender": {"id": "negocio"}, "recipient": {"id": user},
                "message": {"mid": mid, "text": txt, "is_echo": True}}

    def dm(user, mid, txt):
        return {"sender": {"id": user}, "recipient": {"id": "negocio"},
                "message": {"mid": mid, "text": txt}}

    with TestClient(bot.app) as client:
        run_ = client.portal.call
        run_(bot.store.add_sent, "del-bot")
        post(echo("luis", "del-bot", "respuesta del bot"))  # eco propio: no pausa
        post(echo("ana", "a-mano", "Hola Ana, soy Skandar"))  # el dueño escribió
        time.sleep(0.3)
        post(dm("ana", "a1", "gracias!"))
        post(dm("luis", "l1", "hola"))
        time.sleep(1)
        assert replies == [["hola"]]  # solo luis recibe respuesta del bot
        history = run_(bot.store.recent, "ana", 10)
        assert ("assistant", "[contestó una persona del equipo] Hola Ana, soy Skandar") in history
        assert ("user", "gracias!") in history


def test_el_prompt_lleva_el_estilo():
    prompt = bot.agent.system_prompt()
    assert "<estilo>" in prompt and "Una sola pregunta" in prompt and "<temas>" in prompt
