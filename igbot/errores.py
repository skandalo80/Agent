"""Errores inesperados: una página amable en vez de "Internal Server Error" y un correo a soporte.

El correo lleva la ruta, el error y las últimas líneas del traceback (nunca el cuerpo del
formulario, que puede traer datos de clientes). Para no llenar el buzón, el mismo error en la
misma ruta se avisa una vez por hora y como mucho MAX_CORREOS_DIA al día; el log (journalctl)
siempre lo tiene completo.
"""
import logging
import time
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse

from . import panel, skandi

log = logging.getLogger("igbot.errores")

MAX_CORREOS_DIA = 20
REPETIR_CADA = 3600  # segundos antes de volver a avisar del mismo error

_avisados: dict[tuple[str, str], float] = {}
_dia = {"fecha": "", "enviados": 0}


def toca_avisar(clave: tuple[str, str], ahora: float | None = None) -> bool:
    ahora = time.time() if ahora is None else ahora
    hoy = time.strftime("%Y-%m-%d", time.localtime(ahora))
    if _dia["fecha"] != hoy:
        _dia.update(fecha=hoy, enviados=0)
    ultimo = _avisados.get(clave)
    if _dia["enviados"] >= MAX_CORREOS_DIA or (ultimo is not None and ahora - ultimo < REPETIR_CADA):
        return False
    _avisados[clave] = ahora
    _dia["enviados"] += 1
    return True


async def avisar(request: Request, exc: Exception) -> None:
    clave = (request.url.path, type(exc).__name__)
    if not toca_avisar(clave):
        return
    quien = panel.sesion(request) or "sin sesión"
    pila = "".join(traceback.format_exception(exc)[-6:])
    texto = (f"Error inesperado en {request.method} {request.url.path}\n"
             f"Cuenta (id de Instagram): {quien}\n\n{type(exc).__name__}: {exc}\n\n{pila}\n"
             "Completo en el servidor: journalctl -u igbot --since '10 min ago'")
    await skandi.correo_a_soporte(f"Error en el panel: {type(exc).__name__} en {request.url.path}", texto)


async def inesperado(request: Request, exc: Exception):
    """Lo registra, avisa por correo y contesta con la página de error (o JSON fuera del panel)."""
    log.error("Error inesperado en %s %s", request.method, request.url.path, exc_info=exc)
    try:
        await avisar(request, exc)
    except Exception:
        log.exception("No se pudo avisar del error")
    if request.url.path.startswith(("/panel", "/conectar")):
        try:
            return await panel.error_inesperado(request)
        except Exception:
            log.exception("No se pudo armar la página de error")
    return JSONResponse({"detail": "Error interno"}, status_code=500)
