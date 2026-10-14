"""Botones de inicio (ice breakers): preguntas que Instagram muestra al abrir un chat nuevo.

Se definen en botones.yaml de la cuenta y se mandan a Instagram al arrancar,
solo si cambiaron. Cuando alguien toca uno, llega como un mensaje con el texto
de la pregunta y el bot lo contesta igual que cualquier DM.
"""
import hashlib
import json
import logging
from pathlib import Path

import yaml

from . import config
from .instagram import Instagram
from .store import Store

log = logging.getLogger("igbot.botones")

MAX_BUTTONS, MAX_CHARS = 4, 80


def load(path: str | None = None) -> list[str]:
    file = Path(path or config.BUTTONS_PATH)
    if not file.is_file():
        return []
    questions = [str(q).strip() for q in (yaml.safe_load(file.read_text(encoding="utf-8")) or {}).get("preguntas", [])]
    questions = [q for q in questions if q]
    if len(questions) > MAX_BUTTONS or any(len(q) > MAX_CHARS for q in questions):
        raise ValueError(f"{file}: máximo {MAX_BUTTONS} preguntas de hasta {MAX_CHARS} caracteres")
    return questions


async def sync(store: Store, instagram: Instagram) -> None:
    """Manda los botones a Instagram si cambiaron desde la última vez."""
    try:
        questions = load()
    except Exception:
        log.exception("No se pudieron leer los botones de inicio")
        return
    if not questions:
        return
    digest = hashlib.sha256(json.dumps(questions, ensure_ascii=False).encode()).hexdigest()
    if await store.get("botones_hash") == digest:
        return
    try:
        await instagram.set_ice_breakers(questions)
    except Exception:
        log.exception("No se pudieron poner los botones de inicio")
        return
    await store.set("botones_hash", digest)
    log.info("Botones de inicio actualizados: %s", " | ".join(questions))
