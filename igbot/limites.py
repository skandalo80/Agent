"""Límites contra abuso: que nadie llene el bot de mensajes ni lo use como un
ChatGPT gratis. Que solo hable del negocio lo dice el prompt (prompts/comun.md);
aquí van los topes que no dependen de Claude:

- Cada DM se corta a MAX_MESSAGE_CHARS antes de llegar a Claude.
- Cada persona recibe como mucho MAX_REPLIES_PER_USER_PER_DAY respuestas al día.
- La cuenta gasta como mucho DAILY_BUDGET_USD al día en atender (DMs, comentarios, historias).

El gasto se anota en dos rubros: `atencion` (lo que contesta a personas) y `contenido` (campañas,
posts, imágenes, informes). El presupuesto diario solo cuenta el de atención, para que generar una
campaña nunca deje mudo al asistente; el contenido ya tiene su tope de imágenes por mes. Lo que no
diga en qué rubro va cae en contenido (así un olvido nunca calla al asistente). Los días son de la
zona horaria del negocio. Los administradores no tienen tope.
"""
import contextvars
import logging

from . import tools
from .cuenta import cuenta
from .store import Store

log = logging.getLogger("igbot.limites")

LIMIT_NOTICE = (
    "Por hoy ya llegamos al límite de mensajes por aquí. Mañana te sigo contestando sin problema."
)


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


async def replies_today(db: Store, user_id: str) -> int:
    return await db.bot_replies_since(user_id, today_start())


async def cabe_generacion(store: Store, ahora: float | None = None) -> bool:
    """True si la cuenta puede pedir otra generación de texto con IA desde el panel esta hora (y la anota)."""
    from . import config, unavez
    if config.GENERACIONES_HORA <= 0:
        return True
    return await unavez.cabe(store, "generaciones", config.GENERACIONES_HORA, 3600, ahora)


TOPE_GENERACIONES = ("Ya pediste muchas propuestas en la última hora. Espera un rato y vuelve a intentar; lo que ya"
                     " generaste sigue aquí.")


async def add_cost(usd: float, rubro: str = "") -> None:
    """Suma al gasto del día de la cuenta actual, al total y al rubro (el que se pasa o el del contexto).
    Sin base (pruebas sueltas) no se anota."""
    store = cuenta().store
    if store is None or not usd:
        return
    for key in (_cost_key(), _cost_key(rubro or _rubro.get())):
        spent = float(await store.get(key) or 0) + usd
        await store.set(key, f"{spent:.6f}")


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
