"""Cobros con Stripe: link de pago por DM y aviso cuando se paga.

El agente arma el pedido y aquí se calcula el total (nunca lo escribe Claude).
Se crea una sesión de Stripe Checkout con ese total en MXN; los métodos de pago
(tarjeta, OXXO, transferencia) son los que estén activos en el panel de Stripe.
Stripe avisa a POST /stripe/webhook y el bot le confirma el pago a la persona y
al dueño. OXXO se paga después: al terminar el checkout queda "esperando" y el
pago llega en otro aviso, a veces días más tarde.
"""
import hashlib
import hmac
import logging
import time

import httpx

from . import config
from .google import Google
from .instagram import Instagram
from .store import Store
from .tienda import money

log = logging.getLogger("igbot.pagos")

API = "https://api.stripe.com/v1"


def available() -> bool:
    # PUBLIC_URL hace falta para las páginas a las que Stripe regresa después de pagar.
    return bool(config.STRIPE_SECRET_KEY and config.PUBLIC_URL)


def folio(payment_id: int) -> str:
    return f"PED-{payment_id:04d}"


async def checkout(folio_: str, total: float, concepto: str, detalle: str, user_id: str, email: str = "",
                   http: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Crea la sesión de Checkout. Devuelve (id, url)."""
    data = {
        "mode": "payment",
        "locale": "es",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "mxn",
        "line_items[0][price_data][unit_amount]": str(round(total * 100)),
        "line_items[0][price_data][product_data][name]": concepto[:250],
        "line_items[0][price_data][product_data][description]": detalle[:500],
        "expires_at": str(int(time.time() + config.PAYMENT_LINK_HOURS * 3600)),
        "success_url": f"{config.PUBLIC_URL}/pago/listo",
        "cancel_url": f"{config.PUBLIC_URL}/pago/cancelado",
        "client_reference_id": folio_,
    }
    meta = {"folio": folio_, "user_id": user_id, "cliente": config.MEDIA_SLUG}
    for key, value in meta.items():
        data[f"metadata[{key}]"] = value
        data[f"payment_intent_data[metadata][{key}]"] = value
    if email:
        data["customer_email"] = email
    own = http is None
    http = http or httpx.AsyncClient(timeout=20)
    try:
        r = await http.post(f"{API}/checkout/sessions", data=data,
                            headers={"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"})
        r.raise_for_status()
        body = r.json()
        return body["id"], body["url"]
    finally:
        if own:
            await http.aclose()


def signature_ok(body: bytes, header: str | None, secret: str, tolerance: float = 300,
                 now: float | None = None) -> bool:
    """Firma de Stripe ("t=...,v1=..."): HMAC-SHA256 de "t.cuerpo" con el secreto del webhook."""
    if not header or not secret:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.partition("=")
        parts.setdefault(k.strip(), []).append(v.strip())
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


# Evento de Stripe -> estado del pago (solo si la sesión ya se pagó o no).
PAID, WAITING, FAILED, EXPIRED = "pagado", "esperando", "fallido", "vencido"


def new_status(event: dict) -> str | None:
    kind = event.get("type", "")
    session = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        return PAID if session.get("payment_status") in ("paid", "no_payment_required") else WAITING
    return {
        "checkout.session.async_payment_succeeded": PAID,
        "checkout.session.async_payment_failed": FAILED,
        "checkout.session.expired": EXPIRED,
    }.get(kind)


async def handle(event: dict, store: Store, instagram: Instagram | None, google: Google | None) -> None:
    session = (event.get("data") or {}).get("object") or {}
    if (session.get("metadata") or {}).get("cliente") != config.MEDIA_SLUG:
        return  # el pago es de otra cuenta (comparten la misma cuenta de Stripe)
    status = new_status(event)
    payment = await store.payment_by_session(session.get("id", ""))
    if not status or not payment:
        return
    # Stripe reintenta y puede mandar los avisos en desorden: un pago no se "despaga".
    if payment["status"] == status or payment["status"] == PAID:
        return
    if status == EXPIRED and payment["status"] == WAITING:
        return  # la sesión vence pero la ficha de OXXO sigue viva
    await store.set_payment_status(payment["id"], status)
    log.info("Pago %s: %s", payment["folio"], status)
    if status == PAID:
        await notify_paid(payment, store, instagram, google)


async def notify_paid(payment: dict, store: Store, instagram: Instagram | None, google: Google | None) -> None:
    user_id, total, fol = payment["user_id"], payment["total"], payment["folio"]
    await store.add_event(user_id, "pago", {"folio": fol, "total": total})
    text = (f"¡Recibimos tu pago de {money(total)} MXN del pedido {fol}! Muchas gracias."
            " En breve te confirmamos los detalles de producción y entrega.")
    if instagram:
        try:
            await instagram.send_text(user_id, text)
            await store.add_bot_message(user_id, text)
        except Exception:
            # Fuera de la ventana de 24 h Instagram no deja escribirle; el dueño sí se entera.
            log.exception("No se pudo avisar del pago %s por DM a %s", fol, user_id)
        name = await instagram.username(user_id)
    else:
        name = user_id
    aviso = f"Pago recibido: {fol} de {name}, {money(total)} MXN."
    for admin in config.ADMIN_IG_IDS:
        try:
            await instagram.send_text(admin, aviso)
            await store.add_bot_message(f"admin:{admin}", aviso)
        except Exception:
            log.exception("No se pudo avisar del pago al administrador %s", admin)
    if google and config.OWNER_EMAIL:
        body = f"{aviso}\n\nPedido:\n{payment['detalle']}\n\nCorreo del cliente: {payment['email'] or '(no dio)'}"
        try:
            await google.send_email(config.MAIL_FROM, config.MAIL_FROM_NAME, config.OWNER_EMAIL, "",
                                    f"Pago recibido {fol} · {money(total)} MXN", body)
        except Exception:
            log.exception("No se pudo mandar el correo del pago %s", fol)
