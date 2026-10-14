"""Cobros con Stripe: link de pago, firma del webhook y aviso de pago (sin red)."""
import asyncio
import hashlib
import hmac
import json
import os
import time

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from igbot import config, pagos, tienda, tools  # noqa: E402
from igbot.store import Store  # noqa: E402
from test_tienda import CATALOGO, EXISTENCIAS, item  # noqa: E402
from test_tools import FakeGoogle  # noqa: E402

SECRET = "whsec_prueba"


def firma(body: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def evento(tipo: str, session_id: str, cliente: str = "nortex", pagado: str = "paid") -> dict:
    return {"id": "evt_1", "type": tipo, "data": {"object": {
        "id": session_id, "payment_status": pagado, "metadata": {"cliente": cliente},
    }}}


def test_firma_de_stripe():
    body = b'{"id": "evt_1"}'
    assert pagos.signature_ok(body, firma(body), SECRET)
    assert not pagos.signature_ok(body + b" ", firma(body), SECRET)
    assert not pagos.signature_ok(body, firma(body, secret="otro"), SECRET)
    assert not pagos.signature_ok(body, firma(body, ts=int(time.time()) - 3600), SECRET)
    assert not pagos.signature_ok(body, "", SECRET) and not pagos.signature_ok(body, firma(body), "")


class FakeIG:
    def __init__(self):
        self.texts = []

    async def send_text(self, user_id, text):
        self.texts.append((user_id, text))

    async def username(self, user_id):
        return "@" + user_id


def setup(monkeypatch):
    monkeypatch.setattr(config, "STORE_RULES_PATH", "clientes/nortex/reglas.yaml")
    monkeypatch.setattr(config, "MEDIA_SLUG", "nortex")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")
    monkeypatch.setattr(config, "ADMIN_IG_IDS", {"jefe"})
    monkeypatch.setattr(config, "OWNER_EMAIL", "dueno@gmail.com")

    async def catalogo(google):
        return CATALOGO

    async def existencias(google):
        return EXISTENCIAS

    monkeypatch.setattr(tienda, "products", catalogo)
    monkeypatch.setattr(tienda, "stock", existencias)
    created = []

    async def checkout(folio, total, concepto, detalle, user_id, email="", http=None):
        created.append((folio, total, user_id, email))
        return f"cs_{len(created)}", f"https://checkout.stripe.com/c/cs_{len(created)}"

    monkeypatch.setattr(pagos, "checkout", checkout)
    return created


def test_link_de_pago_y_aviso_cuando_paga(monkeypatch):
    created = setup(monkeypatch)

    async def main():
        store = Store(":memory:")
        await store.open()
        google, ig, state = FakeGoogle(), FakeIG(), {}
        t = {x.name: x.handler for x in tools.store_tools(google, store, "ana", ig, state)}
        args = {"items": [item("NX-POL-NVY", "M", 30, "bordado"), item("NX-POL-NVY", "L", 30, "bordado")],
                "logos": 1, "email": ""}
        r = await t["cobrar_pedido"](args)
        status_before = (await t["estado_pago"]({}))["content"][0]["text"]

        # OXXO: termina el checkout sin pagar; luego vence la sesión; luego paga la ficha.
        await pagos.handle(evento("checkout.session.completed", "cs_1", pagado="unpaid"), store, ig, google)
        await pagos.handle(evento("checkout.session.expired", "cs_1"), store, ig, google)
        waiting = (await store.payments("ana"))[0]["status"]
        await pagos.handle(evento("checkout.session.completed", "cs_1", cliente="principal"), store, ig, google)
        assert not ig.texts  # aviso de otra cuenta: no se toca
        await pagos.handle(evento("checkout.session.async_payment_succeeded", "cs_1"), store, ig, google)
        await pagos.handle(evento("checkout.session.async_payment_succeeded", "cs_1"), store, ig, google)  # reintento
        await pagos.handle(evento("checkout.session.async_payment_failed", "cs_1"), store, ig, google)
        paid = (await store.payments("ana"))[0]
        events = await store.events("pago", 0, 2e9)
        await store.close()
        return r, status_before, waiting, paid, events, ig, google, state

    r, status_before, waiting, paid, events, ig, google, state = asyncio.run(main())
    text = r["content"][0]["text"]
    assert "PED-0001" in text and "https://checkout.stripe.com/c/cs_1" in text and "$14,534.80" in text
    assert created == [("PED-0001", 14534.8, "ana", "")] and state["acted"]
    assert "sin pagar" in status_before and waiting == "esperando"
    assert paid["status"] == "pagado" and paid["paid_at"] > 0
    assert len(events) == 1 and events[0][1] == {"folio": "PED-0001", "total": 14534.8}
    assert [u for u, _ in ig.texts] == ["ana", "jefe"] and "$14,534.80" in ig.texts[0][1]
    assert google.sent[0][1] == "dueno@gmail.com" and "PED-0001" in google.sent[0][3]


def test_limites_del_link(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(config, "MAX_PAYMENT_LINKS_PER_USER_PER_DAY", 1)

    async def main():
        store = Store(":memory:")
        await store.open()
        t = {x.name: x.handler for x in tools.store_tools(FakeGoogle(), store, "ana", FakeIG(), {})}
        args = {"items": [item("NX-POL-NVY", "M", 30)], "logos": 1}
        bad_email = await t["cobrar_pedido"]({**args, "email": "nunca@lo.dijo"})
        first = await t["cobrar_pedido"]({**args, "email": ""})
        second = await t["cobrar_pedido"]({**args, "email": ""})
        await store.close()
        return bad_email, first, second

    bad_email, first, second = asyncio.run(main())
    assert bad_email.get("is_error") and not first.get("is_error") and second.get("is_error")


def test_sin_stripe_no_hay_herramienta(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    names = {x.name for x in tools.store_tools(FakeGoogle(), object(), "ana")}
    assert "cobrar_pedido" not in names and "enviar_cotizacion_pedido" in names


def test_webhook_de_stripe(monkeypatch):
    from fastapi.testclient import TestClient

    import app as bot

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "FORWARD_WEBHOOKS", [])
    handled = []

    async def fake_handle(event, *a):
        handled.append(event["type"])

    monkeypatch.setattr(pagos, "handle", fake_handle)
    client = TestClient(bot.app)
    body = json.dumps(evento("checkout.session.completed", "cs_9")).encode()
    assert client.post("/stripe/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=x"}).status_code == 400
    assert client.post("/stripe/webhook", content=body, headers={"Stripe-Signature": firma(body)}).status_code == 200
    assert handled == ["checkout.session.completed"]
    assert "Gracias" in client.get("/pago/listo").text


def test_sesion_de_checkout(monkeypatch):
    import httpx
    from urllib.parse import parse_qs

    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.ejemplo.com")
    monkeypatch.setattr(config, "MEDIA_SLUG", "nortex")
    seen = {}

    def answer(request):
        seen["auth"] = request.headers["Authorization"]
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            return await pagos.checkout("PED-0007", 1234.56, "Pedido PED-0007", "30 polos", "ana", "a@b.com", http)

    assert asyncio.run(main()) == ("cs_1", "https://checkout.stripe.com/c/cs_1")
    f = seen["form"]
    assert seen["auth"] == "Bearer sk_test_x"
    assert f["line_items[0][price_data][unit_amount]"] == "123456" and f["line_items[0][price_data][currency]"] == "mxn"
    assert f["metadata[cliente]"] == "nortex" and f["payment_intent_data[metadata][folio]"] == "PED-0007"
    assert f["customer_email"] == "a@b.com" and f["success_url"] == "https://bot.ejemplo.com/pago/listo"
