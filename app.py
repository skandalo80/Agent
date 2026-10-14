"""Etapa 1: webhook de Instagram que responde "hola" a cada DM."""
import hashlib
import hmac
import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

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


PRIVACY_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Política de privacidad</title>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5">
<h1>Política de privacidad</h1>
<p>Este servicio responde automáticamente a los mensajes directos que recibe la cuenta de Instagram asociada.</p>
<p>Solo se procesan el identificador de Instagram de quien escribe y el contenido del mensaje, únicamente para generar la respuesta. No se venden ni se comparten estos datos con terceros.</p>
<p>Para pedir que se borre cualquier dato, escribe un mensaje directo a la cuenta de Instagram indicándolo.</p>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PRIVACY_HTML


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
