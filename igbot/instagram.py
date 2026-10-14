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

    async def send_text(self, recipient_id: str, text: str) -> None:
        token = await self.token()
        for chunk in split_text(text):
            r = await self.http.post(
                f"{GRAPH}/{config.API_VERSION}/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"recipient": {"id": recipient_id}, "message": {"text": chunk}},
            )
            r.raise_for_status()
        log.info("Respondido a %s", recipient_id)

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
