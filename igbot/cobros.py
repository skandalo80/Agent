"""Conecta tu Mercado Pago y tu Stripe: cobrar por el chat y ver qué chat pagó.

Si la venta se hace en la página del cliente, el procesador de pago no sabe de qué chat vino. Lo que sí
se sabe es lo que se cobra con un link de pago que arma el asistente:

1. El cliente conecta su cuenta en Conexión (un botón; OAuth de Mercado Pago o Stripe Connect).
2. El asistente tiene la herramienta `link_de_pago`: con el precio de un producto de Tu empresa crea un
   cobro en la cuenta del cliente que lleva el código del chat (external_reference en Mercado Pago,
   client_reference_id en Stripe).
3. Cuando lo pagan, el procesador nos avisa (/cobros/mercadopago/aviso, /cobros/stripe/aviso); el pago
   se guarda en `ventas` y sale en el chat de quien pagó, con el anuncio que lo trajo si llegó por uno.

kv "cobro_mercadopago": {"token", "refresh", "vence", "usuario", "fecha"};
kv "cobro_stripe": {"cuenta": "acct_…", "fecha"}. Sin sus llaves en .env, cada uno no sale en el panel.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import config, marco
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.cobros")

router = APIRouter(prefix="/panel/cobros")
publico = APIRouter(prefix="/cobros")
cuentas = lambda: []  # noqa: E731 — lo pone app.py: todas las cuentas, para los avisos de pago

STATE_COOKIE = "cobros_state"
STATE_TTL = 900
MAX_CANTIDAD = 20
# Monedas que Stripe cobra sin centavos.
SIN_CENTAVOS = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf",
                "xof", "xpf"}
_transporte: httpx.AsyncBaseTransport | None = None  # las pruebas ponen uno falso
_Cliente = httpx.AsyncClient  # el de verdad, aunque las pruebas de otras partes cambien httpx.AsyncClient

NOMBRES = {"mercadopago": "Mercado Pago", "stripe": "Stripe"}
AVISOS = {
    "conectado": "Listo: conectamos tu cuenta de cobro. Tu asistente ya puede mandar links de pago.",
    "cancelado": "No se conectó tu cuenta de cobro. Puedes intentarlo de nuevo cuando quieras.",
    "fallo": "No pudimos conectar tu cuenta de cobro esta vez. Intenta de nuevo en un momento.",
    "desconectado": "Desconectamos tu cuenta de cobro: tu asistente ya no manda links de pago.",
}


def enabled(prov: str) -> bool:
    if not config.PUBLIC_URL:
        return False
    if prov == "mercadopago":
        return bool(config.MP_CLIENT_ID and config.MP_CLIENT_SECRET)
    if prov == "stripe":
        return bool(config.STRIPE_CLIENT_ID and config.STRIPE_SECRET_KEY and config.STRIPE_WEBHOOK_SECRET)
    return False


def redirect_uri(prov: str) -> str:
    return f"{config.PUBLIC_URL}/panel/cobros/{prov}/callback"


async def conexion(c: Cuenta, prov: str) -> dict:
    return json.loads(await c.store.get(f"cobro_{prov}") or "{}")


async def guardar(c: Cuenta, prov: str, datos: dict) -> None:
    await c.store.set(f"cobro_{prov}", json.dumps(datos) if datos else "")


def cliente() -> httpx.AsyncClient:
    return _Cliente(timeout=20, transport=_transporte)


def _firmar(texto: str) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), f"cobros:{texto}".encode(), hashlib.sha256).hexdigest()


def nuevo_state(c: Cuenta, prov: str) -> str:
    base = f"{prov}.{c.slug or '-'}.{int(time.time())}.{secrets.token_urlsafe(12)}"
    return f"{base}.{_firmar(base)[:32]}"


def state_valido(state: str, cookie: str | None, c: Cuenta, prov: str) -> bool:
    partes = state.split(".")
    if len(partes) != 5 or not cookie or not secrets.compare_digest(cookie, state):
        return False
    p, slug, ts, nonce, firma = partes
    return (p == prov and slug == (c.slug or "-") and ts.isdigit() and 0 <= time.time() - int(ts) < STATE_TTL
            and secrets.compare_digest(firma, _firmar(f"{p}.{slug}.{ts}.{nonce}")[:32]))


def _volver(aviso: str) -> RedirectResponse:
    r = RedirectResponse(f"/panel/conexion?aviso={aviso}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/panel/cobros")
    return r


# --- conectar ---

@router.post("/{prov}/conectar")
async def conectar(c: marco.Formulario, prov: str):
    if not enabled(prov):
        return RedirectResponse("/panel/conexion", status_code=303)
    state = nuevo_state(c, prov)
    if prov == "mercadopago":
        url = "https://auth.mercadopago.com/authorization?" + urlencode({
            "client_id": config.MP_CLIENT_ID, "response_type": "code", "platform_id": "mp", "state": state,
            "redirect_uri": redirect_uri(prov)})
    else:
        url = "https://connect.stripe.com/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": config.STRIPE_CLIENT_ID, "scope": "read_write", "state": state,
            "redirect_uri": redirect_uri(prov)})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, path="/panel/cobros", secure=True, httponly=True,
                 samesite="lax")
    return r


async def _token_mercadopago(datos: dict) -> dict:
    async with cliente() as http:
        r = await http.post("https://api.mercadopago.com/oauth/token", json={
            "client_id": config.MP_CLIENT_ID, "client_secret": config.MP_CLIENT_SECRET, **datos})
    if r.is_error:
        log.error("Mercado Pago respondió %s al pedir el token: %s", r.status_code, r.text[:300])
    r.raise_for_status()
    t = r.json()
    return {"token": t["access_token"], "refresh": t.get("refresh_token", ""), "usuario": str(t.get("user_id", "")),
            "vence": time.time() + float(t.get("expires_in") or 15552000)}


@router.get("/{prov}/callback")
async def callback(request: Request, c: marco.Sesion, prov: str):
    q = request.query_params
    if not q.get("code"):
        return _volver("cancelado")
    if not (enabled(prov) and state_valido(q.get("state", ""), request.cookies.get(STATE_COOKIE), c, prov)):
        log.warning("Vuelta de %s con state inválido", prov)
        return _volver("fallo")
    try:
        if prov == "mercadopago":
            con = await _token_mercadopago({"grant_type": "authorization_code", "code": q["code"],
                                            "redirect_uri": redirect_uri(prov)})
        else:
            async with cliente() as http:
                r = await http.post("https://connect.stripe.com/oauth/token",
                                    data={"grant_type": "authorization_code", "code": q["code"]},
                                    headers={"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"})
            if r.is_error:
                log.error("Stripe respondió %s al conectar: %s", r.status_code, r.text[:300])
            r.raise_for_status()
            con = {"cuenta": r.json()["stripe_user_id"]}
    except Exception:
        log.exception("No se pudo conectar %s", prov)
        return _volver("fallo")
    await guardar(c, prov, {**con, "fecha": int(time.time())})
    log.info("@%s conectó su %s", await marco.usuario(c), NOMBRES[prov])
    return _volver("conectado")


@router.post("/{prov}/desconectar")
async def desconectar(c: marco.Formulario, prov: str):
    if prov in NOMBRES:
        await guardar(c, prov, {})
    return RedirectResponse("/panel/conexion?aviso=desconectado", status_code=303)


async def filas(c: Cuenta) -> list[tuple[str, str, str, str]]:
    """(estado, título, detalle, botón) de cada cobro para la tarjeta Conexión."""
    resultado = []
    for prov, nombre in NOMBRES.items():
        if not enabled(prov):
            continue
        con = await conexion(c, prov)
        if con:
            boton = (f'<form method="post" action="/panel/cobros/{prov}/desconectar">'
                     '<button class="boton secundario chico">Desconectar</button></form>')
            resultado.append(("ok", nombre, "Conectado: tu asistente manda links de pago y ves quién pagó", boton))
        else:
            boton = (f'<form method="post" action="/panel/cobros/{prov}/conectar">'
                     f'<button class="boton secundario chico">Conectar tu {nombre}</button></form>')
            resultado.append(("no", nombre, "Sin conectar: tu asistente podría cobrar por el chat", boton))
    return resultado


# --- links de pago ---

def _precio(texto: str) -> float | None:
    limpio = re.sub(r"[^\d.]", "", str(texto).replace(",", ""))
    try:
        v = float(limpio)
    except ValueError:
        return None
    return v if v > 0 else None


def _buscar(productos: list[dict], nombre: str) -> dict | None:
    def norma(t: str) -> str:
        return re.sub(r"\s+", " ", str(t).lower()).strip()
    buscado = norma(nombre)
    exacto = [p for p in productos if norma(p.get("nombre", "")) == buscado]
    if exacto:
        return exacto[0]
    parecidos = [p for p in productos if buscado and buscado in norma(p.get("nombre", ""))]
    return parecidos[0] if len(parecidos) == 1 else None


async def _vigente_mercadopago(c: Cuenta) -> dict:
    """La conexión de Mercado Pago, con el token renovado si vence en menos de una semana."""
    con = await conexion(c, "mercadopago")
    if con and con.get("refresh") and con.get("vence", 0) < time.time() + 7 * config.DIA:
        try:
            con = {**con, **await _token_mercadopago({"grant_type": "refresh_token", "refresh_token": con["refresh"]})}
            await guardar(c, "mercadopago", con)
        except Exception:
            log.exception("No se pudo renovar el token de Mercado Pago")
    return con


async def crear_link(c: Cuenta, user_id: str, producto: str, cantidad: int) -> str:
    """El link de pago de un producto de Tu empresa para este chat, o por qué no se pudo."""
    from . import empresa
    p = _buscar(await empresa.productos(c), producto)
    if not p:
        return "No encontré ese producto en el catálogo. Pregunta cuál quiere o pásalo con una persona."
    precio = _precio(p.get("precio", ""))
    if not precio or p.get("desde"):
        return "Ese producto no tiene un precio fijo en el catálogo: no mandes link de pago, pásalo con una persona."
    cantidad = max(1, min(int(cantidad or 1), MAX_CANTIDAD))
    moneda = (p.get("moneda") or "MXN").upper()
    codigo = await c.store.codigo_chat(user_id)
    mp, st = await _vigente_mercadopago(c), await conexion(c, "stripe")
    usar_mp = mp and enabled("mercadopago") and (moneda == "MXN" or not st)
    try:
        async with cliente() as http:
            if usar_mp:
                r = await http.post("https://api.mercadopago.com/checkout/preferences",
                                    headers={"Authorization": f"Bearer {mp['token']}"}, json={
                    "items": [{"title": p["nombre"][:250], "quantity": cantidad, "unit_price": precio,
                               "currency_id": moneda}],
                    "external_reference": codigo,
                    "notification_url": f"{config.PUBLIC_URL}/cobros/mercadopago/aviso?c={c.slug or '-'}"})
                r.raise_for_status()
                url = r.json()["init_point"]
            elif st and enabled("stripe"):
                cent = 1 if moneda.lower() in SIN_CENTAVOS else 100
                r = await http.post("https://api.stripe.com/v1/checkout/sessions", headers={
                    "Authorization": f"Bearer {config.STRIPE_SECRET_KEY}", "Stripe-Account": st["cuenta"]}, data={
                    "mode": "payment", "client_reference_id": codigo,
                    "success_url": f"{config.PUBLIC_URL}/cobros/gracias",
                    "line_items[0][quantity]": cantidad,
                    "line_items[0][price_data][currency]": moneda.lower(),
                    "line_items[0][price_data][unit_amount]": round(precio * cent),
                    "line_items[0][price_data][product_data][name]": p["nombre"][:250]})
                r.raise_for_status()
                url = r.json()["url"]
            else:
                return "No hay una cuenta de cobro conectada."
    except httpx.HTTPError:
        log.exception("No se pudo crear el link de pago de %s", p.get("nombre"))
        return "No se pudo crear el link de pago ahora. Dile que en un momento se lo mandas o pásalo con una persona."
    total = precio * cantidad
    return f"Link de pago de {cantidad} × {p['nombre']} (total ${total:,.2f} {moneda}): {url}"


async def conectado(c: Cuenta) -> bool:
    for p in NOMBRES:
        if enabled(p) and await conexion(c, p):
            return True
    return False


def herramientas(c: Cuenta, user_id: str) -> list:
    """La herramienta `link_de_pago` para el asistente de una cuenta con Mercado Pago o Stripe conectado."""
    from claude_agent_sdk import tool

    from .tools import ok

    @tool("link_de_pago", "Crea un link para pagar en línea un producto del catálogo, con su precio del catálogo."
          " Úsala solo cuando la persona ya decidió comprar y pide pagar o cómo pagar. Manda el link tal cual,"
          " con el total; nunca inventes precios ni links.", {"producto": str, "cantidad": int})
    async def link_de_pago(args):
        return ok(await crear_link(c, user_id, str(args.get("producto", ""))[:120], args.get("cantidad") or 1))

    return [link_de_pago]


# --- avisos de pago ---

async def _guardar_pago(c: Cuenta, origen: str, externo: str, codigo: str, monto: float, moneda: str,
                        detalle: dict) -> bool:
    user_id = await c.store.usuario_de_codigo(codigo) if codigo else ""
    if not user_id:
        return False  # no es un cobro del asistente
    anuncio = (await c.store.anuncios_de([user_id])).get(user_id, {}).get("ad_id", "")
    if nueva := await c.store.add_venta(origen, externo, user_id, anuncio, monto, moneda.upper(), detalle, time.time()):
        from .conversiones_meta import avisar
        avisar(c, user_id, "venta", monto, moneda, f"{origen}:{externo}")
    return nueva


MAX_AVISOS_MP_MINUTO = 60  # avisos de Mercado Pago que se atienden por minuto (cada uno consulta su API)
_avisos_mp: list[float] = []


def firma_mp_valida(pago: str, cabecera: str, request_id: str, ahora: float | None = None,
                    tolerancia: int = 300) -> bool:
    """x-signature de Mercado Pago: "ts=...,v1=..."; v1 = HMAC-SHA256 del manifiesto
    "id:<data.id>;request-id:<x-request-id>;ts:<ts>;" con la clave secreta del webhook."""
    pares = dict(x.strip().split("=", 1) for x in cabecera.split(",") if "=" in x)
    ts, v1 = pares.get("ts", ""), pares.get("v1", "")
    if not ts.isdigit() or not v1 or abs((time.time() if ahora is None else ahora) - int(ts) / (1000 if len(ts) > 11 else 1)) > tolerancia:
        return False
    manifiesto = f"id:{pago.lower()};request-id:{request_id};ts:{ts};"
    esperada = hmac.new(config.MP_WEBHOOK_SECRET.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, v1)


def _cabe_aviso_mp(ahora: float | None = None) -> bool:
    ahora = time.time() if ahora is None else ahora
    _avisos_mp[:] = [t for t in _avisos_mp if ahora - t < 60]
    if len(_avisos_mp) >= MAX_AVISOS_MP_MINUTO:
        return False
    _avisos_mp.append(ahora)
    return True


@publico.post("/mercadopago/aviso")
async def aviso_mercadopago(request: Request):
    """Mercado Pago avisa que cambió un pago. No se cree lo que dice el aviso: se pregunta el pago a Mercado
    Pago con el token de esa cuenta, así un aviso falso no guarda nada. Con MP_WEBHOOK_SECRET se exige además
    la firma x-signature; y se atienden como mucho MAX_AVISOS_MP_MINUTO por minuto."""
    q = request.query_params
    try:
        cuerpo = json.loads(await request.body() or b"{}")
    except ValueError:
        cuerpo = {}
    tipo = q.get("type") or q.get("topic") or cuerpo.get("type") or cuerpo.get("topic") or ""
    pago = str(q.get("data.id") or q.get("id") or (cuerpo.get("data") or {}).get("id") or "")
    if tipo != "payment" or not pago.isdigit():
        return Response(status_code=200)
    if config.MP_WEBHOOK_SECRET and not firma_mp_valida(pago, request.headers.get("x-signature", ""),
                                                        request.headers.get("x-request-id", "")):
        log.warning("Aviso de Mercado Pago con firma inválida")
        raise HTTPException(status_code=401)
    if not _cabe_aviso_mp():
        raise HTTPException(status_code=429)  # que lo vuelva a mandar más tarde
    c = next((x for x in cuentas() if (x.slug or "-") == q.get("c")), None)
    if not c:
        return Response(status_code=200)
    with usar(c):
        con = await conexion(c, "mercadopago")
        if not con:
            return Response(status_code=200)
        async with cliente() as http:
            r = await http.get(f"https://api.mercadopago.com/v1/payments/{pago}",
                               headers={"Authorization": f"Bearer {con['token']}"})
        if r.is_error:
            log.warning("Mercado Pago respondió %s al leer el pago %s", r.status_code, pago)
            if r.status_code >= 500:
                raise HTTPException(status_code=502)  # que lo vuelva a mandar
            return Response(status_code=200)  # no es un pago de esta cuenta
        p = r.json()
        if p.get("status") == "approved":
            productos = [f"{i.get('quantity', 1)} × {i.get('title', '')}"
                         for i in (p.get("additional_info") or {}).get("items") or []][:10]
            if await _guardar_pago(c, "mercadopago", pago, str(p.get("external_reference") or ""),
                                   float(p.get("transaction_amount") or 0), str(p.get("currency_id", "")),
                                   {"productos": productos}):
                log.info("Cuenta %s: pago de Mercado Pago de un chat", c.slug or "principal")
        elif p.get("status") in ("refunded", "cancelled", "charged_back"):
            await c.store.borrar_venta("mercadopago", pago)
    return Response(status_code=200)


def firma_stripe_valida(cuerpo: bytes, cabecera: str, tolerancia: int = 300) -> bool:
    pares = [x.strip().split("=", 1) for x in cabecera.split(",") if "=" in x]
    t = next((v for k, v in pares if k == "t"), "")
    firmas = [v for k, v in pares if k == "v1"]
    if not t.isdigit() or abs(time.time() - int(t)) > tolerancia:
        return False
    esperada = hmac.new(config.STRIPE_WEBHOOK_SECRET.encode(), f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(esperada, f) for f in firmas)


@publico.post("/stripe/aviso")
async def aviso_stripe(request: Request):
    """Los avisos de Stripe de las cuentas conectadas (endpoint de Connect, firmado)."""
    cuerpo = await request.body()
    if not enabled("stripe") or not firma_stripe_valida(cuerpo, request.headers.get("stripe-signature", "")):
        raise HTTPException(status_code=400)
    evento = json.loads(cuerpo)
    s = (evento.get("data") or {}).get("object") or {}
    cuenta_st = evento.get("account", "")
    for c in cuentas():
        with usar(c):
            if not cuenta_st or (await conexion(c, "stripe")).get("cuenta") != cuenta_st:
                continue
            if evento.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded") \
                    and s.get("payment_status") == "paid":
                moneda = str(s.get("currency", ""))
                cent = 1 if moneda.lower() in SIN_CENTAVOS else 100
                await _guardar_pago(c, "stripe", str(s.get("id", "")), str(s.get("client_reference_id") or ""),
                                    (s.get("amount_total") or 0) / cent, moneda, {})
    return Response(status_code=200)


@publico.get("/gracias")
async def gracias():
    return marco.pagina(
        '<main><div class="contenedor"><div class="tarjeta"><h2>Gracias por tu pago</h2>'
        '<p class="ayuda">Tu pago quedó registrado. Puedes volver a tu chat de Instagram.</p></div></div></main>')
