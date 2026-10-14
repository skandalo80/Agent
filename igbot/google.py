"""Cliente mínimo de Google Calendar y Gmail.

Actúa como tu cuenta de Gmail con un refresh token OAuth que se obtiene una
sola vez con scripts/google_auth.py, y solo con los permisos de SCOPES.
"""
import asyncio
import base64
import uuid
from email.message import EmailMessage
from email.utils import formataddr

import httpx

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/gmail.send",
]
CAL = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1"


class Google:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        from google.oauth2.credentials import Credentials

        self._creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        self.http = httpx.AsyncClient(timeout=20)
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if not self._creds.valid:
                from google.auth.transport.requests import Request

                await asyncio.to_thread(self._creds.refresh, Request())
            return self._creds.token

    async def _call(self, method: str, url: str, **kw) -> dict:
        r = await self.http.request(
            method, url, headers={"Authorization": f"Bearer {await self.token()}"}, **kw
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    async def busy(self, start_iso: str, end_iso: str) -> list[tuple[str, str]]:
        data = await self._call(
            "POST", f"{CAL}/freeBusy",
            json={"timeMin": start_iso, "timeMax": end_iso, "items": [{"id": "primary"}]},
        )
        return [(b["start"], b["end"]) for b in data["calendars"]["primary"].get("busy", [])]

    async def create_meeting(
        self, start_iso: str, end_iso: str, tz: str, email: str, summary: str, description: str
    ) -> tuple[str, str]:
        """Crea el evento con Google Meet e invita a `email`. Devuelve (id, link)."""
        event = await self._call(
            "POST", f"{CAL}/calendars/primary/events",
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json={
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_iso, "timeZone": tz},
                "end": {"dateTime": end_iso, "timeZone": tz},
                "attendees": [{"email": email}],
                "conferenceData": {"createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }},
            },
        )
        return event["id"], event.get("hangoutLink", "")

    async def cancel_meeting(self, event_id: str) -> None:
        await self._call(
            "DELETE", f"{CAL}/calendars/primary/events/{event_id}", params={"sendUpdates": "all"}
        )

    async def send_email(
        self, sender: str, sender_name: str, to: str, cc: str, subject: str, body: str
    ) -> None:
        """Envía desde tu Gmail. `sender` debe ser tu dirección (o un alias suyo)."""
        msg = EmailMessage()
        if sender:
            msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        await self._call("POST", f"{GMAIL}/users/me/messages/send", json={"raw": raw})
