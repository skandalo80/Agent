"""Avisar a Meta lo que pasó en el chat (Conversions API for Business Messaging).

Con la cuenta de anuncios conectada, cuando una persona que escribió por Instagram o Messenger pide
cotización, agenda una cita o paga, se lo mandamos a Meta como evento del chat. Meta lo liga al anuncio que
la trajo y el cliente puede optimizar sus anuncios de mensajes hacia gente que cotiza, no solo que escribe.

- pidió cotización, visita o dejó sus datos (tools.anotar_interes) -> LeadSubmitted, una vez por persona;
- agendó cita (tools.agendar_reunion) -> QualifiedLead, una vez por persona;
- pagó con link (cobros) o el dueño marcó la venta (Conversaciones) -> Purchase con su monto.
Los pedidos de Shopify no: pasan en su página y ya los manda su Pixel o la app de Meta de Shopify.

Se usa el token de la cuenta de anuncios (login de Facebook) con instagram_manage_events y page_events; el
dataset de su Instagram y el de su página se crean (o se leen, si ya existen) la primera vez y se guardan
en kv "ads_meta". Si Meta rechaza el evento por permisos, se anota en "capi_error" y sale en
Tu cuenta de anuncios. Nada de esto frena la respuesta del asistente: va en segundo plano.
"""
import asyncio
import logging
import time

import httpx

from . import anuncios_meta, config
from .cuenta import Cuenta
from .pagina_fb import FB_GRAPH

log = logging.getLogger("igbot.conversiones_meta")

EVENTOS = {"interes": "LeadSubmitted", "cita": "QualifiedLead", "venta": "Purchase"}
UNA_VEZ = ("interes", "cita")  # por persona: la segunda cotización de alguien no es otro prospecto
_tareas: set[asyncio.Task] = set()


def _canal(c: Cuenta, user_id: str, pagina: str) -> tuple[str, str, dict] | None:
    """(canal, de quién es el dataset, user_data) para este chat; None si no se puede avisar."""
    if user_id.startswith("fb:"):
        if not pagina:
            return None
        return "messenger", pagina, {"page_id": pagina, "page_scoped_user_id": user_id[3:]}
    if not c.ig_account_id or ":" in user_id:
        return None  # sin cuenta de Instagram o un chat que no es de Instagram (admin:, pruebas)
    return "instagram", c.ig_account_id, {"instagram_business_account_id": c.ig_account_id, "ig_sid": user_id}


async def _dataset(http: httpx.AsyncClient, c: Cuenta, datos: dict, canal: str, dueno: str) -> str:
    clave = f"dataset_{canal}"
    if datos.get(clave):
        return datos[clave]
    r = await http.post(f"{FB_GRAPH}/{config.API_VERSION}/{dueno}/dataset", params={"access_token": datos["token"]})
    anuncios_meta._check(r)
    datos[clave] = str(r.json()["id"])
    await anuncios_meta.guardar(c, datos)
    return datos[clave]


async def mandar(c: Cuenta, user_id: str, tipo: str, valor: float = 0, moneda: str = "", event_id: str = "") -> bool:
    """Manda el evento a Meta. False si no aplica, ya se mandó o Meta no lo aceptó."""
    datos = await anuncios_meta.conexion(c)
    if not datos.get("token") or anuncios_meta.vencida(datos) or tipo not in EVENTOS:
        return False
    if not (canal := _canal(c, user_id, await c.store.get("fb_page_id") or "")):
        return False
    marca = f"capi:{tipo}:{user_id}"
    if tipo in UNA_VEZ and await c.store.get(marca):
        return False
    nombre, dueno, user_data = canal
    evento = {"event_name": EVENTOS[tipo], "event_time": int(time.time()), "action_source": "business_messaging",
              "messaging_channel": nombre, "user_data": user_data,
              "event_id": event_id or f"{tipo}:{user_id}"}  # Meta descarta el mismo event_id si llega dos veces
    if tipo == "venta":
        evento["custom_data"] = {"currency": (moneda or datos.get("moneda") or "MXN").upper(), "value": round(valor, 2)}
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            dataset = await _dataset(http, c, datos, nombre, dueno)
            r = await http.post(f"{FB_GRAPH}/{config.API_VERSION}/{dataset}/events",
                                params={"access_token": datos["token"]}, json={"data": [evento]})
            anuncios_meta._check(r)
    except Exception as e:
        texto = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else str(e)[:300]
        log.warning("Meta no aceptó el evento %s de %s: %s", EVENTOS[tipo], user_id, texto)
        await anuncios_meta.guardar(c, {**await anuncios_meta.conexion(c), "capi_error": texto, "capi_error_t": time.time()})
        return False
    if tipo in UNA_VEZ:
        await c.store.set(marca, str(int(time.time())))
    actual = await anuncios_meta.conexion(c)
    actual.pop("capi_error", None)
    await anuncios_meta.guardar(c, {**actual, "capi_ok": time.time()})
    return True


def avisar(c: Cuenta, user_id: str, tipo: str, valor: float = 0, moneda: str = "", event_id: str = "") -> None:
    """Como mandar(), en segundo plano: quien llama no espera a Meta."""
    async def correr():
        try:
            await mandar(c, user_id, tipo, valor, moneda, event_id)
        except Exception:
            log.exception("No se pudo avisar a Meta el evento %s", tipo)
    tarea = asyncio.get_running_loop().create_task(correr())
    _tareas.add(tarea)  # sin referencia, la tarea se puede perder antes de terminar
    tarea.add_done_callback(_tareas.discard)
