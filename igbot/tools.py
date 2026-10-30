"""Herramientas del agente (servidor MCP dentro del proceso).

Cada conversación arma sus propias herramientas atadas a UN usuario de
Instagram: no pueden leer ni tocar datos de otra persona. Las reglas de
negocio (horario, límites, a quién se puede escribir) se validan aquí en
código, no en el prompt, para que nadie pueda saltárselas con un DM.
"""
import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import catalog, config, conversiones_meta, tienda
from .cuenta import cuenta
from .google import Google
from .instagram import Instagram
from .store import Store

log = logging.getLogger("igbot.tools")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def tz() -> ZoneInfo:
    return ZoneInfo(cuenta().timezone)


def now() -> datetime:
    return datetime.now(tz())


def fmt(dt: datetime) -> str:
    dt = dt.astimezone(tz())
    return f"{DAYS[dt.weekday()]} {dt:%d/%m} a las {dt:%H:%M}"


def business_window(day: date) -> tuple[datetime, datetime] | None:
    """Inicio y fin del horario de atención de ese día, o None si no se atiende."""
    if day.isoweekday() not in cuenta().business_days:
        return None
    start_s, end_s = cuenta().business_hours.split("-")
    start = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time(), tz())
    end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time(), tz())
    return start, end


def overlaps(start: datetime, end: datetime, busy: list[tuple[str, str]]) -> bool:
    return any(
        start < datetime.fromisoformat(b_end) and datetime.fromisoformat(b_start) < end
        for b_start, b_end in busy
    )


def free_slots(day: date, busy: list[tuple[str, str]], current: datetime, minutos: int = 0) -> list[datetime]:
    window = business_window(day)
    if not window:
        return []
    step = timedelta(minutes=minutos or cuenta().meeting_minutes)
    earliest = current + timedelta(hours=cuenta().min_notice_hours)
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


async def check_client_email(store: Store, user_id: str, email: str) -> str | None:
    """Solo se puede escribir/invitar a un correo que la persona dio en el chat."""
    if not EMAIL_RE.match(email):
        return "Ese correo no parece válido."
    if not await store.user_wrote(user_id, email):
        return (
            "Ese correo no lo escribió la persona en el chat. Pídele que te escriba"
            " su correo en un mensaje antes de usarlo."
        )
    return None


def workspace_tools(
    store: Store, google: Google, user_id: str, state: dict | None = None, correo: bool = True,
    presencial: bool = False, minutos: int = 0,
) -> list:
    """Agenda (y correo, con `correo`) en el Google que se pase: el tuyo de .env o el calendario que
    conectó la cuenta (agenda_google.py), que no manda correos. `presencial`: citas en el negocio,
    sin Meet y con el correo opcional. `minutos`: lo que dura cada cita (0: el de .env)."""
    minutos = minutos or cuenta().meeting_minutes
    # Se marca cuando una herramienta hace algo fuera del chat (agendar,
    # cancelar, enviar correo): esa respuesta ya no se puede descartar.
    state = state if state is not None else {}

    async def check_email(email: str) -> str | None:
        return await check_client_email(store, user_id, email)

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
        slots = free_slots(day, busy, now(), minutos)
        if not slots:
            return ok(f"No quedan horarios libres el {DAYS[day.weekday()]} {day:%d/%m}.")
        horas = ", ".join(f"{s:%H:%M}" for s in slots)
        return ok(
            f"Libres el {DAYS[day.weekday()]} {day:%d/%m} ({minutos} min,"
            f" hora de {cuenta().timezone}): {horas}"
        )

    @tool(
        "agendar_reunion",
        "Agenda una cita en el negocio. Antes confirma con la persona fecha, hora y su nombre; si da su correo,"
        " le llega la invitación (si no, deja email vacío). fecha: AAAA-MM-DD, hora: HH:MM." if presencial else
        "Agenda una videollamada (Google Meet) e invita a la persona por correo. Antes confirma"
        " con ella fecha, hora y correo. fecha: AAAA-MM-DD, hora: HH:MM.",
        {"fecha": str, "hora": str, "email": str, "nombre": str, "motivo": str},
    )
    async def agendar_reunion(args):
        email = str(args.get("email") or "").strip()
        if (email or not presencial) and (problem := await check_email(email)):
            return error(problem)
        try:
            day = parse_day(args["fecha"])
            start = datetime.combine(day, datetime.strptime(args["hora"].strip(), "%H:%M").time(), tz())
        except ValueError:
            return error("Fecha u hora inválida; usa AAAA-MM-DD y HH:MM.")
        end = start + timedelta(minutes=minutos)
        window = business_window(day)
        if not window or start < window[0] or end > window[1]:
            return error(f"Fuera del horario de atención ({cuenta().business_hours}).")
        if start < now() + timedelta(hours=cuenta().min_notice_hours):
            return error(f"Se necesitan al menos {cuenta().min_notice_hours:g} horas de anticipación.")
        upcoming = await store.meetings(user_id, now().isoformat())
        if len(upcoming) >= cuenta().max_meetings_per_user:
            return error(
                f"Esta persona ya tiene {len(upcoming)} reuniones agendadas (máximo"
                f" {cuenta().max_meetings_per_user}). Puede cancelar una antes."
            )
        if overlaps(start, end, await google.busy(start.isoformat(), end.isoformat())):
            return error("Ese horario ya está ocupado. Revisa la disponibilidad otra vez.")
        nombre = args["nombre"].strip()[:80] or "Cliente"
        event_id, link = await google.create_meeting(
            start.isoformat(), end.isoformat(), cuenta().timezone, email,
            summary=f"{'Cita' if presencial else 'Reunión'} con {nombre}",
            description=f"Agendada por el asistente de Instagram.\nMotivo: {str(args.get('motivo') or '')[:500]}",
            meet=not presencial,
        )
        await store.add_meeting(event_id, user_id, start.isoformat(), email, link)
        await store.add_event(user_id, "cita", {"id": event_id, "inicio": start.isoformat()})  # para Resultados > Anuncios
        conversiones_meta.avisar(cuenta(), user_id, "cita")
        state["acted"] = True
        if presencial:
            return ok(f"Cita agendada para el {fmt(start)} (hora de {cuenta().timezone})."
                      + (f" Invitación enviada a {email}." if email else "") + f" id: {event_id}")
        return ok(
            f"Agendada para el {fmt(start)} (hora de {cuenta().timezone}). Invitación enviada a"
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
        state["acted"] = True
        con_correo = any(r[0] == args["id"] and r[2] for r in rows)
        return ok("Cancelada." + (" La persona recibe el aviso por correo." if con_correo else ""))

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
        if sent >= cuenta().max_emails_per_user_per_day:
            return error("Ya se le enviaron los correos permitidos hoy. Ofrece seguir por aquí.")
        subject = args["asunto"].strip()[:150]
        await google.send_email(
            config.MAIL_FROM, cuenta().mail_from_name, email, cuenta().mail_cc,
            subject, args["cuerpo"][:5000],
        )
        await store.log_email(user_id, email, subject)
        state["acted"] = True
        return ok(f"Correo enviado a {email}.")

    return [ver_disponibilidad, agendar_reunion, mis_reuniones, cancelar_reunion] + ([enviar_correo] if correo else [])


QUOTE_ARGS = {"paquete": str, "pago": str, "bloques_extra": int}


def quote_url(token: str) -> str:
    """Link público del PDF; lo sirve cualquier proceso (todas las cuentas comparten data/)."""
    slug = f"{cuenta().slug}/" if cuenta().slug else ""
    return f"{config.PUBLIC_URL}/cotizaciones/{slug}{token}.pdf"


CANALES = ("chat", "correo", "ambos")
CANAL_DOC = (
    "canal: por dónde la quiere la persona: 'chat' (solo por este DM), 'correo' o 'ambos'."
    " email: su correo, o cadena vacía si la quiere solo por este chat."
)


async def check_quote_delivery(
    store: Store, instagram: Instagram | None, user_id: str, canal: str, email: str
) -> str | None:
    """Revisa que se pueda entregar la cotización por el canal que eligió la persona."""
    if canal not in CANALES:
        return "canal debe ser 'chat', 'correo' o 'ambos'. Pregúntale si la quiere por aquí o por correo."
    if canal != "correo" and not (instagram and config.PUBLIC_URL):
        return "Ahora no se puede mandar el PDF por este chat. Ofrece mandárselo por correo."
    if canal != "chat":
        if not email:
            return "Para mandarla por correo pídele su correo; si prefiere no darlo, mándala solo por este chat."
        if problem := await check_client_email(store, user_id, email):
            return problem
        if await store.emails_since(user_id, time.time() - 86400) >= cuenta().max_emails_per_user_per_day:
            return "Ya se le enviaron los correos permitidos hoy. Ofrece mandarla por este chat."
    return None


async def deliver_quote(
    store: Store, google: Google, instagram: Instagram | None, user_id: str, canal: str, email: str,
    folio: str, token: str, data: bytes, subject: str, body: str, sender: str,
) -> str:
    """Manda el PDF por correo y/o por este DM. Devuelve por dónde llegó ('' si no llegó)."""
    sent = []
    if canal != "chat":
        try:
            await google.send_email(
                config.MAIL_FROM, sender, email, cuenta().mail_cc, subject, body,
                attachments=[(f"Cotizacion-{folio}.pdf", data)],
            )
            await store.log_email(user_id, email, subject)
            sent.append(f"por correo a {email}")
        except Exception:
            log.exception("No se pudo mandar la cotización %s por correo", folio)
    if canal != "correo":
        try:
            await instagram.send_file(user_id, quote_url(token))
            sent.append("por este chat")
        except Exception:
            log.exception("No se pudo mandar el PDF por DM a %s", user_id)
    return " y ".join(sent)


def quote_tools(
    store: Store, google: Google, instagram: Instagram | None, user_id: str, state: dict | None = None
) -> list:
    state = state if state is not None else {}

    @tool(
        "cotizar",
        "Calcula el precio exacto de un paquete del catálogo para mostrárselo a la persona. paquete:"
        " la clave del catálogo; pago: 'mensual' o 'anual'; bloques_extra: bloques de respuestas"
        " extra (0 si no pidió más). Usa SIEMPRE esto para dar precios; nunca hagas cuentas tú.",
        QUOTE_ARGS,
    )
    async def cotizar(args):
        try:
            q = catalog.quote(args["paquete"], args["pago"], int(args["bloques_extra"]))
        except ValueError as e:
            return error(str(e))
        return ok(q.texto())

    @tool(
        "enviar_cotizacion",
        "Genera la cotización formal en PDF (con folio) y se la manda a la persona por este DM,"
        " por correo (con copia al dueño) o por ambos, según lo que ella eligió. Antes confirma"
        " con ella el paquete y la forma de pago. " + CANAL_DOC + " nombre y empresa son"
        " opcionales (cadena vacía si no los dio).",
        {**QUOTE_ARGS, "canal": str, "email": str, "nombre": str, "empresa": str},
    )
    async def enviar_cotizacion(args):
        canal, email = args.get("canal", "").strip().lower(), args.get("email", "").strip()
        if problem := await check_quote_delivery(store, instagram, user_id, canal, email):
            return error(problem)
        try:
            q = catalog.quote(args["paquete"], args["pago"], int(args["bloques_extra"]))
        except ValueError as e:
            return error(str(e))

        nombre, empresa = args["nombre"].strip()[:80], args["empresa"].strip()[:80]
        token = secrets.token_urlsafe(16)
        number = await store.add_quote(user_id, token, q.paquete, q.pago, q.bloques_extra, q.primer_pago, email)
        folio = f"COT-{number:04d}"
        data = catalog.pdf(q, folio, nombre, empresa, email, now().date())
        folder = Path(cuenta().quotes_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{token}.pdf").write_bytes(data)

        neg = catalog.load()["negocio"]
        subject = f"Cotización {folio} · {neg['nombre']}"
        body = (
            f"Hola{' ' + nombre if nombre else ''}:\n\n"
            f"Te comparto la cotización {folio} que platicamos por Instagram (va en el PDF adjunto).\n\n"
            f"{q.texto()}\n\n"
            f"Es válida por {catalog.load()['vigencia_dias']} días. Si tienes dudas, contesta este"
            f" correo o escríbenos a {neg['instagram']}.\n\nSaludos,\n{neg['nombre']}"
        )
        sent = await deliver_quote(
            store, google, instagram, user_id, canal, email, folio, token, data, subject, body,
            cuenta().mail_from_name,
        )
        if not sent:
            return error("No se pudo mandar la cotización en este momento. Ofrece intentarlo en un rato.")
        state["acted"] = True
        return ok(f"Cotización {folio} enviada {sent}.\n{q.texto()}")

    return [cotizar, enviar_cotizacion]


ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "Una línea por producto y talla.",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "talla": {"type": "string"},
                    "cantidad": {"type": "integer"},
                    "personalizacion": {"type": "string", "enum": list(tienda.TECNICAS)},
                    "ubicaciones_extra": {"type": "integer", "description": "0 si solo lleva el logo principal"},
                },
                "required": ["sku", "talla", "cantidad", "personalizacion", "ubicaciones_extra"],
            },
        },
        "logos": {"type": "integer", "description": "Diseños de logo distintos (1 si no dijo)"},
    },
    "required": ["items", "logos"],
}


SEND_ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        **ORDER_SCHEMA["properties"],
        "canal": {"type": "string", "enum": list(CANALES),
                  "description": "Por dónde la quiere: solo este chat, correo o ambos."},
        "email": {"type": "string",
                  "description": "El correo que la persona escribió en el chat, o cadena vacía si la quiere solo por aquí."},
        "nombre": {"type": "string", "description": "Su nombre, o cadena vacía."},
        "empresa": {"type": "string", "description": "Su empresa, o cadena vacía."},
    },
    "required": ORDER_SCHEMA["required"] + ["canal", "email", "nombre", "empresa"],
}


def store_tools(
    google: Google, store: Store | None = None, user_id: str = "",
    instagram: Instagram | None = None, state: dict | None = None,
) -> list:
    """Catálogo, existencias y cotización de la tienda (hojas de Google)."""
    state = state if state is not None else {}

    async def load(what):
        try:
            return await what(google), None
        except Exception:
            log.exception("No se pudieron leer las hojas de la tienda")
            return None, error(
                "No se pudo consultar el catálogo en este momento. Di que lo confirmas en un rato"
                " u ofrece pasar con una persona."
            )

    @tool(
        "ver_productos",
        "Lista los productos con material, precio por pieza en cada rango, mínimo y si se pueden"
        " personalizar. tipo: filtro opcional (p. ej. 'polo', 'hoodie', 'gorra'; cadena vacía = todos).",
        {"tipo": str},
    )
    async def ver_productos(args):
        catalogo, err = await load(tienda.products)
        return err or ok(tienda.catalog_text(catalogo, tienda.rules(), args.get("tipo", "")))

    @tool(
        "ver_existencias",
        "Tallas y piezas disponibles de un producto (por SKU).",
        {"sku": str},
    )
    async def ver_existencias(args):
        existencias, err = await load(tienda.stock)
        if err:
            return err
        tallas = existencias.get(args["sku"].strip().upper())
        if not tallas:
            return error("No hay existencias registradas de ese SKU; revisa con ver_productos.")
        return ok(", ".join(f"{t}: {n} disponibles" for t, n in tallas.items()))

    @tool(
        "cotizar_pedido",
        "Calcula el precio exacto de un pedido (prendas, personalización, IVA, total y tiempo de"
        " entrega) con los precios por volumen. Usa SIEMPRE esto para dar precios o totales.",
        ORDER_SCHEMA,
    )
    async def cotizar_pedido(args):
        catalogo, err = await load(tienda.products)
        if err:
            return err
        existencias, err = await load(tienda.stock)
        if err:
            return err
        try:
            order = tienda.quote(args.get("items", []), args.get("logos", 1), catalogo, existencias, tienda.rules())
        except (ValueError, KeyError, TypeError) as e:
            return error(str(e))
        await log_quote(order, catalogo, args)
        return ok(order.texto())

    async def log_quote(order, catalogo, args):
        if store:  # para el reporte semanal
            productos = [
                {"producto": catalogo[str(i["sku"]).strip().upper()]["Name_ES"], "cantidad": int(i["cantidad"])}
                for i in args.get("items", [])
            ]
            await store.add_event(user_id, "cotizacion", {"total": order.total, "productos": productos})

    @tool(
        "enviar_cotizacion_pedido",
        "Genera la cotización formal del pedido en PDF (con folio) y se la manda a la persona por"
        " este DM, por correo (con copia al negocio) o por ambos, según lo que ella eligió. Antes"
        " confirma con ella el pedido exacto (prendas, tallas, cantidades, personalización).",
        SEND_ORDER_SCHEMA,
    )
    async def enviar_cotizacion_pedido(args):
        canal, email = args.get("canal", "").strip().lower(), args.get("email", "").strip()
        if problem := await check_quote_delivery(store, instagram, user_id, canal, email):
            return error(problem)
        catalogo, err = await load(tienda.products)
        if err:
            return err
        existencias, err = await load(tienda.stock)
        if err:
            return err
        try:
            order = tienda.quote(args.get("items", []), args.get("logos", 1), catalogo, existencias, tienda.rules())
        except (ValueError, KeyError, TypeError) as e:
            return error(str(e))

        nombre, empresa = args.get("nombre", "").strip()[:80], args.get("empresa", "").strip()[:80]
        token = secrets.token_urlsafe(16)
        number = await store.add_quote(user_id, token, "pedido", "", 0, order.total, email)
        folio = f"COT-{number:04d}"
        data = tienda.pdf(order, folio, nombre, empresa, email, now().date())
        folder = Path(cuenta().quotes_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{token}.pdf").write_bytes(data)

        reglas = tienda.rules()
        neg = reglas["negocio"]
        subject = f"Cotización {folio} · {neg['nombre']}"
        body = (
            f"Hola{' ' + nombre if nombre else ''}:\n\n"
            f"Te comparto la cotización {folio} que platicamos por Instagram (va en el PDF adjunto).\n\n"
            f"{order.texto()}\n\n"
            f"Es válida por {reglas['vigencia_dias']} días. Para confirmar el pedido o cualquier duda,"
            f" contesta este correo o escríbenos a {neg['instagram']}.\n\nSaludos,\n{neg['nombre']}"
        )
        sent = await deliver_quote(
            store, google, instagram, user_id, canal, email, folio, token, data, subject, body,
            cuenta().mail_from_name or neg["nombre"],
        )
        if not sent:
            return error("No se pudo mandar la cotización en este momento. Ofrece intentarlo en un rato.")
        await log_quote(order, catalogo, args)
        state["acted"] = True
        return ok(f"Cotización {folio} enviada {sent}.\n{order.texto()}")

    tools = [ver_productos, ver_existencias, cotizar_pedido]
    return tools + [enviar_cotizacion_pedido] if store else tools


# Por qué el asistente pasa un chat a una persona: sale como etiqueta en Conversaciones.
RAZONES = {"pide_persona": "Pidió una persona", "molesto": "Molesto", "precio": "Negociar precio",
           "fuera_catalogo": "Fuera del catálogo", "otro": "Otro motivo"}
# Lo que anota el asistente cuando alguien está a punto de comprar un servicio (Resultados > Anuncios).
INTERESES = {"cotizacion": "Pidió cotización", "visita": "Pidió visita o medición", "datos": "Dejó sus datos"}
HUMANO_SCHEMA = {
    "type": "object",
    "properties": {
        "razon": {"type": "string", "enum": list(RAZONES),
                  "description": "pide_persona si pidió hablar con alguien; molesto si está enojado o"
                                 " inconforme; precio si quiere negociar; fuera_catalogo si busca algo que no"
                                 " está en lo que vendemos; otro para lo demás."},
        "motivo": {"type": "string", "description": "Una línea con qué necesita."},
    },
    "required": ["razon", "motivo"],
}


def human_tools(store: Store, google: Google | None, user_id: str, state: dict | None = None,
                instagram: Instagram | None = None) -> list:
    state = state if state is not None else {}

    @tool(
        "pasar_a_humano",
        "Avisa al dueño para que una persona continúe esta conversación y deja de contestar aquí"
        " por unas horas. Úsalo si la persona lo pide, si quiere negociar precio o algo fuera del"
        " catálogo, o si está molesta.",
        HUMANO_SCHEMA,
    )
    async def pasar_a_humano(args):
        razon = args.get("razon") if args.get("razon") in RAZONES else "otro"  # se valida aquí, no en el prompt
        # El evento va antes que la pausa: así el panel en vivo ya lo ve como "nuevo" al despertar.
        await store.add_event(user_id, "humano", {"motivo": args["motivo"][:300], "razon": razon})
        await store.pause(user_id, time.time() + cuenta().human_pause_hours * 3600)
        state["acted"] = True
        # Por DM a los administradores (solo llega si le escribieron al bot en las últimas 24 h).
        if instagram and cuenta().aviso_dm:
            quien = await instagram.username(user_id)
            for admin_id in cuenta().admin_ig_ids:
                try:
                    await instagram.send_text(admin_id, f"{quien} necesita hablar contigo ({RAZONES[razon]}): {args['motivo'][:300]}")
                except Exception:
                    log.warning("No se pudo avisar por DM a %s", admin_id)
        if google and cuenta().owner_email and cuenta().aviso_correo:
            facts = await store.facts(user_id)
            recent = await store.recent(user_id, 10)
            body = (
                f"Alguien en Instagram pidió hablar con una persona.\n\nMotivo: {args['motivo'][:500]}\n\n"
                + ("Datos guardados:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()) + "\n\n" if facts else "")
                + "Últimos mensajes:\n"
                + "\n".join(f"{'Cliente' if r == 'user' else 'Bot'}: {t}" for r, t in recent)
                + f"\n\nEl bot no contestará en esa conversación durante {cuenta().human_pause_hours:g} horas."
                " Contéstale desde Instagram."
            )
            try:
                await google.send_email(
                    config.MAIL_FROM, cuenta().mail_from_name, cuenta().owner_email, "",
                    "Instagram: un cliente quiere hablar contigo", body,
                )
            except Exception:
                log.exception("No se pudo avisar al dueño de %s", user_id)
        return ok("Listo: el dueño fue avisado y continuará la conversación. Despídete por ahora.")

    @tool(
        "anotar_interes",
        "Anota que esta persona pidió cotización o presupuesto, una visita o medición, o dejó sus datos para"
        " que el negocio la contacte. Úsala una vez cuando pase; la persona no ve nada. No la uses si solo"
        " preguntó un precio del catálogo.",
        {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": list(INTERESES)},
            "detalle": {"type": "string", "description": "Una línea: qué servicio y lo que ya se sabe."}},
         "required": ["tipo"]},
    )
    async def anotar_interes(args):
        tipo = args.get("tipo") if args.get("tipo") in INTERESES else "cotizacion"
        await store.add_event(user_id, "interes", {"tipo": tipo, "detalle": str(args.get("detalle") or "")[:300]})
        conversiones_meta.avisar(cuenta(), user_id, "interes")
        return ok("Anotado.")

    return [pasar_a_humano, anotar_interes]


def build(
    store: Store, google: Google | None, user_id: str, state: dict, instagram: Instagram | None = None,
    extra: list | None = None, agenda: Google | None = None, presencial: bool = False, minutos: int = 0,
):
    """Servidor MCP con las herramientas de este usuario y la lista de permitidas.
    `extra`: las de sus conexiones (p. ej. el inventario de su Shopify). `agenda`: el Google Calendar
    que conectó la cuenta; sus citas van ahí aunque la cuenta no use tu Google."""
    tools = memory_tools(store, user_id) + human_tools(store, google, user_id, state, instagram) + (extra or [])
    if agenda:
        tools += workspace_tools(store, agenda, user_id, state, correo=bool(google and cuenta().google_tools),
                                 presencial=presencial, minutos=minutos)
    # Agenda, correo y cotizaciones usan tu Google: solo en las cuentas que lo tienen.
    if google and cuenta().google_tools:
        if not agenda:
            tools += workspace_tools(store, google, user_id, state)
        if catalog.available():
            tools += quote_tools(store, google, instagram, user_id, state)
        if tienda.available():
            tools += store_tools(google, store, user_id, instagram, state)
    server = create_sdk_mcp_server("bot", tools=tools)
    return server, [f"mcp__bot__{t.name}" for t in tools]
