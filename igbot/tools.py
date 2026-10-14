"""Herramientas del agente (servidor MCP dentro del proceso).

Cada conversación arma sus propias herramientas atadas a UN usuario de
Instagram: no pueden leer ni tocar datos de otra persona. Las reglas de
negocio (horario, límites, a quién se puede escribir) se validan aquí en
código, no en el prompt, para que nadie pueda saltárselas con un DM.
"""
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import config
from .google import Google
from .store import Store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def tz() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def now() -> datetime:
    return datetime.now(tz())


def fmt(dt: datetime) -> str:
    dt = dt.astimezone(tz())
    return f"{DAYS[dt.weekday()]} {dt:%d/%m} a las {dt:%H:%M}"


def business_window(day: date) -> tuple[datetime, datetime] | None:
    """Inicio y fin del horario de atención de ese día, o None si no se atiende."""
    if day.isoweekday() not in config.BUSINESS_DAYS:
        return None
    start_s, end_s = config.BUSINESS_HOURS.split("-")
    start = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time(), tz())
    end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time(), tz())
    return start, end


def overlaps(start: datetime, end: datetime, busy: list[tuple[str, str]]) -> bool:
    return any(
        start < datetime.fromisoformat(b_end) and datetime.fromisoformat(b_start) < end
        for b_start, b_end in busy
    )


def free_slots(day: date, busy: list[tuple[str, str]], current: datetime) -> list[datetime]:
    window = business_window(day)
    if not window:
        return []
    step = timedelta(minutes=config.MEETING_MINUTES)
    earliest = current + timedelta(hours=config.MIN_NOTICE_HOURS)
    slots, t = [], window[0]
    while t + step <= window[1]:
        if t >= earliest and not overlaps(t, t + step, busy):
            slots.append(t)
        t += step
    return slots


def parse_day(text: str) -> date:
    return date.fromisoformat(text.strip())


def memory_tools(store: Store, user_id: str) -> list:
    @tool(
        "recordar",
        "Guarda un dato duradero sobre esta persona (nombre, correo, interés, pedido, preferencia)"
        " para recordarlo en futuras conversaciones. Usa claves cortas en minúsculas.",
        {"clave": str, "valor": str},
    )
    async def recordar(args):
        await store.set_fact(user_id, args["clave"][:50], args["valor"][:500])
        return ok("Guardado.")

    @tool("olvidar", "Borra un dato guardado de esta persona.", {"clave": str})
    async def olvidar(args):
        await store.delete_fact(user_id, args["clave"])
        return ok("Borrado.")

    return [recordar, olvidar]


def workspace_tools(store: Store, google: Google, user_id: str) -> list:
    async def check_email(email: str) -> str | None:
        """Solo se puede escribir/invitar a un correo que la persona dio en el chat."""
        if not EMAIL_RE.match(email):
            return "Ese correo no parece válido."
        if not await store.user_wrote(user_id, email):
            return (
                "Ese correo no lo escribió la persona en el chat. Pídele que te escriba"
                " su correo en un mensaje antes de usarlo."
            )
        return None

    @tool(
        "ver_disponibilidad",
        "Muestra los horarios libres para una reunión en una fecha (formato AAAA-MM-DD).",
        {"fecha": str},
    )
    async def ver_disponibilidad(args):
        try:
            day = parse_day(args["fecha"])
        except ValueError:
            return error("Fecha inválida; usa AAAA-MM-DD.")
        window = business_window(day)
        if not window:
            return ok(f"El {DAYS[day.weekday()]} {day:%d/%m} no hay atención.")
        busy = await google.busy(window[0].isoformat(), window[1].isoformat())
        slots = free_slots(day, busy, now())
        if not slots:
            return ok(f"No quedan horarios libres el {DAYS[day.weekday()]} {day:%d/%m}.")
        horas = ", ".join(f"{s:%H:%M}" for s in slots)
        return ok(
            f"Libres el {DAYS[day.weekday()]} {day:%d/%m} ({config.MEETING_MINUTES} min,"
            f" hora de {config.TIMEZONE}): {horas}"
        )

    @tool(
        "agendar_reunion",
        "Agenda una videollamada (Google Meet) e invita a la persona por correo. Antes confirma"
        " con ella fecha, hora y correo. fecha: AAAA-MM-DD, hora: HH:MM.",
        {"fecha": str, "hora": str, "email": str, "nombre": str, "motivo": str},
    )
    async def agendar_reunion(args):
        email = args["email"].strip()
        if problem := await check_email(email):
            return error(problem)
        try:
            day = parse_day(args["fecha"])
            start = datetime.combine(day, datetime.strptime(args["hora"].strip(), "%H:%M").time(), tz())
        except ValueError:
            return error("Fecha u hora inválida; usa AAAA-MM-DD y HH:MM.")
        end = start + timedelta(minutes=config.MEETING_MINUTES)
        window = business_window(day)
        if not window or start < window[0] or end > window[1]:
            return error(f"Fuera del horario de atención ({config.BUSINESS_HOURS}).")
        if start < now() + timedelta(hours=config.MIN_NOTICE_HOURS):
            return error(f"Se necesitan al menos {config.MIN_NOTICE_HOURS:g} horas de anticipación.")
        upcoming = await store.meetings(user_id, now().isoformat())
        if len(upcoming) >= config.MAX_MEETINGS_PER_USER:
            return error(
                f"Esta persona ya tiene {len(upcoming)} reuniones agendadas (máximo"
                f" {config.MAX_MEETINGS_PER_USER}). Puede cancelar una antes."
            )
        if overlaps(start, end, await google.busy(start.isoformat(), end.isoformat())):
            return error("Ese horario ya está ocupado. Revisa la disponibilidad otra vez.")
        nombre = args["nombre"].strip()[:80] or "Cliente"
        event_id, link = await google.create_meeting(
            start.isoformat(), end.isoformat(), config.TIMEZONE, email,
            summary=f"Reunión con {nombre}",
            description=f"Agendada por el asistente de Instagram.\nMotivo: {args['motivo'][:500]}",
        )
        await store.add_meeting(event_id, user_id, start.isoformat(), email, link)
        return ok(
            f"Agendada para el {fmt(start)} (hora de {config.TIMEZONE}). Invitación enviada a"
            f" {email}. Link de Meet: {link or 'viene en la invitación'}. id: {event_id}"
        )

    @tool("mis_reuniones", "Lista las próximas reuniones agendadas de esta persona.", {})
    async def mis_reuniones(args):
        rows = await store.meetings(user_id, now().isoformat())
        if not rows:
            return ok("No tiene reuniones agendadas.")
        return ok("\n".join(
            f"- {fmt(datetime.fromisoformat(start))} · {email} · {link} · id: {event_id}"
            for event_id, start, email, link in rows
        ))

    @tool(
        "cancelar_reunion",
        "Cancela una reunión de esta persona (usa el id que da mis_reuniones) y le avisa por correo.",
        {"id": str},
    )
    async def cancelar_reunion(args):
        rows = await store.meetings(user_id, now().isoformat())
        if args["id"] not in {r[0] for r in rows}:
            return error("No encontré esa reunión entre las de esta persona.")
        await google.cancel_meeting(args["id"])
        await store.cancel_meeting(args["id"])
        return ok("Reunión cancelada; la persona recibe el aviso por correo.")

    @tool(
        "enviar_correo",
        "Envía un correo a esta persona desde el correo del dueño. Úsalo para mandarle información que pidió. Solo al correo que"
        " ella escribió en el chat. Texto plano, sin markdown.",
        {"email": str, "asunto": str, "cuerpo": str},
    )
    async def enviar_correo(args):
        email = args["email"].strip()
        if problem := await check_email(email):
            return error(problem)
        sent = await store.emails_since(user_id, time.time() - 86400)
        if sent >= config.MAX_EMAILS_PER_USER_PER_DAY:
            return error("Ya se le enviaron los correos permitidos hoy. Ofrece seguir por aquí.")
        subject = args["asunto"].strip()[:150]
        await google.send_email(
            config.MAIL_FROM, config.MAIL_FROM_NAME, email, config.MAIL_CC,
            subject, args["cuerpo"][:5000],
        )
        await store.log_email(user_id, email, subject)
        return ok(f"Correo enviado a {email}.")

    return [ver_disponibilidad, agendar_reunion, mis_reuniones, cancelar_reunion, enviar_correo]


def build(store: Store, google: Google | None, user_id: str):
    """Servidor MCP con las herramientas de este usuario y la lista de permitidas."""
    tools = memory_tools(store, user_id)
    if google:
        tools += workspace_tools(store, google, user_id)
    server = create_sdk_mcp_server("bot", tools=tools)
    return server, [f"mcp__bot__{t.name}" for t in tools]
