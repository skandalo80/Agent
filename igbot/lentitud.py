"""Anota en el registro las peticiones lentas, para saber qué hace lento al panel.

Cada petición que tarda más de LENTO_SEG en empezar a responder deja una línea
"LENTO" con su ruta, el total y cuánto de ese tiempo se fue en la base (SQLite),
en Instagram, en la IA (OpenAI / Anthropic) y en otras llamadas HTTP. Lo que
sobra ("resto") es nuestro código: armar la página o esperar al bucle.

Aparte, `vigilar_bucle` avisa con "BLOQUEO" cuando algo síncrono detiene el
bucle de asyncio (entonces todas las peticiones esperan, aunque no hagan nada).

Para leerlas en el servidor:
  sudo journalctl -u igbot --since "30 min ago" | grep -E "LENTO|BLOQUEO"
"""
import asyncio
import logging
import time
from contextvars import ContextVar

import aiosqlite
import httpx

log = logging.getLogger("igbot.lento")

LENTO_SEG = 2.0
BLOQUEO_SEG = 1.0

# Cuántas veces pasó hoy (lo muestra /panel/salud); se reinicia al cambiar el día.
CONTADORES: dict = {"dia": "", "lentos": 0, "bloqueos": 0}


def contar(clave: str) -> None:
    hoy = time.strftime("%Y-%m-%d")
    if CONTADORES["dia"] != hoy:
        CONTADORES.update(dia=hoy, lentos=0, bloqueos=0)
    CONTADORES[clave] += 1

# Lo que lleva gastado la petición en curso: {"db": [veces, segundos], ...}.
GASTO: ContextVar[dict | None] = ContextVar("gasto", default=None)

HOSTS = {
    "instagram.com": "ig", "facebook.com": "ig",
    "openai.com": "ia", "anthropic.com": "ia",
}


def sumar(tipo: str, seg: float) -> None:
    if (g := GASTO.get()) is not None:
        n = g.setdefault(tipo, [0, 0.0])
        n[0] += 1
        n[1] += seg


def tipo_de(host: str) -> str:
    return next((t for h, t in HOSTS.items() if host == h or host.endswith("." + h)), "http")


def instalar() -> None:
    """Mide cada consulta a SQLite y cada llamada HTTP (una sola vez por proceso)."""
    if getattr(aiosqlite.Connection, "_medido", False):
        return
    ejecutar, enviar = aiosqlite.Connection._execute, httpx.AsyncClient.send

    async def _execute(self, fn, *args, **kwargs):
        t = time.perf_counter()
        try:
            return await ejecutar(self, fn, *args, **kwargs)
        finally:
            sumar("db", time.perf_counter() - t)

    async def send(self, request, *args, **kwargs):
        t = time.perf_counter()
        try:
            return await enviar(self, request, *args, **kwargs)
        finally:
            sumar(tipo_de(request.url.host or ""), time.perf_counter() - t)

    aiosqlite.Connection._execute, httpx.AsyncClient.send = _execute, send
    aiosqlite.Connection._medido = True


def linea(metodo: str, ruta: str, total: float, gasto: dict) -> str:
    partes = [f"LENTO {total:.2f}s {metodo} {ruta}"]
    usado = 0.0
    for tipo in ("db", "ig", "ia", "http"):
        veces, seg = gasto.get(tipo, (0, 0.0))
        if veces:
            partes.append(f"{tipo} {veces}× {seg:.2f}s")
            usado += seg
    partes.append(f"resto {max(total - usado, 0):.2f}s")
    return " · ".join(partes)


class Medir:
    """Middleware ASGI: mide hasta que sale la cabecera de la respuesta (así un
    flujo largo, como el chat de prueba, no cuenta como lento)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        gasto: dict = {}
        GASTO.set(gasto)
        inicio = time.perf_counter()
        listo = False

        def anotar():
            nonlocal listo
            if listo:
                return
            listo = True
            if (total := time.perf_counter() - inicio) >= LENTO_SEG:
                contar("lentos")
                log.warning(linea(scope.get("method", ""), scope.get("path", ""), total, gasto))

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                anotar()
            await send(msg)

        try:
            await self.app(scope, receive, enviar)
        finally:
            anotar()


async def vigilar_bucle(cada: float = 0.5) -> None:
    """Avisa cuando el bucle se quedó trabado (algo síncrono lo detuvo)."""
    while True:
        t = time.perf_counter()
        await asyncio.sleep(cada)
        if (tarde := time.perf_counter() - t - cada) >= BLOQUEO_SEG:
            contar("bloqueos")
            log.warning("BLOQUEO el bucle estuvo detenido %.2fs", tarde)
