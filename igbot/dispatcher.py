"""Reparte los DMs pendientes entre conversaciones que corren en paralelo.

Reglas:
- Cada usuario tiene como máximo UNA tarea activa, así sus mensajes se
  contestan en orden y nunca hay dos respuestas cruzadas.
- Antes de contestar se espera `debounce` segundos: si alguien manda
  "hola" / "una pregunta" / "cuánto cuesta?" seguidos, se contestan juntos.
- Si llegan más mensajes mientras se genera la respuesta, la misma tarea
  vuelve a dar la vuelta y los contesta después.
- Entre todos los usuarios, como máximo `max_concurrent` respuestas se
  generan a la vez.

La cola real vive en la base (mensajes `pending`); aquí solo se decide quién
trabaja y cuándo. Si algún día hay varios servidores, esto se cambia por
Redis sin tocar el resto.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from .store import Store

log = logging.getLogger("igbot.dispatcher")

Handler = Callable[[str, list[tuple[int, str]]], Awaitable[None]]


class Dispatcher:
    def __init__(
        self,
        store: Store,
        handle: Handler,
        debounce: float,
        max_concurrent: int,
        retries: int = 3,
        retry_delay: float = 20,
    ):
        self.store = store
        self.handle = handle
        self.debounce = debounce
        self.slots = asyncio.Semaphore(max_concurrent)
        self.retries = retries
        self.retry_delay = retry_delay
        self.active: dict[str, asyncio.Task] = {}
        self.dirty: set[str] = set()

    def notify(self, user_id: str) -> None:
        """Avisa que `user_id` tiene mensajes nuevos en la base."""
        self.dirty.add(user_id)
        if user_id not in self.active:
            self.active[user_id] = asyncio.create_task(self._run(user_id))

    async def resume_pending(self) -> None:
        """Al arrancar, retoma los DMs que quedaron sin contestar."""
        for user_id in await self.store.pending_users():
            self.notify(user_id)

    async def drain(self) -> None:
        """Espera a que terminen todas las conversaciones activas (para pruebas)."""
        while self.active:
            await asyncio.gather(*self.active.values(), return_exceptions=True)

    async def stop(self) -> None:
        """Corta las conversaciones en curso; lo pendiente se retoma al arrancar."""
        for task in list(self.active.values()):
            task.cancel()
        await asyncio.gather(*self.active.values(), return_exceptions=True)

    async def _run(self, user_id: str) -> None:
        try:
            failures = 0
            while True:
                await asyncio.sleep(self.debounce)
                self.dirty.discard(user_id)
                rows = await self.store.pending(user_id)
                if not rows:
                    if user_id in self.dirty:
                        continue  # llegó algo mientras leíamos la base
                    return
                try:
                    async with self.slots:
                        await self.handle(user_id, rows)
                    failures = 0
                except Exception:
                    failures += 1
                    log.exception("Fallo contestando a %s (intento %d)", user_id, failures)
                    if failures >= self.retries:
                        await self.store.mark_failed([r[0] for r in rows])
                        failures = 0
                    else:
                        await asyncio.sleep(self.retry_delay * failures)
        finally:
            self.active.pop(user_id, None)
