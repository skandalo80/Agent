"""Límites contra abuso: que nadie llene el bot de mensajes ni lo use como un
ChatGPT gratis. Que solo hable del negocio lo dice el prompt (prompts/comun.md);
aquí van los topes que no dependen de Claude:

- Cada DM se corta a MAX_MESSAGE_CHARS antes de llegar a Claude.
- Cada persona recibe como mucho MAX_REPLIES_PER_USER_PER_DAY respuestas al día.
- La cuenta gasta como mucho DAILY_BUDGET_USD al día en Claude.

Los días son de la zona horaria del negocio. Los administradores no tienen tope.
"""
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


def _cost_key() -> str:
    return f"gasto:{tools.now():%Y-%m-%d}"


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


async def add_cost(usd: float) -> None:
    """Suma al gasto del día de la cuenta actual (sin base, en pruebas sueltas, no se anota)."""
    store = cuenta().store
    if store is None or not usd:
        return
    spent = float(await store.get(_cost_key()) or 0) + usd
    await store.set(_cost_key(), f"{spent:.6f}")


async def spent_today(db: Store) -> float:
    return float(await db.get(_cost_key()) or 0)


async def over_budget(db: Store) -> bool:
    return cuenta().daily_budget_usd > 0 and await spent_today(db) >= cuenta().daily_budget_usd


async def first_budget_alert(db: Store) -> bool:
    """True solo la primera vez del día que se pasa el presupuesto (para avisar una vez)."""
    key = f"aviso_{_cost_key()}"
    if await db.get(key):
        return False
    await db.set(key, "1")
    return True
