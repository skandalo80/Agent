"""Memoria del bot en SQLite.

Todo el acceso a datos pasa por esta clase: si algún día hace falta Postgres,
se reescribe solo este archivo con los mismos métodos.

La tabla `messages` también funciona como cola: un DM entra como `pending` y
pasa a `done` cuando el bot lo contestó. Así, si el proceso se reinicia, los
mensajes sin contestar no se pierden.
"""
import time
from datetime import datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,              -- 'user' | 'assistant'
    text       TEXT NOT NULL,
    mid        TEXT UNIQUE,                -- id de Instagram, evita duplicados
    status     TEXT NOT NULL DEFAULT 'done', -- 'pending' | 'done' | 'failed'
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_user ON messages (user_id, id);
CREATE INDEX IF NOT EXISTS messages_pending ON messages (status) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS summaries (
    user_id TEXT PRIMARY KEY,
    text    TEXT NOT NULL,
    upto_id INTEGER NOT NULL               -- último mensaje incluido en el resumen
);

CREATE TABLE IF NOT EXISTS facts (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS meetings (
    event_id   TEXT PRIMARY KEY,           -- id del evento en Google Calendar
    user_id    TEXT NOT NULL,
    start      TEXT NOT NULL,              -- ISO 8601 con zona horaria
    email      TEXT NOT NULL,
    meet_link  TEXT,
    status     TEXT NOT NULL DEFAULT 'active' -- 'active' | 'cancelled'
);

CREATE TABLE IF NOT EXISTS emails (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    sent_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT, -- el folio (COT-0001)
    user_id    TEXT NOT NULL,
    token      TEXT NOT NULL UNIQUE,       -- nombre secreto del PDF
    paquete    TEXT NOT NULL,
    pago       TEXT NOT NULL,
    bloques    INTEGER NOT NULL,
    total      REAL NOT NULL,              -- primer pago
    email      TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- DMs que mandó el bot: sus ecos se ignoran; cualquier otro eco es una
-- persona contestando desde Instagram.
CREATE TABLE IF NOT EXISTS sent (
    mid     TEXT PRIMARY KEY,
    sent_at REAL NOT NULL
);

-- Conversaciones donde el bot se queda callado porque las atiende una persona.
CREATE TABLE IF NOT EXISTS paused (
    user_id TEXT PRIMARY KEY,
    until   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    # --- cola de mensajes entrantes ---

    async def add_incoming(self, user_id: str, mid: str, text: str) -> bool:
        """Guarda un DM nuevo. Devuelve False si Meta lo reenvió (duplicado)."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO messages (user_id, role, text, mid, status, created_at)"
            " VALUES (?, 'user', ?, ?, 'pending', ?)",
            (user_id, text, mid, time.time()),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def pending_users(self) -> list[str]:
        cur = await self.db.execute(
            "SELECT DISTINCT user_id FROM messages WHERE status = 'pending'"
        )
        return [r[0] for r in await cur.fetchall()]

    async def pending(self, user_id: str) -> list[tuple[int, str]]:
        cur = await self.db.execute(
            "SELECT id, text FROM messages WHERE user_id = ? AND status = 'pending' ORDER BY id",
            (user_id,),
        )
        return list(await cur.fetchall())

    async def mark_answered(self, user_id: str, ids: list[int], reply: str) -> None:
        """Marca los DMs como contestados y guarda la respuesta, todo junto."""
        marks = ",".join("?" * len(ids))
        await self.db.execute(
            f"UPDATE messages SET status = 'done' WHERE id IN ({marks})", ids
        )
        await self.db.execute(
            "INSERT INTO messages (user_id, role, text, created_at) VALUES (?, 'assistant', ?, ?)",
            (user_id, reply, time.time()),
        )
        await self.db.commit()

    async def mark_seen(self, ids: list[int]) -> None:
        """Saca DMs de la cola sin contestarlos (la conversación la lleva una persona)."""
        marks = ",".join("?" * len(ids))
        await self.db.execute(f"UPDATE messages SET status = 'done' WHERE id IN ({marks})", ids)
        await self.db.commit()

    async def add_owner_message(self, user_id: str, mid: str, text: str) -> None:
        """Guarda lo que contestó el dueño a mano, para que el bot lo vea después."""
        await self.db.execute(
            "INSERT OR IGNORE INTO messages (user_id, role, text, mid, created_at)"
            " VALUES (?, 'assistant', ?, ?, ?)",
            (user_id, f"[contestó una persona del equipo] {text}", mid, time.time()),
        )
        await self.db.commit()

    async def mark_failed(self, ids: list[int]) -> None:
        """Saca de la cola DMs que no se pudieron contestar tras varios intentos."""
        marks = ",".join("?" * len(ids))
        await self.db.execute(
            f"UPDATE messages SET status = 'failed' WHERE id IN ({marks})", ids
        )
        await self.db.commit()

    # --- memoria por usuario ---

    async def recent(self, user_id: str, limit: int) -> list[tuple[str, str]]:
        """Últimos mensajes ya contestados que aún no están en el resumen."""
        cur = await self.db.execute(
            "SELECT role, text FROM messages"
            " WHERE user_id = ? AND status = 'done'"
            " AND id > COALESCE((SELECT upto_id FROM summaries WHERE user_id = ?), 0)"
            " ORDER BY id DESC LIMIT ?",
            (user_id, user_id, limit),
        )
        return list(reversed(await cur.fetchall()))

    async def summary(self, user_id: str) -> str:
        cur = await self.db.execute("SELECT text FROM summaries WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else ""

    async def to_summarize(self, user_id: str, keep: int) -> list[tuple[int, str, str]]:
        """Mensajes más viejos que los `keep` recientes y aún sin resumir."""
        cur = await self.db.execute(
            "SELECT id, role, text FROM messages"
            " WHERE user_id = ? AND status = 'done'"
            " AND id > COALESCE((SELECT upto_id FROM summaries WHERE user_id = ?), 0)"
            " ORDER BY id",
            (user_id, user_id),
        )
        rows = list(await cur.fetchall())
        return rows[:-keep] if len(rows) > keep else []

    async def save_summary(self, user_id: str, text: str, upto_id: int) -> None:
        await self.db.execute(
            "INSERT INTO summaries (user_id, text, upto_id) VALUES (?, ?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET text = excluded.text, upto_id = excluded.upto_id",
            (user_id, text, upto_id),
        )
        await self.db.commit()

    async def facts(self, user_id: str) -> dict[str, str]:
        cur = await self.db.execute(
            "SELECT key, value FROM facts WHERE user_id = ? ORDER BY key", (user_id,)
        )
        return dict(await cur.fetchall())

    async def set_fact(self, user_id: str, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO facts (user_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, value),
        )
        await self.db.commit()

    async def delete_fact(self, user_id: str, key: str) -> None:
        await self.db.execute("DELETE FROM facts WHERE user_id = ? AND key = ?", (user_id, key))
        await self.db.commit()

    async def user_wrote(self, user_id: str, fragment: str) -> bool:
        """True si la persona escribió `fragment` en algún DM (sin distinguir mayúsculas)."""
        cur = await self.db.execute(
            "SELECT 1 FROM messages WHERE user_id = ? AND role = 'user'"
            " AND instr(lower(text), lower(?)) > 0 LIMIT 1",
            (user_id, fragment),
        )
        return await cur.fetchone() is not None

    # --- reuniones y correos que hace el agente ---

    async def add_meeting(self, event_id, user_id, start, email, meet_link) -> None:
        await self.db.execute(
            "INSERT INTO meetings (event_id, user_id, start, email, meet_link) VALUES (?, ?, ?, ?, ?)",
            (event_id, user_id, start, email, meet_link),
        )
        await self.db.commit()

    async def meetings(self, user_id: str, after_iso: str) -> list[tuple[str, str, str, str]]:
        """Reuniones activas de la persona que empiezan después de `after_iso` (UTC)."""
        cur = await self.db.execute(
            "SELECT event_id, start, email, meet_link FROM meetings"
            " WHERE user_id = ? AND status = 'active' ORDER BY start",
            (user_id,),
        )
        after = datetime.fromisoformat(after_iso)
        return [r for r in await cur.fetchall() if datetime.fromisoformat(r[1]) > after]

    async def cancel_meeting(self, event_id: str) -> None:
        await self.db.execute("UPDATE meetings SET status = 'cancelled' WHERE event_id = ?", (event_id,))
        await self.db.commit()

    async def log_email(self, user_id: str, to_addr: str, subject: str) -> None:
        await self.db.execute(
            "INSERT INTO emails (user_id, to_addr, subject, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, to_addr, subject, time.time()),
        )
        await self.db.commit()

    async def emails_since(self, user_id: str, since: float) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM emails WHERE user_id = ? AND sent_at > ?", (user_id, since)
        )
        return (await cur.fetchone())[0]

    async def add_quote(self, user_id, token, paquete, pago, bloques, total, email) -> int:
        cur = await self.db.execute(
            "INSERT INTO quotes (user_id, token, paquete, pago, bloques, total, email, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, token, paquete, pago, bloques, total, email, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid

    # --- atención humana ---

    async def add_sent(self, mid: str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO sent (mid, sent_at) VALUES (?, ?)", (mid, time.time())
        )
        await self.db.commit()

    async def was_sent(self, mid: str) -> bool:
        cur = await self.db.execute("SELECT 1 FROM sent WHERE mid = ?", (mid,))
        return await cur.fetchone() is not None

    async def bot_said(self, user_id: str, text: str, since: float) -> bool:
        """True si `text` es parte de una respuesta reciente del bot (respaldo por si el id no coincide)."""
        cur = await self.db.execute(
            "SELECT 1 FROM messages WHERE user_id = ? AND role = 'assistant' AND created_at > ?"
            " AND instr(text, ?) > 0 LIMIT 1",
            (user_id, since, text.strip()),
        )
        return await cur.fetchone() is not None

    async def pause(self, user_id: str, until: float) -> None:
        await self.db.execute(
            "INSERT INTO paused (user_id, until) VALUES (?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET until = MAX(until, excluded.until)",
            (user_id, until),
        )
        await self.db.commit()

    async def paused_until(self, user_id: str) -> float:
        cur = await self.db.execute("SELECT until FROM paused WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0.0

    # --- valores sueltos (token de Instagram, etc.) ---

    async def get(self, key: str) -> str | None:
        cur = await self.db.execute("SELECT value FROM kv WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.db.commit()
