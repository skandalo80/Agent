"""Límites contra abuso: que nadie llene el bot de mensajes ni lo use como un
ChatGPT gratis. Que solo hable del negocio lo dice el prompt (prompts/comun.md);
aquí van los topes que no dependen de Claude:

- Cada DM se corta a MAX_MESSAGE_CHARS antes de llegar a Claude.
- Cada persona recibe como mucho MAX_REPLIES_PER_USER_PER_DAY respuestas al día.
- La cuenta gasta como mucho DAILY_BUDGET_USD al día en atender (DMs, comentarios, historias).

El gasto se anota en dos rubros: `atencion` (lo que contesta a personas) y `contenido` (campañas,
posts, imágenes, informes). El presupuesto diario solo cuenta el de atención, para que generar una
campaña nunca deje mudo al asistente; el contenido tiene su propio presupuesto del mes (contenido_usd_mes, por
plan) además del cupo de imágenes. Lo que no
diga en qué rubro va cae en contenido (así un olvido nunca calla al asistente). Los días son de la
zona horaria del negocio. Los administradores no tienen tope.
"""
import asyncio
import contextvars
import logging

from . import config, tools
from .cuenta import cuenta
from .store import Store

log = logging.getLogger("igbot.limites")

LIMIT_NOTICE = (
    "Por hoy ya llegamos al límite de mensajes por aquí. Mañana te sigo contestando sin problema."
)


# Lo que recibe la persona cuando el asistente no pudo contestarle (Claude falló varias veces o la cuenta agotó
# su presupuesto del día): una sola vez al día por persona, para que no se quede en silencio.
SIN_RESPUESTA = "Recibimos tu mensaje. En un momento te contestamos por aquí."


async def avisar_sin_respuesta(store: Store, user_id: str, motivo: str) -> bool:
    """Le dice a la persona que se le contesta después (una vez al día) y deja el evento `sin_respuesta` para
    que Conversaciones lo muestre. El mensaje no se guarda como respuesta del asistente: así el chat sigue
    saliendo en Sin contestar hasta que alguien le conteste de verdad."""
    await store.add_event(user_id, "sin_respuesta", {"motivo": motivo})
    key = f"aviso_sin_respuesta:{user_id}:{tools.now():%Y-%m-%d}"
    if await store.get(key):
        return False
    await store.set(key, "1")
    try:
        await cuenta().instagram.send_text(user_id, SIN_RESPUESTA)
    except Exception:
        log.exception("No se pudo avisar a %s que se le contesta después", user_id)
        return False
    return True


def trim(text: str) -> str:
    if len(text) <= cuenta().max_message_chars:
        return text
    return text[: cuenta().max_message_chars] + " […mensaje recortado]"


def today_start() -> float:
    return tools.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


ATENCION, CONTENIDO = "atencion", "contenido"
_rubro: contextvars.ContextVar[str] = contextvars.ContextVar("rubro", default=CONTENIDO)


class rubro:
    """`with limites.rubro(limites.ATENCION):` todo gasto dentro cuenta en ese rubro (sigue a las tareas)."""

    def __init__(self, nombre: str):
        self.nombre = nombre

    def __enter__(self):
        self._token = _rubro.set(self.nombre)
        return self

    def __exit__(self, *exc):
        _rubro.reset(self._token)


def _cost_key(rubro: str = "") -> str:
    dia = f"{tools.now():%Y-%m-%d}"
    return f"gasto:{rubro}:{dia}" if rubro else f"gasto:{dia}"


def _clave_mes() -> str:
    """Lo gastado en contenido en el mes (de la zona de la cuenta): contra contenido_usd_mes."""
    return f"gasto_mes:{CONTENIDO}:{tools.now():%Y-%m}"


async def replies_today(db: Store, user_id: str) -> int:
    return await db.bot_replies_since(user_id, today_start())


async def cabe_generacion(store: Store, ahora: float | None = None) -> bool:
    """True si la cuenta puede pedir otra generación de texto con IA desde el panel esta hora (y la anota)."""
    from . import config, unavez
    if config.GENERACIONES_HORA <= 0:
        return True
    return await unavez.cabe(store, "generaciones", config.GENERACIONES_HORA, config.HORA, ahora)


TOPE_GENERACIONES = ("Ya pediste muchas propuestas en la última hora. Espera un rato y vuelve a intentar; lo que ya"
                     " generaste sigue aquí.")


async def add_cost(usd: float, rubro: str = "", imagen: bool = False) -> None:
    """Suma al gasto del día de la cuenta actual, al total y al rubro (el que se pasa o el del contexto); el de
    contenido, también al del mes. Una `imagen` además cuenta para su costo promedio (imagenes_restantes).
    Sin base (pruebas sueltas) no se anota."""
    store = cuenta().store
    if store is None or not usd:
        return
    rubro = rubro or _rubro.get()
    claves = (_cost_key(), _cost_key(rubro)) + ((_clave_mes(),) if rubro == CONTENIDO else ())
    async with _sumando:
        for key in claves:
            spent = float(await store.get(key) or 0) + usd
            await store.set(key, f"{spent:.6f}")
        if imagen:
            n, total = await _imagenes_del_mes(store)
            await store.set(_clave_imagenes(), f"{n + 1} {total + usd:.6f}")


def _clave_imagenes() -> str:
    return f"gasto_mes:imagenes:{tools.now():%Y-%m}"


async def _imagenes_del_mes(store: Store) -> tuple[int, float]:
    """(imágenes generadas este mes, lo que costaron), contando versiones y reintentos."""
    n, _, total = (await store.get(_clave_imagenes()) or "0 0").partition(" ")
    return int(n), float(total or 0)


# Leer y escribir el gasto son dos pasos: las versiones de un anuncio terminan a la vez y se pisaban.
_sumando = asyncio.Lock()


SIN_PRESUPUESTO = ("Ya usaste el presupuesto de contenido de tu plan este mes. Se renueva el día 1; si necesitas"
                   " más antes, escríbenos.")


async def gasto_contenido_mes(store: Store) -> float:
    return float(await store.get(_clave_mes()) or 0)


async def presupuesto_restante(store: Store) -> float | None:
    """US$ que le quedan a la cuenta este mes para crear contenido; None si su plan no tiene tope."""
    tope = cuenta().contenido_usd_mes
    if tope <= 0:
        return None
    return max(0.0, tope - await gasto_contenido_mes(store))


async def imagenes_restantes(store: Store) -> int | None:
    """Más o menos cuántas imágenes le alcanzan todavía con el presupuesto del mes, al costo promedio de las que
    ya generó (o IMAGE_COST_USD si aún no hay); None si su plan no tiene tope."""
    restante = await presupuesto_restante(store)
    if restante is None:
        return None
    n, total = await _imagenes_del_mes(store)
    promedio = total / n if n else config.IMAGE_COST_USD
    return int(round(restante / promedio, 6)) if promedio > 0 else 0  # sin que 1.0 / 0.05 dé 19


async def sin_presupuesto(store: Store) -> bool:
    """True si la cuenta ya gastó su presupuesto de contenido del mes: no se empieza nada nuevo que cueste."""
    return await presupuesto_restante(store) == 0


async def spent_today(db: Store, rubro: str = "") -> float:
    """Gasto del día: total, o solo el del rubro."""
    return float(await db.get(_cost_key(rubro)) or 0)


async def over_budget(db: Store) -> bool:
    """True si lo gastado hoy en atender ya llegó al presupuesto diario (el contenido no cuenta)."""
    return cuenta().daily_budget_usd > 0 and await spent_today(db, ATENCION) >= cuenta().daily_budget_usd


async def first_budget_alert(db: Store) -> bool:
    """True solo la primera vez del día que se pasa el presupuesto (para avisar una vez)."""
    key = f"aviso_{_cost_key()}"
    if await db.get(key):
        return False
    await db.set(key, "1")
    return True
