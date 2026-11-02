"""Lo común de todas las conexiones por OAuth (Instagram, Facebook, anuncios de Meta, Google Calendar, Shopify,
Mercado Pago y Stripe) y del login con Google: el `state`, su cookie y la vuelta.

El state es `ambito.cuenta.tiempo.azar.firma`, firmado con SESSION_SECRET: dice para qué conexión se hizo
(ambito), de qué cuenta (su slug, o "-" si no hay sesión: conectar, entrar) y cuándo. Al volver se exige que
sea el de la cookie (contra un enlace armado) salvo donde el proveedor regresa en otro navegador (Instagram y
Facebook en el celular vuelven en el suyo, sin la cookie de Safari): ahí basta la firma fresca. Cada state
sirve una sola vez: gastable() lo anota en la base (unavez), así un reinicio no lo vuelve a dar por bueno.
"""
import hashlib
import hmac
import secrets
import time

from fastapi.responses import RedirectResponse

from . import config, unavez
from .cuenta import Cuenta

STATE_TTL = 900  # segundos que vale un state: lo que tarda alguien en autorizar


def firmar(texto: str) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), f"oauth:{texto}".encode(), hashlib.sha256).hexdigest()[:32]


def _slug(c: Cuenta | None) -> str:
    return (c.slug or "-") if c is not None else "-"


def nuevo_state(ambito: str, c: Cuenta | None = None) -> str:
    base = f"{ambito}.{_slug(c)}.{int(time.time())}.{secrets.token_urlsafe(12)}"
    return f"{base}.{firmar(base)}"


def state_valido(state: str, cookie: str | None, ambito: str, c: Cuenta | None = None,
                 sin_cookie: bool = False) -> bool:
    """Firmado por nosotros para este ámbito y esta cuenta, hace menos de STATE_TTL, y el mismo de la cookie
    (salvo `sin_cookie`, para los proveedores que vuelven en otro navegador)."""
    partes = state.split(".")
    if len(partes) != 5:
        return False
    amb, slug, ts, nonce, firma = partes
    if cookie:
        if not secrets.compare_digest(cookie, state):
            return False
    elif not sin_cookie:
        return False
    return (amb == ambito and slug == _slug(c) and ts.isdigit() and 0 <= time.time() - int(ts) < STATE_TTL
            and secrets.compare_digest(firma, firmar(f"{amb}.{slug}.{ts}.{nonce}")))


async def gastable(state: str, cookie: str | None, ambito: str, c: Cuenta | None = None, store=None,
                   sin_cookie: bool = False) -> bool:
    """state_valido y además lo gasta: la segunda vez con el mismo state es False, aunque se reinicie."""
    if not state_valido(state, cookie, ambito, c, sin_cookie):
        return False
    return await unavez.gastar(store if store is not None else (c.store if c is not None else None),
                               "state:" + state[-24:], STATE_TTL)


def ir(url: str, cookie: str, path: str, state: str) -> RedirectResponse:
    """Manda al diálogo del proveedor y deja el state en su cookie, para compararlo al volver."""
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(cookie, state, max_age=STATE_TTL, path=path, secure=True, httponly=True, samesite="lax")
    return r


def volver(destino: str, cookie: str, path: str) -> RedirectResponse:
    """De regreso a nuestra página, ya sin la cookie del state."""
    r = RedirectResponse(destino, status_code=303)
    r.delete_cookie(cookie, path=path)
    return r
