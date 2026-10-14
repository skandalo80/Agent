"""Envío de DMs y renovación automática del token de Instagram."""
import asyncio
import logging
import time

import httpx

from . import config
from .store import Store

log = logging.getLogger("igbot.instagram")

GRAPH = "https://graph.instagram.com"
MAX_CHARS = 1000  # límite de Instagram por mensaje de texto
REFRESH_EVERY = 7 * 24 * 3600  # el token dura 60 días; lo renovamos cada semana


class Instagram:
    def __init__(self, store: Store):
        self.store = store
        self.http = httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        await self.http.aclose()

    async def token(self) -> str:
        return await self.store.get("ig_token") or config.ACCESS_TOKEN

    async def _send(self, recipient_id: str, message: dict) -> None:
        await self._send_to({"id": recipient_id}, message)

    async def _send_to(self, recipient: dict, message: dict) -> None:
        r = await self.http.post(
            f"{GRAPH}/{config.API_VERSION}/me/messages",
            headers={"Authorization": f"Bearer {await self.token()}"},
            json={"recipient": recipient, "message": message},
        )
        r.raise_for_status()
        # Se anota el id para reconocer el eco y no confundirlo con el dueño.
        if mid := r.json().get("message_id"):
            await self.store.add_sent(mid)

    async def send_text(self, recipient_id: str, text: str) -> None:
        for chunk in split_text(text):
            await self._send(recipient_id, {"text": chunk})
        log.info("Respondido a %s", recipient_id)

    async def send_file(self, recipient_id: str, url: str) -> None:
        """Manda un PDF (u otro archivo) que Instagram descarga de `url`."""
        await self._send(
            recipient_id, {"attachment": {"type": "file", "payload": {"url": url}}}
        )

    async def private_reply(self, comment_id: str, text: str) -> None:
        """DM a quien comentó (Instagram permite uno por comentario, dentro de 7 días)."""
        await self._send_to({"comment_id": comment_id}, {"text": split_text(text)[0]})

    async def public_reply(self, comment_id: str, text: str) -> None:
        r = await self.http.post(
            f"{GRAPH}/{config.API_VERSION}/{comment_id}/replies",
            headers={"Authorization": f"Bearer {await self.token()}"},
            json={"message": text[:2000]},
        )
        r.raise_for_status()

    async def permalink(self, media_id: str) -> str:
        key = f"permalink:{media_id}"
        if cached := await self.store.get(key):
            return cached
        r = await self.http.get(
            f"{GRAPH}/{config.API_VERSION}/{media_id}",
            params={"fields": "permalink"},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        r.raise_for_status()
        link = r.json().get("permalink", "")
        await self.store.set(key, link)
        return link

    async def subscribe(self, fields: str) -> None:
        """Suscribe esta cuenta a los avisos del webhook (p. ej. 'messages,comments')."""
        r = await self.http.post(
            f"{GRAPH}/{config.API_VERSION}/me/subscribed_apps",
            params={"subscribed_fields": fields},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        r.raise_for_status()
        log.info("Cuenta suscrita a: %s", fields)

    async def account_id(self) -> str:
        """Id de la cuenta de este token (el mismo que viene en los avisos de Meta)."""
        r = await self.http.get(
            f"{GRAPH}/{config.API_VERSION}/me",
            params={"fields": "user_id,username"},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        r.raise_for_status()
        data = r.json()
        log.info("Cuenta de Instagram: @%s (%s)", data.get("username"), data["user_id"])
        return str(data["user_id"])

    async def refresh_token(self) -> None:
        r = await self.http.get(
            f"{GRAPH}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": await self.token()},
        )
        r.raise_for_status()
        data = r.json()
        await self.store.set("ig_token", data["access_token"])
        await self.store.set("ig_token_refreshed_at", str(time.time()))
        days = int(data.get("expires_in", 0)) // 86400
        log.info("Token de Instagram renovado; vence en %d días", days)

    async def refresh_loop(self) -> None:
        """Revisa una vez al día si toca renovar el token."""
        while True:
            last = float(await self.store.get("ig_token_refreshed_at") or 0)
            if time.time() - last > REFRESH_EVERY:
                try:
                    await self.refresh_token()
                except Exception:
                    log.exception("No se pudo renovar el token; se reintenta mañana")
            await asyncio.sleep(24 * 3600)


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Parte un texto largo en trozos, cortando en saltos de línea o espacios."""
    chunks = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks
