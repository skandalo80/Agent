"""Conecta tu cuenta de anuncios: lo que Meta sabe de cada anuncio, junto a lo que pasó en los chats.

El cliente que anuncia manda a la gente directo a su página y ahí compra; eso ya lo mide el Pixel de Meta.
No lo reconstruimos: lo leemos. Con el mismo login de Facebook (permiso ads_read, solo lectura) elige su
cuenta publicitaria y en Resultados > Anuncios ve, por anuncio y en el periodo elegido:
- de Meta: gasto, clics y lo que registró su Pixel o sus formularios: compras (con su valor), prospectos,
  contactos y citas; un negocio de servicios no vende en su página, pero sí junta prospectos o citas;
- de Skandar: los chats que llegaron de ese anuncio (anuncios_chat) y hasta dónde llegaron (cotización, cita,
  venta; Store.por_anuncio).

kv "ads_meta": {"token", "vence", "cuenta": "act_…", "nombre", "moneda", "fecha"}. El token de usuario dura 60
días: al vencer se pide volver a conectar (sale en Conexión). Sin FB_APP_ID en .env no sale nada de esto.
"""
import json
import logging
import secrets
import time
from datetime import date
from html import escape
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, conectar, panel
from .cuenta import Cuenta
from .pagina_fb import FB_GRAPH

log = logging.getLogger("igbot.anuncios_meta")

router = APIRouter(prefix="/panel/anuncios")

CLAVE = "ads_meta"
# instagram_manage_events y page_events: para avisarle a Meta lo que pasa en los chats (conversiones_meta.py).
SCOPES = "ads_read,business_management,instagram_manage_events,page_events"
STATE_COOKIE = "anuncios_state"
ELEGIR_COOKIE = "anuncios_elegir"
TTL = 900
AVISO_DIAS = 7  # cuántos días antes de vencer se avisa en Conexión
CACHE_SEGUNDOS = 600
_por_elegir: dict[str, tuple[float, dict, list[dict]]] = {}  # código -> (cuándo, token, cuentas)
_cache: dict[tuple, tuple[float, list[dict] | None]] = {}

# Las compras que cuenta Meta, de la más completa a la más angosta: se usa la primera que venga, para no
# contar la misma compra dos veces (omni_purchase ya incluye a las del Pixel).
COMPRAS = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
CHATS_META = "onsite_conversion.messaging_conversation_started_7d"
# Lo mismo para los negocios de servicios: prospectos (Pixel Lead o formularios de Meta), contactos y citas.
PROSPECTOS = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
CONTACTOS = ("contact_total", "contact_website", "offsite_conversion.fb_pixel_contact")
CITAS = ("schedule_total", "schedule_website", "offsite_conversion.fb_pixel_schedule")


def enabled() -> bool:
    return bool(config.FB_APP_ID and config.FB_APP_SECRET and config.PUBLIC_URL)


def redirect_uri() -> str:
    # Va en "URI de redireccionamiento de OAuth válidos" del login de Facebook, junto a la de facebook.py.
    return f"{config.PUBLIC_URL}/panel/anuncios/callback"


async def conexion(c: Cuenta) -> dict:
    """La cuenta de anuncios conectada ({} si no hay)."""
    return json.loads(await c.store.get(CLAVE) or "{}")


async def guardar(c: Cuenta, datos: dict) -> None:
    await c.store.set(CLAVE, json.dumps(datos, ensure_ascii=False) if datos else "")


def vencida(datos: dict) -> bool:
    return bool(datos.get("token")) and float(datos.get("vence") or 0) < time.time()


def por_vencer(datos: dict) -> bool:
    return bool(datos.get("token")) and float(datos.get("vence") or 0) < time.time() + AVISO_DIAS * 86400


# --- conectar (login de Facebook, solo lectura de anuncios) ---

def _dialogo() -> RedirectResponse:
    state = conectar.nuevo_state()
    url = f"https://www.facebook.com/{config.API_VERSION}/dialog/oauth?" + urlencode({
        "client_id": config.FB_APP_ID, "redirect_uri": redirect_uri(), "state": state, "response_type": "code",
        **({"config_id": config.FB_ADS_CONFIG_ID} if config.FB_ADS_CONFIG_ID else {"scope": SCOPES})})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, state, max_age=TTL, path="/panel/anuncios", secure=True, httponly=True,
                 samesite="lax")
    return r


@router.post("/conectar")
async def conectar_cuenta(request: Request):
    panel.de_aqui(request)
    if not panel.cuenta_de_sesion(request):
        return RedirectResponse("/panel", status_code=303)
    return _dialogo() if enabled() else RedirectResponse("/panel/anuncios", status_code=303)


def _check(r: httpx.Response) -> None:
    if r.is_error:
        log.error("Meta respondió %s en %s: %s", r.status_code, r.url.path, r.text[:500])
    r.raise_for_status()


async def cuentas_del_codigo(code: str) -> tuple[dict, list[dict]]:
    """Código de un solo uso -> token de 60 días -> sus cuentas publicitarias activas.
    Devuelve ({"token", "vence"}, [{id, name, currency}])."""
    base = f"{FB_GRAPH}/{config.API_VERSION}"
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{base}/oauth/access_token", params={
            "client_id": config.FB_APP_ID, "client_secret": config.FB_APP_SECRET,
            "redirect_uri": redirect_uri(), "code": code})
        _check(r)
        r = await http.get(f"{base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": config.FB_APP_ID,
            "client_secret": config.FB_APP_SECRET, "fb_exchange_token": r.json()["access_token"]})
        _check(r)
        datos = r.json()
        token = {"token": datos["access_token"], "vence": time.time() + float(datos.get("expires_in") or 60 * 86400)}
        r = await http.get(f"{base}/me/adaccounts", params={
            "access_token": token["token"], "limit": 100, "fields": "id,name,currency,account_status"})
        _check(r)
        # account_status 1 = activa; las cerradas o deshabilitadas no se ofrecen.
        cuentas = [x for x in r.json().get("data", []) if x.get("id") and x.get("account_status", 1) in (1, 3, 7, 8, 9)]
        return token, cuentas


def _volver(aviso: str) -> RedirectResponse:
    r = RedirectResponse(f"/panel/anuncios?aviso={aviso}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/panel/anuncios")
    return r


async def _guardar_cuenta(c: Cuenta, token: dict, cuenta: dict) -> None:
    await guardar(c, {**token, "cuenta": cuenta["id"], "nombre": cuenta.get("name", ""),
                      "moneda": cuenta.get("currency", ""), "fecha": time.time()})
    for k in [k for k in _cache if k[0] == c.slug]:
        del _cache[k]
    log.info("@%s conectó su cuenta de anuncios %s (%s)", await panel.usuario(c), cuenta.get("name"), cuenta["id"])


@router.get("/callback")
async def callback(request: Request):
    p = request.query_params
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if p.get("error") or not p.get("code"):
        return _volver("cancelada")
    if not (enabled() and conectar.state_valido(p.get("state", ""), request.cookies.get(STATE_COOKIE))):
        log.warning("Vuelta de Meta (anuncios) con state inválido")
        return _volver("fallo")
    try:
        token, cuentas = await cuentas_del_codigo(p["code"].removesuffix("#_"))
    except Exception:
        log.exception("No se pudo leer las cuentas de anuncios")
        return _volver("fallo")
    if not cuentas:
        return _volver("sin_cuentas")
    if len(cuentas) == 1:
        await _guardar_cuenta(c, token, cuentas[0])
        return _volver("conectada")
    codigo = secrets.token_urlsafe(18)
    ahora = time.time()
    for k in [k for k, (t, _, _) in _por_elegir.items() if ahora - t > TTL]:
        del _por_elegir[k]
    _por_elegir[codigo] = (ahora, token, cuentas)
    filas = "".join(
        '<form method="post" action="/panel/anuncios/elegir" class="fila">'
        f'<input type="hidden" name="cuenta" value="{escape(x["id"])}">'
        f'<div class="datos"><b>{escape(x.get("name", ""))}</b><span>{escape(x["id"].removeprefix("act_"))}'
        f'{" · " + escape(x["currency"]) if x.get("currency") else ""}</span></div>'
        '<button class="boton secundario chico">Elegir</button></form>' for x in cuentas)
    r = panel.pagina(
        await panel.menu_de(c, "cuenta", sub="conexion")
        + f'<main class="contenido">{panel.encabezado("Tu cuenta de anuncios")}'
        '<div class="tarjeta"><h2>¿Qué cuenta de anuncios conectamos?</h2>'
        '<p class="ayuda">Solo leemos sus números: no creamos, cambiamos ni pagamos anuncios.</p>'
        + filas + "</div></main>")
    r.set_cookie(ELEGIR_COOKIE, codigo, max_age=TTL, path="/panel/anuncios", secure=True, httponly=True,
                 samesite="lax")
    r.delete_cookie(STATE_COOKIE, path="/panel/anuncios")
    return r


@router.post("/elegir")
async def elegir(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    t, token, cuentas = _por_elegir.pop(request.cookies.get(ELEGIR_COOKIE, ""), (0, {}, []))
    cuenta_id = (await panel.form(request)).get("cuenta", "")
    elegida = next((x for x in cuentas if x["id"] == cuenta_id), None)
    if not elegida or time.time() - t > TTL:
        return _volver("fallo")
    await _guardar_cuenta(c, token, elegida)
    r = _volver("conectada")
    r.delete_cookie(ELEGIR_COOKIE, path="/panel/anuncios")
    return r


@router.post("/desconectar")
async def desconectar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    await guardar(c, {})
    return _volver("desconectada")


AVISOS = {
    "conectada": "Listo: conectamos tu cuenta de anuncios. Tus números salen en Resultados > Anuncios.",
    "cancelada": "No se conectó tu cuenta de anuncios. Puedes intentarlo de nuevo cuando quieras.",
    "fallo": "No pudimos conectar tu cuenta de anuncios esta vez. Intenta de nuevo en un momento.",
    "sin_cuentas": "No encontramos cuentas de anuncios activas, o no nos diste permiso de leerlas.",
    "desconectada": "Desconectamos tu cuenta de anuncios.",
}


@router.get("")
async def ver(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    aviso = request.query_params.get("aviso", "")
    datos = await conexion(c)
    boton = ('<form method="post" action="/panel/anuncios/conectar"><div class="acciones">'
             '<button class="boton">{}</button></div>'
             '<p class="ayuda">Te llevamos a Facebook para que elijas tu cuenta de anuncios y des el permiso de'
             " lectura; al aceptar vuelves aquí.</p></form>")
    if not enabled():
        cuerpo = ('<div class="tarjeta"><h2>Tu cuenta de anuncios</h2><p class="ayuda">Muy pronto vas a poder'
                  " conectarla desde aquí.</p></div>")
    elif datos.get("token") and not vencida(datos):
        cuerpo = ('<div class="tarjeta"><h2>Tu cuenta de anuncios está conectada</h2>'
                  f'<p class="ayuda"><b>{escape(datos.get("nombre", ""))}</b> · '
                  f'{escape(datos.get("cuenta", "").removeprefix("act_"))}</p>'
                  '<ul class="lista-shopify"><li>En Resultados &gt; Anuncios ves, por anuncio, lo que gastaste, los'
                  " clics y lo que registró tu Pixel: compras, prospectos o citas.</li><li>Junto a eso, los chats que"
                  " trajo cada anuncio y hasta dónde llegaron: cotización, cita o venta.</li><li>Le avisamos a Meta"
                  " cuando alguien del chat pide cotización, agenda o paga, para que tus anuncios de mensajes"
                  " busquen a gente así.</li><li>No creamos, cambiamos ni pagamos anuncios.</li></ul>"
                  + (f'<p class="ayuda">Meta no aceptó el último aviso del chat: {escape(datos["capi_error"][:200])}.'
                     " Vuelve a conectar y acepta todos los permisos.</p>" + boton.format("Volver a conectar")
                     if datos.get("capi_error") else "")
                  + ('<p class="ayuda">El permiso vence pronto: vuelve a conectar para no perder tus números.</p>'
                     + boton.format("Volver a conectar") if por_vencer(datos) else "")
                  + '<form method="post" action="/panel/anuncios/desconectar" class="acciones">'
                  '<button class="boton secundario chico">Desconectar tu cuenta de anuncios</button></form></div>')
    else:
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu cuenta de anuncios</h2>'
                  '<p class="ayuda">Ve en un solo lugar lo que deja cada anuncio: el gasto, los clics y lo que ya mide'
                  " tu Pixel de Meta (compras, prospectos o citas), junto a los chats que trajo y hasta dónde"
                  " llegaron: cotización, cita o venta. No cambias nada de cómo trabajas.</p>"
                  + ('<p class="ayuda">El permiso anterior venció.</p>' if vencida(datos) else "")
                  + boton.format("Conectar tu cuenta de anuncios") + "</div>")
    aviso_html = f'<div class="aviso">{escape(AVISOS[aviso])}</div>' if aviso in AVISOS else ""
    return panel.pagina(
        await panel.menu_de(c, "cuenta", sub="conexion")
        + f'<main class="contenido">{panel.encabezado("Tu cuenta de anuncios")}{aviso_html}{cuerpo}</main>')


# --- los números de Meta ---

def _accion(lista: list[dict] | None, tipos: tuple[str, ...]) -> float:
    """El valor de la primera de `tipos` que venga en actions/action_values (0 si ninguna)."""
    valores = {a.get("action_type"): a.get("value") for a in lista or []}
    for t in tipos:
        if t in valores:
            try:
                return float(valores[t])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def fila_de(x: dict) -> dict:
    return {"ad_id": str(x.get("ad_id", "")), "nombre": x.get("ad_name", ""),
            "gasto": float(x.get("spend") or 0), "clics": int(float(x.get("clicks") or 0)),
            "impresiones": int(float(x.get("impressions") or 0)),
            "compras": int(_accion(x.get("actions"), COMPRAS)),
            "valor_compras": _accion(x.get("action_values"), COMPRAS),
            "chats_meta": int(_accion(x.get("actions"), (CHATS_META,))),
            "prospectos": int(_accion(x.get("actions"), PROSPECTOS)),
            "contactos": int(_accion(x.get("actions"), CONTACTOS)),
            "citas_meta": int(_accion(x.get("actions"), CITAS))}


async def por_anuncio(c: Cuenta, ini: date, fin: date) -> list[dict] | None:
    """Los números de Meta por anuncio en el periodo; None si no hay cuenta o Meta no contestó."""
    datos = await conexion(c)
    if not datos.get("token") or vencida(datos):
        return None
    clave = (c.slug, datos.get("cuenta"), ini, fin)
    if (guardado := _cache.get(clave)) and time.time() - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]
    filas: list[dict] | None = []
    url = f"{FB_GRAPH}/{config.API_VERSION}/{datos['cuenta']}/insights"
    params = {"access_token": datos["token"], "level": "ad", "limit": 200,
              "fields": "ad_id,ad_name,spend,clicks,impressions,actions,action_values",
              "time_range": json.dumps({"since": ini.isoformat(), "until": fin.isoformat()})}
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            for _ in range(10):  # hasta 2,000 anuncios
                r = await http.get(url, params=params)
                _check(r)
                cuerpo = r.json()
                filas += [fila_de(x) for x in cuerpo.get("data", [])]
                if not (url := (cuerpo.get("paging") or {}).get("next")):
                    break
                params = {}
    except Exception:
        log.exception("No se pudieron leer los números de anuncios de %s", datos.get("cuenta"))
        filas = None
    _cache[clave] = (time.time(), filas)
    return filas
