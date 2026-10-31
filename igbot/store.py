"""Memoria del bot en SQLite.

Todo el acceso a datos pasa por esta clase: si algún día hace falta Postgres,
se reescribe solo este archivo con los mismos métodos.

La tabla `messages` también funciona como cola: un DM entra como `pending` y
pasa a `done` cuando el bot lo contestó. Así, si el proceso se reinicia, los
mensajes sin contestar no se pierden.
"""
import asyncio
import json
import secrets
import time
from datetime import datetime
from pathlib import Path

import aiosqlite

# Así empieza lo que contestó el dueño a mano (ver add_owner_message).
MANUAL = "[contestó una persona del equipo]"

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
CREATE INDEX IF NOT EXISTS messages_time ON messages (created_at);

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
    created_at  REAL NOT NULL,
    tipo        TEXT NOT NULL DEFAULT 'post', -- post | historia
    ig_id       TEXT NOT NULL DEFAULT ''      -- id que le dio Instagram al publicarla
);

-- Cosas que pasaron en las conversaciones, para el reporte semanal
-- ('cotizacion' con sus productos y total, 'humano' cuando se pasó a una persona, 'interes' cuando pidió
-- cotización, visita o dejó sus datos, 'cita' cuando el asistente agendó una).
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

-- Foto diaria de la cuenta de Instagram (seguidores, alcance, vistas...), para Resultados.
-- Una fila por día (fecha local AAAA-MM-DD); `datos` es JSON y se completa por partes.
CREATE TABLE IF NOT EXISTS metricas_dia (
    dia   TEXT PRIMARY KEY,
    datos TEXT NOT NULL DEFAULT '{}'
);

-- Estadísticas de cada post publicado (alcance, likes...), como las dio Instagram la última vez que se
-- pidieron. Las refresca metricas.py cada día (posts de los últimos 30 días); `datos` es JSON o null si
-- Instagram no las dio. Así Campañas, Pasadas y Resultados no le preguntan a Instagram al abrirse.
CREATE TABLE IF NOT EXISTS insights_post (
    media_id TEXT PRIMARY KEY,
    datos    TEXT,
    leido    REAL NOT NULL
);

-- Chats con Skandi (la ayuda del panel y de la web), en la base de la cuenta principal.
-- origen: "panel" o "web"; quien: la carpeta de la cuenta (panel) o la cookie del visitante (web).
CREATE TABLE IF NOT EXISTS skandi_chats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    origen      TEXT NOT NULL,
    quien       TEXT NOT NULL,
    usuario     TEXT NOT NULL DEFAULT '',   -- @ de Instagram (panel)
    creado      REAL NOT NULL,
    actualizado REAL NOT NULL,
    mensajes    TEXT NOT NULL,              -- JSON [[rol, texto], ...]
    soporte     INTEGER NOT NULL DEFAULT 0  -- 1 si se pasó a soporte
);
CREATE INDEX IF NOT EXISTS skandi_quien ON skandi_chats (origen, quien, actualizado);

-- El anuncio por el que llegó cada chat (anuncios de "Enviar mensaje": Meta lo manda con el primer
-- mensaje). Una fila por persona y anuncio; si vuelve por otro anuncio, se suma otra.
CREATE TABLE IF NOT EXISTS anuncios_chat (
    user_id    TEXT NOT NULL,
    ad_id      TEXT NOT NULL,
    titulo     TEXT NOT NULL DEFAULT '',
    foto       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, ad_id)
);

-- Ventas ligadas a un chat o a un anuncio: pedidos de Shopify, pagos con link (Stripe, Mercado Pago) y las que
-- marca a mano el dueño desde Conversaciones (origen 'manual').
-- user_id vacío = la venta vino de un anuncio pero no de un chat; ad_id vacío = de un chat sin anuncio.
CREATE TABLE IF NOT EXISTS ventas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    origen     TEXT NOT NULL,              -- 'shopify' | 'stripe' | 'mercadopago' | 'manual'
    externo    TEXT NOT NULL,              -- id del pedido en su origen
    user_id    TEXT NOT NULL DEFAULT '',
    ad_id      TEXT NOT NULL DEFAULT '',
    monto      REAL NOT NULL,
    moneda     TEXT NOT NULL DEFAULT '',
    detalle    TEXT NOT NULL DEFAULT '{}', -- JSON: folio, productos
    created_at REAL NOT NULL,
    UNIQUE (origen, externo)
);
CREATE INDEX IF NOT EXISTS ventas_user ON ventas (user_id);

-- El código corto de cada chat que va en los enlaces a la tienda (?sk=...), para saber de qué chat
-- vino un pedido.
CREATE TABLE IF NOT EXISTS codigos_chat (
    codigo  TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE
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
        # Cuántas veces cambiaron los chats desde que arrancó el proceso, y quién espera el siguiente
        # cambio (los paneles abiertos de esta cuenta, ver panel /vivo). Solo en memoria.
        self.cambios = 0
        self._esperando: set[asyncio.Event] = set()

    def avisar(self) -> None:
        """Un chat cambió (mensaje nuevo, respuesta o pausa): despierta a los paneles abiertos."""
        self.cambios += 1
        for e in self._esperando:
            e.set()

    async def esperar_cambio(self, visto: int, segundos: float) -> None:
        """Vuelve en cuanto `cambios` deja de ser `visto`, o a los `segundos`."""
        if self.cambios != visto:
            return
        e = asyncio.Event()
        self._esperando.add(e)
        try:
            await asyncio.wait_for(e.wait(), segundos)
        except TimeoutError:
            pass
        finally:
            self._esperando.discard(e)

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
        # Bases de antes de las historias: todo lo que había era post.
        cur = await self.db.execute("PRAGMA table_info(posts)")
        if "tipo" not in {row[1] for row in await cur.fetchall()}:
            await self.db.execute("ALTER TABLE posts ADD COLUMN tipo TEXT NOT NULL DEFAULT 'post'")
        # Bases de antes de guardar el id de Instagram (para saber a qué historia le respondieron).
        cur = await self.db.execute("PRAGMA table_info(posts)")
        if "ig_id" not in {row[1] for row in await cur.fetchall()}:
            await self.db.execute("ALTER TABLE posts ADD COLUMN ig_id TEXT NOT NULL DEFAULT ''")
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
        if cur.rowcount == 1:
            self.avisar()
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
        self.avisar()

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

    async def add_owner_message(self, user_id: str, mid: str | None, text: str) -> None:
        """Guarda lo que contestó el dueño a mano, para que el bot lo vea después."""
        await self.db.execute(
            "INSERT OR IGNORE INTO messages (user_id, role, text, mid, created_at)"
            " VALUES (?, 'assistant', ?, ?, ?)",
            (user_id, f"{MANUAL} {text}", mid, time.time()),
        )
        await self.db.commit()
        self.avisar()

    async def add_bot_message(self, user_id: str, text: str) -> None:
        """Guarda un mensaje que el bot mandó por su cuenta (p. ej. por un comentario)."""
        await self.db.execute(
            "INSERT INTO messages (user_id, role, text, created_at) VALUES (?, 'assistant', ?, ?)",
            (user_id, text, time.time()),
        )
        await self.db.commit()
        self.avisar()

    async def add_comment(self, comment_id: str, user_id: str, media_id: str, text: str) -> bool:
        """Anota un comentario. False si ya se había procesado (Meta reintenta)."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO comments (comment_id, user_id, media_id, text, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (comment_id, user_id, media_id, text, time.time()),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def comments_since(self, since: float) -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM comments WHERE created_at >= ?", (since,))
        return (await cur.fetchone())[0]

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
        self.avisar()

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

    async def recent_chats(self, limit: int) -> list[tuple[str, float, str, str, str]]:
        """Los últimos chats con clientes: (usuario, hora, rol y texto del último mensaje,
        último mensaje del cliente), del más reciente al más viejo."""
        cur = await self.db.execute(
            "SELECT m.user_id, m.created_at, m.role, m.text, COALESCE((SELECT u.text FROM messages u"
            " WHERE u.user_id = m.user_id AND u.role = 'user' ORDER BY u.id DESC LIMIT 1), '')"
            " FROM messages m JOIN (SELECT MAX(id) AS id FROM messages WHERE user_id NOT LIKE 'admin:%'"
            " GROUP BY user_id) ultimo ON m.id = ultimo.id ORDER BY m.id DESC LIMIT ?",
            (limit,),
        )
        return list(await cur.fetchall())

    def _chats(self, now: float, humano_desde: float, revisar_antes: float, aviso_limite: str) -> tuple[str, dict]:
        """El WITH que da cada chat con clientes y su estado, para la bandeja de Conversaciones.
        Del lado "Tú" (el chat está en pausa: lo atiende una persona):
          "nuevo"  el asistente te lo pasó (pasar_a_humano) y aún no le contestas a mano;
          "tu_sin" el último mensaje es del cliente;  "tu" lo demás.
        "revisar": el asistente sigue encendido pero el cliente escribió hace rato y no hubo respuesta, o
        tocó el límite del plan; sale del lado "Tú", en Sin contestar. "asistente": lo demás."""
        sql = (
            "WITH ult AS (SELECT user_id, MAX(id) AS id FROM messages WHERE user_id NOT LIKE 'admin:%'"
            " GROUP BY user_id"
            # Los que están en pausa salen aunque no tengan mensajes guardados.
            " UNION ALL SELECT user_id, 0 FROM paused WHERE until > :now"
            " AND user_id NOT IN (SELECT user_id FROM messages)),"
            " b AS (SELECT u.user_id, u.id, COALESCE(m.created_at, 0) AS t, COALESCE(m.role, '') AS role,"
            " COALESCE(m.text, '') AS text, p.until AS hasta,"
            " (SELECT MAX(e.created_at) FROM events e WHERE e.kind = 'humano' AND e.user_id = u.user_id"
            " AND e.created_at >= :humano) AS paso"
            " FROM ult u LEFT JOIN messages m ON m.id = u.id"
            " LEFT JOIN paused p ON p.user_id = u.user_id AND p.until > :now),"
            " c AS (SELECT *, CASE WHEN hasta IS NOT NULL THEN CASE"
            " WHEN paso IS NOT NULL AND NOT EXISTS (SELECT 1 FROM messages x WHERE x.user_id = b.user_id"
            " AND x.role = 'assistant' AND substr(x.text, 1, length(:manual)) = :manual AND x.created_at > paso)"
            " THEN 'nuevo' WHEN role = 'user' THEN 'tu_sin' ELSE 'tu' END"
            " WHEN (role = 'user' AND t < :revisar) OR text = :limite THEN 'revisar' ELSE 'asistente' END AS est"
            " FROM b) ")
        return sql, {"now": now, "humano": humano_desde, "manual": MANUAL, "revisar": revisar_antes,
                     "limite": aviso_limite}

    # Cuántos hay en cada pestaña de Conversaciones (sin la búsqueda).
    # Los que el asistente no pudo contestar ("revisar") también te esperan a ti: salen en Sin contestar.
    _CUANTOS = ("SELECT SUM(est = 'nuevo'), SUM(est IN ('tu_sin', 'revisar')),"
                " SUM(est IN ('nuevo', 'tu_sin', 'tu', 'revisar')),"
                " SUM(est = 'asistente' AND t >= :curso), SUM(est = 'asistente') FROM c")
    FILTROS_CHATS = {"nuevos": "est = 'nuevo'", "sin": "est IN ('tu_sin', 'revisar')",
                     "tuyas": "est IN ('nuevo', 'tu_sin', 'tu', 'revisar')",
                     "curso": "est = 'asistente' AND t >= :curso", "bot": "est = 'asistente'"}

    async def chats_page(self, now: float, curso_desde: float, humano_desde: float, revisar_antes: float,
                         aviso_limite: str, ver: str, buscar: str, limit: int,
                         offset: int) -> tuple[list[tuple], dict[str, int], int]:
        """Una página de chats con clientes, filtrada (FILTROS_CHATS) y buscada en la base: solo se
        cargan los `limit` que se ven. Cada fila: (usuario, hora, rol y texto del último mensaje,
        pausa hasta, estado, cuándo te lo pasó el asistente). También da cuántos hay por pestaña (sin
        la búsqueda) y cuántos cumplen filtro y búsqueda, para las páginas."""
        base, args = self._chats(now, humano_desde, revisar_antes, aviso_limite)
        args |= {"curso": curso_desde, "limit": limit, "offset": offset}
        donde = self.FILTROS_CHATS[ver]
        if buscar:
            # En el @usuario ya conocido o en cualquier mensaje del chat.
            args["b"] = "%" + buscar.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            donde += (" AND (user_id IN (SELECT substr(key, 10) FROM kv WHERE key LIKE 'username:%'"
                      " AND value LIKE :b ESCAPE '\\') OR user_id IN (SELECT user_id FROM messages"
                      " WHERE text LIKE :b ESCAPE '\\'))")
        cur = await self.db.execute(base + self._CUANTOS, args)
        cuantos = dict(zip(self.FILTROS_CHATS, (n or 0 for n in await cur.fetchone())))
        cur = await self.db.execute(base + f"SELECT COUNT(*) FROM c WHERE {donde}", args)
        total = (await cur.fetchone())[0]
        cur = await self.db.execute(
            base + f"SELECT user_id, t, role, text, hasta, est, paso FROM c WHERE {donde}"
            " ORDER BY id DESC LIMIT :limit OFFSET :offset", args)
        return list(await cur.fetchall()), cuantos, total

    async def pendientes_tuyos(self, now: float, humano_desde: float, revisar_antes: float, aviso_limite: str) -> int:
        """Chats del lado "Tú" que esperan tu respuesta (nuevos y sin contestar), para el punto del menú."""
        base, args = self._chats(now, humano_desde, revisar_antes, aviso_limite)
        cur = await self.db.execute(base + "SELECT COUNT(*) FROM c WHERE est IN ('nuevo', 'tu_sin', 'revisar')", args)
        return (await cur.fetchone())[0]

    async def chat_messages(self, user_id: str, limit: int) -> tuple[list[tuple[str, str, float]], bool]:
        """Los últimos `limit` mensajes de un chat, del más viejo al más nuevo: (rol, texto, hora).
        También dice si hay más viejos (para "Ver mensajes anteriores")."""
        cur = await self.db.execute(
            "SELECT role, text, created_at FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit + 1))
        filas = list(await cur.fetchall())
        return list(reversed(filas[:limit])), len(filas) > limit

    async def last_message_from(self, user_id: str) -> float:
        """Hora del último mensaje del cliente en ese chat (0 si nunca escribió)."""
        cur = await self.db.execute(
            "SELECT MAX(created_at) FROM messages WHERE user_id = ? AND role = 'user'", (user_id,))
        return (await cur.fetchone())[0] or 0.0

    async def last_client_message(self) -> float:
        cur = await self.db.execute(
            "SELECT MAX(created_at) FROM messages WHERE role = 'user' AND user_id NOT LIKE 'admin:%'")
        return (await cur.fetchone())[0] or 0.0

    async def has_chat(self, user_id: str) -> bool:
        cur = await self.db.execute("SELECT 1 FROM messages WHERE user_id = ? LIMIT 1", (user_id,))
        return await cur.fetchone() is not None

    async def client_messages_since(self, since: float) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'user' AND user_id NOT LIKE 'admin:%' AND created_at >= ?",
            (since,))
        return (await cur.fetchone())[0]

    async def all_bot_replies_since(self, since: float) -> int:
        """Respuestas del bot a todos los clientes desde `since` (las del dueño llevan mid)."""
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND user_id NOT LIKE 'admin:%'"
            " AND mid IS NULL AND created_at >= ?", (since,))
        return (await cur.fetchone())[0]

    async def unpause(self, user_id: str) -> None:
        await self.db.execute("DELETE FROM paused WHERE user_id = ?", (user_id,))
        await self.db.commit()
        self.avisar()

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

    async def add_post(self, user_id: str, media_ids: list[int], caption: str, publish_at: float,
                       tipo: str = "post") -> int:
        """Un borrador; `tipo` es "post" (feed) o "historia"."""
        cur = await self.db.execute(
            "INSERT INTO posts (user_id, media_ids, caption, publish_at, status, created_at, tipo)"
            " VALUES (?, ?, ?, ?, 'borrador', ?, ?)",
            (user_id, ",".join(map(str, media_ids)), caption, publish_at, time.time(), tipo),
        )
        await self.db.commit()
        return cur.lastrowid

    POST_FIELDS = "id, user_id, media_ids, caption, publish_at, status, permalink, error, created_at, tipo, ig_id"

    def _post(self, row) -> dict | None:
        if not row:
            return None
        post = dict(zip(self.POST_FIELDS.split(", "), row))
        post["media_ids"] = [int(i) for i in post["media_ids"].split(",") if i]
        return post

    async def post(self, post_id: int) -> dict | None:
        cur = await self.db.execute(f"SELECT {self.POST_FIELDS} FROM posts WHERE id = ?", (post_id,))
        return self._post(await cur.fetchone())

    async def post_de_instagram(self, ig_id: str) -> dict | None:
        """La publicación que salió con ese id de Instagram (p. ej. la historia a la que le respondieron)."""
        if not ig_id:
            return None
        cur = await self.db.execute(f"SELECT {self.POST_FIELDS} FROM posts WHERE ig_id = ?", (ig_id,))
        return self._post(await cur.fetchone())

    async def posts_by_id(self, ids: list[int]) -> dict[int, dict]:
        """Varios posts de un jalón, por id (los que ya no existen no vienen)."""
        if not ids:
            return {}
        cur = await self.db.execute(
            f"SELECT {self.POST_FIELDS} FROM posts WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        return {p["id"]: p for p in map(self._post, await cur.fetchall())}

    async def posts(self, statuses: tuple[str, ...], due_before: float | None = None,
                    user_id: str | None = None, desde: float | None = None) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        sql = f"SELECT {self.POST_FIELDS} FROM posts WHERE status IN ({marks})"
        args: list = list(statuses)
        if desde is not None:
            sql += " AND publish_at >= ?"
            args.append(desde)
        if due_before is not None:
            sql += " AND publish_at <= ?"
            args.append(due_before)
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        cur = await self.db.execute(sql + " ORDER BY publish_at, id", args)
        return [self._post(r) for r in await cur.fetchall()]

    async def published_page(self, user_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
        """Una página de lo publicado (de lo más nuevo a lo más viejo) y cuántos hay en total."""
        cur = await self.db.execute("SELECT COUNT(*) FROM posts WHERE status = 'publicada' AND user_id = ?",
                                    (user_id,))
        total = (await cur.fetchone())[0]
        cur = await self.db.execute(
            f"SELECT {self.POST_FIELDS} FROM posts WHERE status = 'publicada' AND user_id = ?"
            " ORDER BY publish_at DESC, id DESC LIMIT ? OFFSET ?", (user_id, limit, offset))
        return [self._post(r) for r in await cur.fetchall()], total

    async def published_with(self, user_id: str, codes: list[str], since: float) -> list[dict]:
        """Lo publicado cuyo link lleva alguno de esos códigos de Instagram, o que salió desde `since`."""
        donde = " OR ".join(["instr(permalink, ?) > 0"] * len(codes) + ["publish_at >= ?"])
        cur = await self.db.execute(
            f"SELECT {self.POST_FIELDS} FROM posts WHERE status = 'publicada' AND user_id = ? AND ({donde})"
            " ORDER BY publish_at DESC, id DESC", (user_id, *codes, since))
        return [self._post(r) for r in await cur.fetchall()]

    async def posts_count(self, statuses: tuple[str, ...], since: float) -> int:
        """Cuántos posts con esos estados salen (o se crearon, si no tienen hora) desde `since`."""
        marks = ",".join("?" * len(statuses))
        cur = await self.db.execute(
            f"SELECT COUNT(*) FROM posts WHERE status IN ({marks}) AND COALESCE(NULLIF(publish_at, 0), created_at) >= ?",
            (*statuses, since))
        return (await cur.fetchone())[0]

    async def update_post(self, post_id: int, caption: str | None = None, publish_at: float | None = None,
                          media_ids: list[int] | None = None) -> None:
        """Cambia el texto, la hora o las fotos de una publicación que aún no sale."""
        for campo, valor in (("caption", caption), ("publish_at", publish_at),
                             ("media_ids", None if media_ids is None else ",".join(map(str, media_ids)))):
            if valor is not None:
                await self.db.execute(f"UPDATE posts SET {campo} = ? WHERE id = ?", (valor, post_id))
        await self.db.commit()

    async def set_post(self, post_id: int, status: str, permalink: str = "", error: str = "",
                       only_if: str | None = None, ig_id: str = "") -> bool:
        """Cambia el estado; con only_if, solo si estaba en ese estado (evita publicar dos veces)."""
        sql = "UPDATE posts SET status = ?, permalink = ?, error = ?, ig_id = ? WHERE id = ?"
        args = [status, permalink, error, ig_id, post_id]
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

    async def sin_dato(self, desde: float) -> dict[str, list[tuple[str, float]]]:
        """Las preguntas que el asistente no supo contestar (pregunta_sin_dato) desde `desde` y que siguen
        abiertas: el dueño no ha contestado a mano después ni las marcó resueltas. {usuario: [(pregunta, hora)]}."""
        cur = await self.db.execute(
            "SELECT e.user_id, e.detail, e.created_at FROM events e WHERE e.kind = 'sin_dato'"
            " AND e.created_at >= ? AND e.user_id NOT LIKE 'admin:%'"
            " AND NOT EXISTS (SELECT 1 FROM events r WHERE r.user_id = e.user_id AND r.kind = 'sin_dato_listo'"
            " AND r.created_at >= e.created_at)"
            " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.user_id = e.user_id AND m.role = 'assistant'"
            " AND substr(m.text, 1, ?) = ? AND m.created_at > e.created_at) ORDER BY e.id",
            (desde, len(MANUAL), MANUAL),
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for u, d, t in await cur.fetchall():
            out.setdefault(u, []).append((json.loads(d).get("pregunta", ""), t))
        return out

    async def week_stats(self, since: float, until: float) -> dict:
        """Números de las conversaciones con clientes (sin las de los administradores)."""
        clients = "user_id NOT LIKE 'admin:%'"
        manual = MANUAL + "%"

        async def one(sql, *args):
            cur = await self.db.execute(sql, (*args,))
            return (await cur.fetchone())[0] or 0

        window = "created_at >= ? AND created_at < ?"
        return {
            "personas": await one(
                f"SELECT COUNT(DISTINCT user_id) FROM messages WHERE role = 'user' AND {clients} AND {window}",
                since, until),
            "nuevas": await one(
                # Escribieron en el periodo y nunca antes (sin recorrer toda la historia).
                f"SELECT COUNT(DISTINCT m.user_id) FROM messages m WHERE m.role = 'user' AND m.{clients}"
                " AND m.created_at >= ? AND m.created_at < ? AND NOT EXISTS (SELECT 1 FROM messages x"
                " WHERE x.user_id = m.user_id AND x.role = 'user' AND x.created_at < ?)",
                since, until, since),
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

    async def has_client_messages(self) -> bool:
        """Si algún cliente ya escribió (sin contar a los administradores)."""
        cur = await self.db.execute(
            "SELECT 1 FROM messages WHERE role = 'user' AND user_id NOT LIKE 'admin:%' LIMIT 1")
        return await cur.fetchone() is not None

    async def message_times(self, since: float, until: float, chunk: int = 1000):
        """(usuario, rol, hora, 1 si contestó una persona) de los mensajes con clientes, en orden.
        Llegan de `chunk` en `chunk`: quien los recorre nunca tiene todos en memoria."""
        cur = await self.db.execute(
            "SELECT user_id, role, created_at, text LIKE ? FROM messages WHERE user_id NOT LIKE 'admin:%'"
            " AND created_at >= ? AND created_at < ? ORDER BY id",
            (MANUAL + "%", since, until),
        )
        while filas := await cur.fetchmany(chunk):
            for fila in filas:
                yield fila

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

    async def client_comments(self, since: float, until: float, limit: int = 200) -> list[tuple[str, str]]:
        """Los comentarios que dejaron en las publicaciones (lo más reciente primero)."""
        cur = await self.db.execute(
            "SELECT user_id, text FROM comments WHERE created_at >= ? AND created_at < ?"
            " ORDER BY created_at DESC LIMIT ?",
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

    async def conexion(self, ig_id: str) -> tuple[str, str | None] | None:
        """(usuario, slug) de una cuenta conectada, o None si nunca se conectó."""
        cur = await self.db.execute("SELECT usuario, slug FROM conexiones WHERE ig_id = ?", (ig_id,))
        row = await cur.fetchone()
        return (row[0], row[1]) if row else None

    async def correo_de(self, usuario: str) -> str:
        """El correo que dejó en el formulario de /conectar (el más reciente), si lo llenó."""
        cur = await self.db.execute(
            "SELECT correo FROM solicitudes WHERE usuario = ? ORDER BY created_at DESC LIMIT 1", (usuario,))
        row = await cur.fetchone()
        return row[0] if row else ""

    # --- métricas diarias de Instagram ---

    async def metricas(self, desde: str) -> dict[str, dict]:
        """Los días guardados desde `desde` (AAAA-MM-DD), del más viejo al más nuevo."""
        cur = await self.db.execute("SELECT dia, datos FROM metricas_dia WHERE dia >= ? ORDER BY dia", (desde,))
        return {dia: json.loads(datos) for dia, datos in await cur.fetchall()}

    async def guardar_metricas(self, dia: str, valores: dict) -> None:
        """Suma `valores` a lo que ya tenía ese día (lo que no viene se queda como estaba)."""
        cur = await self.db.execute("SELECT datos FROM metricas_dia WHERE dia = ?", (dia,))
        row = await cur.fetchone()
        datos = {**(json.loads(row[0]) if row else {}), **valores}
        await self.db.execute(
            "INSERT INTO metricas_dia (dia, datos) VALUES (?, ?)"
            " ON CONFLICT (dia) DO UPDATE SET datos = excluded.datos",
            (dia, json.dumps(datos)))
        await self.db.commit()

    # --- estadísticas de cada post ---

    async def insights_post(self, media_id: str) -> tuple[dict | None, float] | None:
        """(estadísticas o None si Instagram no las dio, cuándo se leyeron), o None si nunca se pidieron."""
        cur = await self.db.execute("SELECT datos, leido FROM insights_post WHERE media_id = ?", (media_id,))
        row = await cur.fetchone()
        return (json.loads(row[0]) if row[0] else None, row[1]) if row else None

    async def guardar_insights_post(self, media_id: str, datos: dict | None, leido: float) -> None:
        await self.db.execute(
            "INSERT INTO insights_post (media_id, datos, leido) VALUES (?, ?, ?)"
            " ON CONFLICT (media_id) DO UPDATE SET datos = excluded.datos, leido = excluded.leido",
            (media_id, json.dumps(datos) if datos is not None else None, leido))
        await self.db.commit()

    # --- chats con Skandi ---

    @staticmethod
    def _skandi(row) -> dict:
        return {"id": row[0], "origen": row[1], "quien": row[2], "usuario": row[3], "creado": row[4],
                "actualizado": row[5], "mensajes": json.loads(row[6]), "soporte": bool(row[7])}

    async def guardar_skandi(self, chat_id: int | None, origen: str, quien: str, usuario: str,
                             mensajes: list, soporte: bool) -> int:
        """Crea el chat (sin `chat_id`) o lo actualiza; `soporte` solo se prende. Devuelve su id."""
        ahora, datos = time.time(), json.dumps(mensajes, ensure_ascii=False)
        if chat_id is None:
            cur = await self.db.execute(
                "INSERT INTO skandi_chats (origen, quien, usuario, creado, actualizado, mensajes, soporte)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", (origen, quien, usuario, ahora, ahora, datos, int(soporte)))
            chat_id = cur.lastrowid
        else:
            await self.db.execute(
                "UPDATE skandi_chats SET actualizado = ?, mensajes = ?, soporte = MAX(soporte, ?), usuario = ?"
                " WHERE id = ?", (ahora, datos, int(soporte), usuario, chat_id))
        await self.db.commit()
        return chat_id

    async def chats_skandi(self, origen: str, quien: str | None = None, soporte: bool = False,
                           desde: int = 0, cuantos: int = 30) -> tuple[list[dict], int]:
        """Del más reciente al más viejo; con `quien`, solo los suyos. También el total."""
        donde, args = ["origen = ?"] if origen else [], [origen] if origen else []
        if quien is not None:
            donde.append("quien = ?")
            args.append(quien)
        if soporte:
            donde.append("soporte = 1")
        filtro = f" WHERE {' AND '.join(donde)}" if donde else ""
        cur = await self.db.execute(f"SELECT COUNT(*) FROM skandi_chats{filtro}", args)
        total = (await cur.fetchone())[0]
        cur = await self.db.execute(
            "SELECT id, origen, quien, usuario, creado, actualizado, mensajes, soporte FROM skandi_chats"
            f"{filtro} ORDER BY actualizado DESC, id DESC LIMIT ? OFFSET ?", [*args, cuantos, desde])
        return [self._skandi(r) for r in await cur.fetchall()], total

    async def chat_skandi(self, chat_id: int) -> dict | None:
        cur = await self.db.execute(
            "SELECT id, origen, quien, usuario, creado, actualizado, mensajes, soporte FROM skandi_chats"
            " WHERE id = ?", (chat_id,))
        row = await cur.fetchone()
        return self._skandi(row) if row else None

    # --- anuncios y ventas ---

    async def guardar_anuncio(self, user_id: str, ad_id: str, titulo: str, foto: str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO anuncios_chat (user_id, ad_id, titulo, foto, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, ad_id, titulo, foto, time.time()))
        await self.db.commit()

    async def anuncios_de(self, user_ids: list[str]) -> dict[str, dict]:
        """El último anuncio por el que llegó cada uno: {user_id: {ad_id, titulo, foto, created_at}}."""
        if not user_ids:
            return {}
        marcas = ",".join("?" * len(user_ids))
        cur = await self.db.execute(
            f"SELECT user_id, ad_id, titulo, foto, created_at FROM anuncios_chat WHERE user_id IN ({marcas})"
            " ORDER BY created_at", user_ids)
        return {u: {"ad_id": a, "titulo": t, "foto": f, "created_at": c} for u, a, t, f, c in await cur.fetchall()}

    async def por_anuncio(self, desde: float, hasta: float) -> dict[str, dict]:
        """Por anuncio, en el periodo: los chats que llegaron de él y hasta dónde llegaron esos chats
        (cotizaciones que anotó el asistente, citas que agendó y no se cancelaron, ventas).
        {ad_id: {"titulo", "chats", "cotizaciones", "citas", "ventas", "monto", "moneda"}}."""
        out: dict[str, dict] = {}

        def fila(ad: str) -> dict:
            return out.setdefault(ad, {"titulo": "", "chats": 0, "cotizaciones": 0, "citas": 0, "ventas": 0,
                                       "monto": 0.0, "moneda": ""})

        cur = await self.db.execute(
            "SELECT ad_id, MAX(titulo), COUNT(DISTINCT user_id) FROM anuncios_chat"
            " WHERE created_at >= ? AND created_at < ? GROUP BY ad_id", (desde, hasta))
        for ad, titulo, chats in await cur.fetchall():
            fila(ad).update(titulo=titulo or "", chats=chats)
        # Personas (no avisos) que pidieron cotización: lo anota el asistente o sale de cotizar un pedido.
        cur = await self.db.execute(
            "SELECT a.ad_id, COUNT(DISTINCT e.user_id) FROM events e JOIN anuncios_chat a ON a.user_id = e.user_id"
            " WHERE e.kind IN ('interes', 'cotizacion') AND e.created_at >= ? AND e.created_at < ?"
            " GROUP BY a.ad_id", (desde, hasta))
        for ad, n in await cur.fetchall():
            fila(ad)["cotizaciones"] = n
        cur = await self.db.execute(
            "SELECT a.ad_id, COUNT(*) FROM events e JOIN anuncios_chat a ON a.user_id = e.user_id"
            " LEFT JOIN meetings m ON m.event_id = json_extract(e.detail, '$.id')"
            " WHERE e.kind = 'cita' AND COALESCE(m.status, 'active') = 'active'"
            " AND e.created_at >= ? AND e.created_at < ? GROUP BY a.ad_id", (desde, hasta))
        for ad, n in await cur.fetchall():
            fila(ad)["citas"] = n
        cur = await self.db.execute(
            "SELECT ad_id, COUNT(*), SUM(monto), MAX(moneda) FROM ventas"
            " WHERE ad_id != '' AND created_at >= ? AND created_at < ? GROUP BY ad_id", (desde, hasta))
        for ad, n, monto, moneda in await cur.fetchall():
            fila(ad).update(ventas=n, monto=float(monto or 0), moneda=moneda or "")
        return out

    async def codigo_chat(self, user_id: str) -> str:
        """El código de este chat para los enlaces a la tienda (se crea la primera vez)."""
        cur = await self.db.execute("SELECT codigo FROM codigos_chat WHERE user_id = ?", (user_id,))
        if row := await cur.fetchone():
            return row[0]
        while True:
            codigo = secrets.token_urlsafe(6).replace("-", "x").replace("_", "z")
            cur = await self.db.execute("INSERT OR IGNORE INTO codigos_chat (codigo, user_id) VALUES (?, ?)",
                                        (codigo, user_id))
            if cur.rowcount:
                await self.db.commit()
                return codigo

    async def usuario_de_codigo(self, codigo: str) -> str:
        cur = await self.db.execute("SELECT user_id FROM codigos_chat WHERE codigo = ?", (codigo,))
        row = await cur.fetchone()
        return row[0] if row else ""

    async def add_venta(self, origen: str, externo: str, user_id: str, ad_id: str, monto: float, moneda: str,
                        detalle: dict, created_at: float) -> bool:
        """False si ya estaba (el mismo pedido no se cuenta dos veces)."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO ventas (origen, externo, user_id, ad_id, monto, moneda, detalle, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (origen, externo, user_id, ad_id, monto, moneda, json.dumps(detalle, ensure_ascii=False), created_at))
        await self.db.commit()
        return bool(cur.rowcount)

    async def borrar_venta(self, origen: str, externo: str) -> None:
        await self.db.execute("DELETE FROM ventas WHERE origen = ? AND externo = ?", (origen, externo))
        await self.db.commit()

    async def ventas_de(self, user_id: str) -> list[dict]:
        cur = await self.db.execute(
            "SELECT origen, externo, ad_id, monto, moneda, detalle, created_at FROM ventas WHERE user_id = ?"
            " ORDER BY created_at", (user_id,))
        return [{"origen": o, "externo": e, "ad_id": a, "monto": m, "moneda": mo, "detalle": json.loads(d),
                 "created_at": t} for o, e, a, m, mo, d, t in await cur.fetchall()]

    async def ventas_desde(self, since: float) -> tuple[int, float, str] | None:
        """Ventas desde `since`: (cuántas, monto, moneda más usada). None si la cuenta nunca ha tenido una."""
        cur = await self.db.execute("SELECT 1 FROM ventas LIMIT 1")
        if await cur.fetchone() is None:
            return None
        cur = await self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(monto), 0) FROM ventas WHERE created_at >= ?", (since,))
        n, monto = await cur.fetchone()
        cur = await self.db.execute("SELECT moneda FROM ventas GROUP BY moneda ORDER BY COUNT(*) DESC LIMIT 1")
        return n, monto, (await cur.fetchone())[0] or ""

    async def escribieron_desde(self, since: float) -> int:
        """Cuántas personas distintas escribieron desde `since`."""
        cur = await self.db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM messages WHERE role = 'user' AND user_id NOT LIKE 'admin:%'"
            " AND created_at >= ?", (since,))
        return (await cur.fetchone())[0]

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
