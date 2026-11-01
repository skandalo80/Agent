"""Texto con OpenAI para el flujo de campañas: los posts y el prompt de cada imagen (config.POSTS_IA y
PROMPT_IA). Contesta un objeto JSON, como las llamadas a Claude, y suma lo que cuesta al gasto del día."""
import base64
import json
import logging

import httpx

from . import config, limites

log = logging.getLogger("igbot.openai_texto")

API = "https://api.openai.com/v1/chat/completions"


def usar(proceso: str) -> bool:
    """Si este proceso ("posts" o "prompt") lo escribe OpenAI: elegido en el .env y con su clave."""
    eleccion = config.POSTS_IA if proceso == "posts" else config.PROMPT_IA
    return eleccion == "openai" and bool(config.OPENAI_API_KEY)


async def json_de(sistema: str, prompt: str, max_tokens: int = 16000, imagenes: list[bytes] = ()) -> dict:
    """Una llamada al modelo de texto de OpenAI que contesta SOLO un objeto JSON; {} si no se pudo leer.
    Con `imagenes` (JPG), el modelo también las ve (las fotos de referencia de una campaña)."""
    contenido = prompt if not imagenes else [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(x).decode()}"}}
        for x in imagenes]
    cuerpo = {"model": config.OPENAI_TEXT_MODEL, "response_format": {"type": "json_object"},
              "max_completion_tokens": max_tokens,
              "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": contenido}]}
    if config.OPENAI_TEXT_EFFORT:
        cuerpo["reasoning_effort"] = config.OPENAI_TEXT_EFFORT
    async with httpx.AsyncClient(timeout=300) as http:
        r = await http.post(API, json=cuerpo, headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
    if r.status_code != 200:
        log.error("OpenAI texto %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
    datos = r.json()
    uso = datos.get("usage") or {}
    costo = (uso.get("prompt_tokens", 0) * config.OPENAI_TEXT_USD_IN
             + uso.get("completion_tokens", 0) * config.OPENAI_TEXT_USD_OUT) / 1_000_000
    log.info("OpenAI texto (%s): %s tokens, US$%.4f", config.OPENAI_TEXT_MODEL, uso.get("total_tokens"), costo)
    await limites.add_cost(costo, limites.CONTENIDO)
    try:
        salida = json.loads(datos["choices"][0]["message"]["content"] or "{}")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}
    return salida if isinstance(salida, dict) else {}
