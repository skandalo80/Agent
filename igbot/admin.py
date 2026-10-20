"""Administradores: el dueño le escribe al bot desde su Instagram personal.

Los ids de ADMIN_IG_IDS no hablan con el vendedor, sino con un asistente
interno que puede:
- publicar en la cuenta: le mandan una o varias fotos y el texto, el bot
  manda una vista previa y publica (ahora o a la hora que digan) solo después
  de que el administrador confirme en otro mensaje;
- reactivar conversaciones que están en pausa porque las atendía una persona;
- mandar el reporte semanal cuando lo pidan.

Con "modo cliente" el administrador habla con el bot como cualquier cliente
(para probarlo) y con "modo admin" vuelve. Lo que escribe en modo admin se
guarda aparte (usuario "admin:<id>") para no mezclarse con sus pruebas.
"""
import asyncio
import io
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config
from .cuenta import cuenta
from .google import Google
from .instagram import Instagram
from .store import Store
from .tools import error, fmt, ok, tz

log = logging.getLogger("igbot.admin")

PREFIX = "admin:"
MODES = {"modo cliente": "cliente", "modo admin": "admin"}


def is_admin(user_id: str) -> bool:
    return user_id in cuenta().admin_ig_ids


def key(user_id: str) -> str:
    return PREFIX + user_id


def is_key(user_id: str) -> bool:
    return user_id.startswith(PREFIX)


def real_id(user_id: str) -> str:
    return user_id.removeprefix(PREFIX)


def command(text: str) -> str | None:
    """'modo cliente' / 'modo admin' (sin importar mayúsculas ni puntuación)."""
    return MODES.get(" ".join(re.sub(r"[^\w ]", " ", (text or "").lower()).split()))


async def mode(store: Store, user_id: str) -> str:
    return await store.get(f"admin_mode:{user_id}") or "admin"


async def set_mode(store: Store, instagram: Instagram, user_id: str, new_mode: str) -> None:
    await store.set(f"admin_mode:{user_id}", new_mode)
    text = (
        "Modo cliente: te contesto como a cualquier cliente. Escribe \"modo admin\" para volver."
        if new_mode == "cliente"
        else "Modo admin: puedes mandarme fotos para publicar o pedirme las conversaciones en pausa."
    )
    try:
        await instagram.send_text(user_id, text)
    except Exception:
        log.exception("No se pudo confirmar el cambio de modo a %s", user_id)


# --- fotos ---

TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
SLUG = re.compile(r"^[a-z0-9_-]{1,40}$")
# Instagram solo acepta fotos de feed entre 4:5 (vertical) y 1.91:1 (horizontal).
MIN_RATIO, MAX_RATIO = 4 / 5, 1.91
MAX_WIDTH = 1440


def fit_image(data: bytes) -> tuple[bytes, str]:
    """La foto como JPG que Instagram acepta: recortada al centro si hace falta."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    w, h = img.size
    note = ""
    if w / h < MIN_RATIO:
        new_h = round(w / MIN_RATIO)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
        note = "recortada a 4:5 (Instagram no acepta fotos más verticales)"
    elif w / h > MAX_RATIO:
        new_w = round(h * MAX_RATIO)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
        note = "recortada a 1.91:1 (Instagram no acepta fotos más horizontales)"
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue(), note


def media_path(slug: str, token: str) -> Path | None:
    """Archivo de una foto de cualquier cuenta (todas comparten la carpeta data/)."""
    if not (SLUG.match(slug) and TOKEN.match(token)):
        return None
    base = Path("data/media") if slug == "principal" else Path("data") / slug / "media"
    return base / f"{token}.jpg"


def public_url(token: str) -> str:
    return f"{config.PUBLIC_URL}/media/{cuenta().media_slug}/{token}.jpg"


async def save_photo(store: Store, instagram: Instagram, media_id: int) -> None:
    token, url, _ = await store.media(media_id)
    path = Path(cuenta().media_dir) / f"{token}.jpg"
    if path.exists():
        return
    jpg, note = fit_image(await instagram.download(url))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpg)
    if note:
        await store.set_media_note(media_id, note)


async def save_photo_quietly(store: Store, instagram: Instagram, media_id: int) -> None:
    try:
        await save_photo(store, instagram, media_id)
    except Exception:
        log.exception("No se pudo guardar la foto %s", media_id)


async def incoming_text(store: Store, user_id: str, message: dict) -> tuple[str, list[int]]:
    """Texto de un DM del administrador: cada foto queda guardada como "[foto N]"."""
    parts, photos = [], []
    for a in message.get("attachments", []):
        url = (a.get("payload") or {}).get("url")
        if a.get("type") == "image" and url:
            media_id = await store.add_media(user_id, secrets.token_urlsafe(24), url)
            photos.append(media_id)
            parts.append(f"[foto {media_id}]")
        else:
            parts.append(f"[envió: {a.get('type', 'archivo')}; por ahora solo se pueden publicar fotos]")
    if message.get("text"):
        parts.append(message["text"])
    return " ".join(parts) or "[mensaje vacío]", photos


# --- publicar ---


def parse_when(text: str) -> float:
    """0 para "ahora"; si no, 'AAAA-MM-DD HH:MM' en la hora del negocio."""
    text = text.strip().lower()
    if text in ("", "ahora", "ya"):
        return 0
    try:
        when = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=tz())
    except ValueError:
        raise ValueError("cuando debe ser 'ahora' o 'AAAA-MM-DD HH:MM'.") from None
    if when.timestamp() < time.time() - 60:
        raise ValueError(f"Esa hora ya pasó ({fmt(when)}).")
    if when.timestamp() > time.time() + 75 * 86400:
        raise ValueError("Solo se puede programar hasta 75 días adelante.")
    return when.timestamp()


async def publish_post(store: Store, instagram: Instagram, post: dict) -> tuple[str, str]:
    """Publica un borrador o una programada. Devuelve (link, error)."""
    if not await store.set_post(post["id"], "publicando", only_if=post["status"]):
        return "", "ya se estaba publicando"
    try:
        urls = []
        for media_id in post["media_ids"]:
            await save_photo(store, instagram, media_id)
            urls.append(public_url((await store.media(media_id))[0]))
        link = await instagram.publish(urls, post["caption"])
    except Exception as e:
        log.exception("No se pudo publicar la publicación %s", post["id"])
        await store.set_post(post["id"], "fallida", error=str(e)[:500])
        return "", str(e)
    await store.set_post(post["id"], "publicada", permalink=link)
    log.info("Publicada la publicación %s: %s", post["id"], link)
    return link, ""


async def publish_due(store: Store, instagram: Instagram) -> None:
    """Publica las programadas a las que ya les tocó y le avisa a quien las pidió."""
    for post in await store.posts(("programada",), due_before=time.time()):
        link, err = await publish_post(store, instagram, post)
        text = (
            f"Ya salió la publicación #{post['id']}: {link}" if not err
            else f"No se pudo publicar la #{post['id']} que estaba programada: {err}"
        )
        try:
            await instagram.send_text(post["user_id"], text)
            await store.add_bot_message(key(post["user_id"]), text)
        except Exception:
            log.exception("No se pudo avisar de la publicación %s", post["id"])


async def publisher_loop(store: Store, instagram: Instagram, every: float = 30) -> None:
    while True:
        try:
            await publish_due(store, instagram)
        except Exception:
            log.exception("Falló la revisión de publicaciones programadas")
        await asyncio.sleep(every)


def describe(post: dict) -> str:
    when = fmt(datetime.fromtimestamp(post["publish_at"], tz())) if post["publish_at"] else "en cuanto se confirme"
    fotos = ", ".join(str(i) for i in post["media_ids"])
    return f"#{post['id']} ({post['status']}): fotos {fotos}; {when}; texto: \"{post['caption'][:80]}\""


PUBLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "fotos": {"type": "array", "items": {"type": "integer"}, "description": "Números de las fotos, en orden."},
        "texto": {"type": "string", "description": "El texto de la publicación, tal cual se publicará."},
        "cuando": {"type": "string", "description": "'ahora' o 'AAAA-MM-DD HH:MM' en la hora del negocio."},
    },
    "required": ["fotos", "texto", "cuando"],
}


def admin_tools(store: Store, instagram: Instagram, admin_id: str, state: dict,
                google: Google | None = None) -> list:
    turn_started = state.setdefault("turn_started", time.time())

    @tool("ver_pausas", "Lista las conversaciones donde el bot está en pausa porque las atiende una persona.", {})
    async def ver_pausas(args):
        rows = await store.paused_list(time.time())
        if not rows:
            return ok("No hay conversaciones en pausa.")
        lines = []
        for user_id, until, last in rows:
            name = await instagram.username(user_id)
            lines.append(
                f"- {name} (id {user_id}): {(until - time.time()) / 3600:.1f} h más de pausa."
                f" Su último mensaje: \"{last[:100]}\""
            )
        return ok("\n".join(lines))

    @tool(
        "reactivar_conversacion",
        "Quita la pausa de una conversación para que el bot vuelva a contestar."
        " usuario: el @usuario o el id que da ver_pausas.",
        {"usuario": str},
    )
    async def reactivar_conversacion(args):
        wanted = args["usuario"].strip().lstrip("@").lower()
        for user_id, *_ in await store.paused_list(time.time()):
            name = await instagram.username(user_id)
            if wanted in (user_id, name.lstrip("@").lower()):
                await store.unpause(user_id)
                state["acted"] = True
                log.info("El administrador reactivó la conversación con %s", user_id)
                return ok(f"Listo: el bot le vuelve a contestar a {name} desde su próximo mensaje.")
        return error("Esa conversación no está en pausa; revisa con ver_pausas.")

    @tool(
        "preparar_publicacion",
        "Arma un borrador de publicación con fotos que mandó el administrador ([foto N]) y le manda"
        " la vista previa. No publica nada: después hay que mostrarle el texto y esperar a que"
        " confirme. Varias fotos = carrusel (máximo 10).",
        PUBLISH_SCHEMA,
    )
    async def preparar_publicacion(args):
        fotos = [int(f) for f in args.get("fotos") or []]
        texto = (args.get("texto") or "").strip()
        if not 1 <= len(fotos) <= 10 or len(set(fotos)) != len(fotos):
            return error("Una publicación lleva de 1 a 10 fotos distintas.")
        if len(texto) > 2200 or texto.count("#") > 30:
            return error("Instagram acepta hasta 2200 caracteres y 30 hashtags.")
        if not config.PUBLIC_URL:
            return error("Falta PUBLIC_URL en la configuración del servidor; no se puede publicar.")
        try:
            when = parse_when(args.get("cuando", "ahora"))
        except ValueError as e:
            return error(str(e))
        notes = []
        for n in fotos:
            row = await store.media(n)
            if not row:
                return error(f"No existe la foto {n}.")
            try:
                await save_photo(store, instagram, n)
            except Exception:
                log.exception("No se pudo descargar la foto %s", n)
                return error(f"No pude descargar la foto {n}; pídele que la mande otra vez.")
            if note := (await store.media(n))[2]:
                notes.append(f"foto {n} {note}")
        post_id = await store.add_post(admin_id, fotos, texto, when)
        state["acted"] = True
        for n in fotos:
            try:
                await instagram.send_image(admin_id, public_url((await store.media(n))[0]))
            except Exception:
                log.exception("No se pudo mandar la vista previa de la foto %s", n)
        post = await store.post(post_id)
        return ok(
            f"Borrador {describe(post)}. Ya le llegaron las fotos como vista previa."
            + (f" Aviso: {'; '.join(notes)}." if notes else "")
            + f" Muéstrale el texto completo y cuándo sale, y pídele que confirme. Cuando diga que sí"
            f" (en otro mensaje), usa confirmar_publicacion con el número {post_id}."
        )

    @tool(
        "confirmar_publicacion",
        "Publica (o deja programado) un borrador que el administrador ya vio y aprobó.",
        {"numero": int},
    )
    async def confirmar_publicacion(args):
        post = await store.post(int(args["numero"]))
        if not post:
            return error("No existe esa publicación.")
        if post["status"] != "borrador":
            return error(f"La publicación #{post['id']} ya está {post['status']}.")
        if post["created_at"] >= turn_started:
            return error("Primero muéstrale la vista previa y espera a que la confirme en otro mensaje.")
        state["acted"] = True
        if post["publish_at"] > time.time() + 60:
            await store.set_post(post["id"], "programada", only_if="borrador")
            return ok(f"Programada: {describe(await store.post(post['id']))}. Le avisaré por aquí cuando salga.")
        link, err = await publish_post(store, instagram, post)
        if err:
            return error(f"No se pudo publicar: {err}")
        return ok(f"Publicada: {link or 'listo (Instagram no devolvió el link)'}")

    @tool("ver_publicaciones", "Lista las publicaciones programadas y los borradores sin confirmar.", {})
    async def ver_publicaciones(args):
        posts = await store.posts(("borrador", "programada"))
        return ok("\n".join(map(describe, posts)) or "No hay publicaciones pendientes.")

    @tool("cancelar_publicacion", "Cancela un borrador o una publicación programada.", {"numero": int})
    async def cancelar_publicacion(args):
        post = await store.post(int(args["numero"]))
        if not post or post["status"] not in ("borrador", "programada"):
            return error("Esa publicación no está pendiente; revisa con ver_publicaciones.")
        await store.set_post(post["id"], "cancelada", only_if=post["status"])
        state["acted"] = True
        return ok(f"Cancelada la publicación #{post['id']}.")

    @tool(
        "enviar_reporte",
        "Manda ahora el reporte de los últimos 7 días (conversaciones, cotizaciones, comentarios,"
        " publicaciones y lo que más preguntan) por DM y, si está configurado, por correo.",
        {},
    )
    async def enviar_reporte(args):
        from . import reporte  # import tardío: reporte importa tools

        state["acted"] = True
        where = await reporte.send(store, instagram, google, admins=[admin_id])
        if not where:
            return error("No se pudo mandar el reporte; revisa el log del servidor.")
        return ok(f"Reporte enviado ({where}). Ya le llegó: no lo repitas, solo confirma en una línea.")

    return [
        ver_pausas, reactivar_conversacion, preparar_publicacion,
        confirmar_publicacion, ver_publicaciones, cancelar_publicacion, enviar_reporte,
    ]


SYSTEM_PATH = Path("prompts/admin.md")


async def reply(
    store: Store, google: Google | None, user_key: str, new_messages: list[str], instagram: Instagram
) -> tuple[str, bool]:
    """Igual que agent.reply, pero para un administrador y con sus herramientas."""
    from .agent import build_prompt, run_claude  # import tardío: evita el ciclo con agent

    state: dict = {}
    tools = admin_tools(store, instagram, real_id(user_key), state, google)
    server = create_sdk_mcp_server("admin", tools=tools)
    allowed = [f"mcp__admin__{t.name}" for t in tools]
    prompt = await build_prompt(store, user_key, new_messages)
    system = SYSTEM_PATH.read_text(encoding="utf-8")
    text = await run_claude(prompt, system, {"admin": server}, allowed, max_turns=10)
    return text, state.get("acted", False)
