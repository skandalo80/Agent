"""Botones de inicio (ice breakers): preguntas que Instagram muestra al abrir un chat nuevo.

El cliente los escribe en su panel (Configura tu asistente > Reglas y límites) y se guardan en su base
(kv "botones", una lista). Se mandan a Instagram al guardarlos y al arrancar, solo si cambiaron. Cuando
alguien toca uno, llega como un mensaje con el texto de la pregunta y el asistente lo contesta igual que
cualquier DM.

Antes vivían en un botones.yaml por cuenta: la primera vez que una cuenta arranca sin kv "botones" se
toman de ese archivo (si sigue en el servidor) o de los que Instagram ya tiene puestos, para no perderlos.
"""
import hashlib
import json
import logging
from pathlib import Path

import yaml

from .cuenta import Cuenta
from .instagram import Instagram
from .store import Store

log = logging.getLogger("igbot.botones")

MAX_BOTONES, MAX_CARACTERES = 4, 80


def limpiar(preguntas) -> list[str]:
    """Hasta 4 preguntas sin repetir, de hasta 80 caracteres, sin las vacías."""
    out: list[str] = []
    for q in preguntas:
        q = " ".join(str(q or "").split())[:MAX_CARACTERES]
        if q and q not in out:
            out.append(q)
    return out[:MAX_BOTONES]


async def de(store: Store) -> list[str] | None:
    """Los botones guardados; None si la cuenta nunca los configuró (ni se migraron)."""
    guardado = await store.get("botones")
    return None if guardado is None else limpiar(json.loads(guardado))


def _huella(preguntas: list[str]) -> str:
    return hashlib.sha256(json.dumps(preguntas, ensure_ascii=False).encode()).hexdigest()


def _archivo_viejo(c: Cuenta) -> list[str]:
    archivo = Path(f"clientes/{c.slug}/botones.yaml" if c.slug else "negocio/botones.yaml")
    if not archivo.is_file():
        return []
    try:
        return limpiar((yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}).get("preguntas") or [])
    except Exception:
        log.exception("No se pudo leer %s", archivo)
        return []


async def _migrar(c: Cuenta, store: Store, instagram: Instagram) -> list[str]:
    """La primera vez: los del archivo de antes o los que Instagram ya muestra."""
    if preguntas := _archivo_viejo(c):
        await store.set("botones", json.dumps(preguntas, ensure_ascii=False))
        log.info("Botones de inicio pasados del archivo al panel")
        return preguntas
    try:
        preguntas = limpiar(await instagram.ice_breakers())
    except Exception:
        log.exception("No se pudieron leer los botones de inicio de Instagram")
        return []  # se vuelve a intentar en el siguiente arranque
    await store.set("botones", json.dumps(preguntas, ensure_ascii=False))
    await store.set("botones_hash", _huella(preguntas))  # ya están en Instagram: no se vuelven a mandar
    return preguntas


async def sync(c: Cuenta) -> None:
    """Manda los botones a Instagram si cambiaron desde la última vez (vacíos = se quitan)."""
    store, instagram = c.store, c.instagram
    preguntas = await de(store)
    if preguntas is None:
        preguntas = await _migrar(c, store, instagram)
        if await store.get("botones") is None:
            return
    huella = _huella(preguntas)
    if await store.get("botones_hash") == huella:
        return
    if not preguntas and await store.get("botones_hash") is None:
        await store.set("botones_hash", huella)  # nunca tuvo botones: no hay nada que quitar
        return
    try:
        if preguntas:
            await instagram.set_ice_breakers(preguntas)
        else:
            await instagram.delete_ice_breakers()
    except Exception:
        log.exception("No se pudieron poner los botones de inicio")
        return
    await store.set("botones_hash", huella)
    log.info("Botones de inicio actualizados (%d)", len(preguntas))


async def guardar(c: Cuenta, preguntas: list[str]) -> None:
    """Lo que escribió el cliente en el panel: se guarda y se manda a Instagram."""
    await c.store.set("botones", json.dumps(limpiar(preguntas), ensure_ascii=False))
    if c.instagram:
        await sync(c)
