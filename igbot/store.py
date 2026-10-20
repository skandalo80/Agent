"""Memoria del bot en SQLite.

Todo el acceso a datos pasa por esta clase: si algún día hace falta Postgres,
se reescribe solo este archivo con los mismos métodos.

La tabla `messages` también funciona como cola: un DM entra como `pending` y
pasa a `done` cuando el bot lo contestó. Así, si el proceso se reinicia, los
mensajes sin contestar no se pierden.
"""
import json
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

-- Comentarios de las publicaciones que ya se procesaron (y qué se hizo).
CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    media_id   TEXT NOT NULL,
    text       TEXT NOT NULL,
    matched    TEXT NOT NULL DEFAULT '',   -- palabras clave de las reglas que aplicaron
    dm_sent    INTEGER NOT NULL DEFAULT 0,
    replied    INTEGER NOT NULL DEFAULT 0, -- respuesta pública
    created_at REAL NOT NULL
);

-- Fotos que mandó un administrador por DM (el archivo está en MEDIA_DIR/<token>.jpg).
CREATE TABLE IF NOT EXISTS media (
    id         INTEGER PRIMARY KEY AUTOINCREMENT, -- "foto 3" en el chat
    user_id    TEXT NOT NULL,
    token      TEXT NOT NULL UNIQUE,       -- nombre secreto del archivo
    url        TEXT NOT NULL,              -- de dónde se descargó (Instagram)
    note       TEXT NOT NULL DEFAULT '',   -- p. ej. "recortada a 4:5"
    created_at REAL NOT NULL
);

-- Publicaciones que pidió un administrador.
CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,             -- administrador que la pidió
    media_ids   TEXT NOT NULL,             -- ids de la tabla media, separados por comas
    caption     TEXT NOT NULL,
    publish_at  REAL NOT NULL,             -- 0 = en cuanto se confirme
    status      TEXT NOT NULL,             -- borrador | programada | publicando | publicada | fallida | cancelada
    permalink   TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

-- Cosas que pasaron en las conversaciones, para el reporte semanal
-- ('cotizacion' con sus productos y total, 'humano' cuando se pasó a una persona).
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}', -- JSON
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, created_at);

-- Página skandar.online/conectar: negocios que piden conectar su Instagram
-- (para invitarlos como tester) y cuentas que ya lo conectaron con su token.
CREATE TABLE IF NOT EXISTS solicitudes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario    TEXT NOT NULL,              -- @ de Instagram, sin la arroba
    nombre     TEXT NOT NULL,
    correo     TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conexiones (
    ig_id      TEXT PRIMARY KEY,           -- id de la cuenta (el de los webhooks)
    usuario    TEXT NOT NULL,
    token      TEXT NOT NULL,              -- token de larga duración (60 días)
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    slug       TEXT                        -- su carpeta en clientes/ si se dio de alta sola
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
        # Bases de antes del panel: la columna slug se agrega aparte.
        cur = await self.db.execute("PRAGMA table_info(conexiones)")
        if "slug" not in {row[1] for row in await cur.fetchall()}:
            await self.db.execute("ALTER TABLE conexiones ADD COLUMN slug TEXT")
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

    async def bot_replies_since(self, user_id: str, since: float) -> int:
        """Respuestas del bot (no las que el dueño escribió a mano, que llevan mid)."""
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND role = 'assistant'"
            " AND mid IS NULL AND created_at >= ?",
            (user_id, since),
        )
        return (await cur.fetchone())[0]

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

    async def add_bot_message(self, user_id: str, text: str) -> None:
        """Guarda un mensaje que el bot mandó por su cuenta (p. ej. por un comentario)."""
        await self.db.execute(
            "INSERT INTO messages (user_id, role, text, created_at) VALUES (?, 'assistant', ?, ?)",
            (user_id, text, time.time()),
        )
        await self.db.commit()

    async def add_comment(self, comment_id: str, user_id: str, media_id: str, text: str) -> bool:
        """Anota un comentario. False si ya se había procesado (Meta reintenta)."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO comments (comment_id, user_id, media_id, text, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (comment_id, user_id, media_id, text, time.time()),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def comment_result(self, comment_id: str, matched: str, dm_sent: bool, replied: bool) -> None:
        await self.db.execute(
            "UPDATE comments SET matched = ?, dm_sent = ?, replied = ? WHERE comment_id = ?",
            (matched, int(dm_sent), int(replied), comment_id),
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

    async def paused_list(self, now: float) -> list[tuple[str, float, str]]:
        """Conversaciones en pausa: (usuario, hasta, último mensaje del cliente)."""
        cur = await self.db.execute(
            "SELECT p.user_id, p.until, COALESCE((SELECT text FROM messages m WHERE m.user_id = p.user_id"
            " AND m.role = 'user' ORDER BY m.id DESC LIMIT 1), '')"
            " FROM paused p WHERE p.until > ? ORDER BY p.until",
            (now,),
        )
        return list(await cur.fetchall())

    async def unpause(self, user_id: str) -> None:
        await self.db.execute("DELETE FROM paused WHERE user_id = ?", (user_id,))
        await self.db.commit()

    # --- publicaciones de los administradores ---

    async def add_media(self, user_id: str, token: str, url: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO media (user_id, token, url, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token, url, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid

    async def media(self, media_id: int) -> tuple[str, str, str] | None:
        """(token, url, nota) de una foto."""
        cur = await self.db.execute("SELECT token, url, note FROM media WHERE id = ?", (media_id,))
        return await cur.fetchone()

    async def set_media_note(self, media_id: int, note: str) -> None:
        await self.db.execute("UPDATE media SET note = ? WHERE id = ?", (note, media_id))
        await self.db.commit()

    async def add_post(self, user_id: str, media_ids: list[int], caption: str, publish_at: float) -> int:
        cur = await self.db.execute(
            "INSERT INTO posts (user_id, media_ids, caption, publish_at, status, created_at)"
            " VALUES (?, ?, ?, ?, 'borrador', ?)",
            (user_id, ",".join(map(str, media_ids)), caption, publish_at, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid

    POST_FIELDS = "id, user_id, media_ids, caption, publish_at, status, permalink, error, created_at"

    def _post(self, row) -> dict | None:
        if not row:
            return None
        post = dict(zip(self.POST_FIELDS.split(", "), row))
        post["media_ids"] = [int(i) for i in post["media_ids"].split(",") if i]
        return post

    async def post(self, post_id: int) -> dict | None:
        cur = await self.db.execute(f"SELECT {self.POST_FIELDS} FROM posts WHERE id = ?", (post_id,))
        return self._post(await cur.fetchone())

    async def posts(self, statuses: tuple[str, ...], due_before: float | None = None,
                    user_id: str | None = None) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        sql = f"SELECT {self.POST_FIELDS} FROM posts WHERE status IN ({marks})"
        args: list = list(statuses)
        if due_before is not None:
            sql += " AND publish_at <= ?"
            args.append(due_before)
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        cur = await self.db.execute(sql + " ORDER BY publish_at, id", args)
        return [self._post(r) for r in await cur.fetchall()]

    async def update_post(self, post_id: int, caption: str | None = None, publish_at: float | None = None,
                          media_ids: list[int] | None = None) -> None:
        """Cambia el texto, la hora o las fotos de una publicación que aún no sale."""
        for campo, valor in (("caption", caption), ("publish_at", publish_at),
                             ("media_ids", None if media_ids is None else ",".join(map(str, media_ids)))):
            if valor is not None:
                await self.db.execute(f"UPDATE posts SET {campo} = ? WHERE id = ?", (valor, post_id))
        await self.db.commit()

    async def set_post(self, post_id: int, status: str, permalink: str = "", error: str = "",
                       only_if: str | None = None) -> bool:
        """Cambia el estado; con only_if, solo si estaba en ese estado (evita publicar dos veces)."""
        sql = "UPDATE posts SET status = ?, permalink = ?, error = ? WHERE id = ?"
        args = [status, permalink, error, post_id]
        if only_if:
            sql += " AND status = ?"
            args.append(only_if)
        cur = await self.db.execute(sql, args)
        await self.db.commit()
        return cur.rowcount == 1

    # --- reporte semanal ---

    async def add_event(self, user_id: str, kind: str, detail: dict) -> None:
        await self.db.execute(
            "INSERT INTO events (user_id, kind, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, kind, json.dumps(detail, ensure_ascii=False), time.time()),
        )
        await self.db.commit()

    async def events(self, kind: str, since: float, until: float) -> list[tuple[str, dict, float]]:
        cur = await self.db.execute(
            "SELECT user_id, detail, created_at FROM events WHERE kind = ? AND created_at >= ?"
            " AND created_at < ? ORDER BY id",
            (kind, since, until),
        )
        return [(u, json.loads(d), t) for u, d, t in await cur.fetchall()]

    async def week_stats(self, since: float, until: float) -> dict:
        """Números de las conversaciones con clientes (sin las de los administradores)."""
        clients = "user_id NOT LIKE 'admin:%'"
        manual = "[contestó una persona del equipo]%"

        async def one(sql, *args):
            cur = await self.db.execute(sql, (*args,))
            return (await cur.fetchone())[0] or 0

        window = "created_at >= ? AND created_at < ?"
        return {
            "personas": await one(
                f"SELECT COUNT(DISTINCT user_id) FROM messages WHERE role = 'user' AND {clients} AND {window}",
                since, until),
            "nuevas": await one(
                f"SELECT COUNT(*) FROM (SELECT user_id, MIN(created_at) AS first FROM messages"
                f" WHERE role = 'user' AND {clients} GROUP BY user_id) WHERE first >= ? AND first < ?",
                since, until),
            "recibidos": await one(
                f"SELECT COUNT(*) FROM messages WHERE role = 'user' AND {clients} AND {window}", since, until),
            "respuestas_bot": await one(
                f"SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND {clients} AND {window}"
                " AND text NOT LIKE ?", since, until, manual),
            "manuales": await one(
                f"SELECT COUNT(DISTINCT user_id) FROM messages WHERE role = 'assistant' AND {window}"
                " AND text LIKE ?", since, until, manual),
            "cotizaciones_pdf": await one(f"SELECT COUNT(*) FROM quotes WHERE {window}", since, until),
            "total_pdf": await one(f"SELECT SUM(total) FROM quotes WHERE {window}", since, until),
        }

    async def meetings_between(self, start_iso: str, end_iso: str) -> int:
        """Reuniones activas en ese rango (las fechas van en la zona del negocio, como se guardan)."""
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM meetings WHERE status = 'active' AND start >= ? AND start < ?",
            (start_iso, end_iso),
        )
        return (await cur.fetchone())[0]

    async def week_comments(self, since: float, until: float) -> list[tuple[str, int, int]]:
        cur = await self.db.execute(
            "SELECT matched, dm_sent, replied FROM comments WHERE created_at >= ? AND created_at < ?",
            (since, until),
        )
        return list(await cur.fetchall())

    async def week_published(self, since: float, until: float) -> list[dict]:
        cur = await self.db.execute(
            f"SELECT {self.POST_FIELDS} FROM posts WHERE status = 'publicada'"
            " AND COALESCE(NULLIF(publish_at, 0), created_at) >= ?"
            " AND COALESCE(NULLIF(publish_at, 0), created_at) < ? ORDER BY id",
            (since, until),
        )
        return [self._post(r) for r in await cur.fetchall()]

    async def client_messages(self, since: float, until: float, limit: int = 400) -> list[tuple[str, str]]:
        """Lo que escribieron los clientes en la semana (lo más reciente primero)."""
        cur = await self.db.execute(
            "SELECT user_id, text FROM messages WHERE role = 'user' AND user_id NOT LIKE 'admin:%'"
            " AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT ?",
            (since, until, limit),
        )
        return list(await cur.fetchall())

    # --- conexión de cuentas de clientes ---

    async def add_solicitud(self, usuario: str, nombre: str, correo: str) -> None:
        await self.db.execute(
            "INSERT INTO solicitudes (usuario, nombre, correo, created_at) VALUES (?, ?, ?, ?)",
            (usuario, nombre, correo, time.time()),
        )
        await self.db.commit()

    async def solicitudes_since(self, since: float) -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM solicitudes WHERE created_at >= ?", (since,))
        return (await cur.fetchone())[0]

    async def save_conexion(self, ig_id: str, usuario: str, token: str, expires_at: float) -> bool:
        """Si la cuenta se vuelve a conectar, se queda el token nuevo. True si es la primera vez."""
        cur = await self.db.execute("SELECT 1 FROM conexiones WHERE ig_id = ?", (ig_id,))
        nueva = await cur.fetchone() is None
        await self.db.execute(
            "INSERT INTO conexiones (ig_id, usuario, token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (ig_id) DO UPDATE SET usuario = excluded.usuario, token = excluded.token,"
            " expires_at = excluded.expires_at, created_at = excluded.created_at",
            (ig_id, usuario, token, expires_at, time.time()),
        )
        await self.db.commit()
        return nueva

    async def set_conexion_slug(self, ig_id: str, slug: str) -> None:
        await self.db.execute("UPDATE conexiones SET slug = ? WHERE ig_id = ?", (slug, ig_id))
        await self.db.commit()

    async def correo_de(self, usuario: str) -> str:
        """El correo que dejó en el formulario de /conectar (el más reciente), si lo llenó."""
        cur = await self.db.execute(
            "SELECT correo FROM solicitudes WHERE usuario = ? ORDER BY created_at DESC LIMIT 1", (usuario,))
        row = await cur.fetchone()
        return row[0] if row else ""

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
