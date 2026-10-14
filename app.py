"""Etapa 1: webhook de Instagram que responde "hola" a cada DM."""
import hashlib
import hmac
import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

load_dotenv()

VERIFY_TOKEN = os.environ["IG_VERIFY_TOKEN"]
APP_SECRET = os.environ["IG_APP_SECRET"]
ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
API_VERSION = os.getenv("IG_API_VERSION", "v23.0")
REPLY_TEXT = os.getenv("REPLY_TEXT", "hola")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("igbot")

app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/webhook")
def verify(request: Request):
    """Meta llama aquí una vez para confirmar que el webhook es nuestro."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="verify token incorrecto")


def signature_ok(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


async def send_text(recipient_id: str, text: str) -> None:
    url = f"https://graph.instagram.com/{API_VERSION}/me/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
        )
    if r.is_error:
        log.error("Error enviando mensaje: %s %s", r.status_code, r.text)
    else:
        log.info("Respondido a %s", recipient_id)


@app.post("/webhook")
async def receive(request: Request):
    body = await request.body()
    if not signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="firma inválida")

    data = await request.json()
    log.info("Evento recibido: %s", data)

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            message = event.get("message")
            # Ignorar lo que no sea un mensaje y nuestros propios envíos (eco),
            # si no el bot se respondería a sí mismo en bucle.
            if not message or message.get("is_echo"):
                continue
            await send_text(event["sender"]["id"], REPLY_TEXT)

    return {"ok": True}
