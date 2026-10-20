"""La cuenta de Instagram que se está atendiendo.

Todo lo que puede cambiar de un negocio a otro (token, prompt, catálogo, hojas,
horario, límites, a quién avisar) vive en una Cuenta, y el código lo lee con
cuenta().<dato>. Lo que es igual para todas (llaves de Meta, de Claude y de
Google, la dirección pública del bot) se queda en config.

El proceso principal atiende su cuenta (la de .env) y las de CUENTAS, cada
una leída de clientes/<nombre>/.env (ver cargar()). Antes de atender algo de
una cuenta se fija con:

    with usar(otra):
        ...  # aquí cuenta() es `otra`, también en las tareas que se creen dentro

La cuenta actual es una variable de contexto: cada tarea de asyncio tiene la
suya, así que dos conversaciones de cuentas distintas a la vez no se mezclan.
"""
import logging
import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from . import config

log = logging.getLogger("igbot.cuenta")


def _bool(value: str) -> bool:
    return value in ("1", "true", "si", "sí")


def _ids(value: str) -> set[str]:
    return {i.strip() for i in value.split(",") if i.strip()}


@dataclass
class Cuenta:
    slug: str  # "" = la principal (el vendedor); si no, el nombre de clientes/<slug>/
    access_token: str
    ig_account_id: str
    db_path: str
    prompt_path: str
    catalog_path: str
    quotes_dir: str
    sheet_catalog: str
    sheet_inventory: str
    store_rules_path: str
    sheet_comments: str
    hide_spam: bool
    buttons_path: str
    admin_ig_ids: set[str]
    media_slug: str
    media_dir: str
    report_email: str
    report_day: int
    report_hour: int
    model: str
    effort: str
    history_window: int
    mail_from_name: str
    mail_cc: str
    owner_email: str
    timezone: str
    business_hours: str
    business_days: list[int]
    meeting_minutes: int
    min_notice_hours: float
    max_meetings_per_user: int
    max_emails_per_user_per_day: int
    max_replies_per_user_per_day: int
    max_message_chars: int
    daily_budget_usd: float
    human_pause_hours: float
    google_tools: bool
    imagenes_mes: int
    mensajes_mes: int = 0
    posts_mes: int = 0
    # Su base y su cliente de Instagram; los pone app.py al arrancar.
    store: Any = field(default=None, repr=False, compare=False)
    instagram: Any = field(default=None, repr=False, compare=False)
    # Ajustes que el dueño cambia en su panel (Tu asistente > Ajustes; ver igbot/ajustes.py).
    aviso_dm: bool = True  # avisar por DM a los administradores cuando piden una persona
    aviso_correo: bool = True  # y por correo a owner_email
    fuera_horario: str = ""  # mensaje fijo fuera de horario; vacío = decir cuándo le contestan

    def ruta_prompt(self) -> Path:
        """Las instrucciones vigentes. La principal usa prompts/system.md (del repo) salvo que
        se hayan editado en el panel: esas van junto a su base (data/system.md), fuera de git."""
        if not self.slug and (editado := self.prompt_editado()).is_file():
            return editado
        return Path(self.prompt_path)

    def prompt_editado(self) -> Path:
        return Path(self.db_path).parent / "system.md"

    @classmethod
    def desde_env(cls, slug: str, env: Mapping[str, str]) -> "Cuenta":
        """Arma la cuenta con las variables de su .env (ver .env.example)."""
        def get(key: str, default: str = "") -> str:
            return env.get(key) or default

        client_dir = f"clientes/{slug}" if slug else ""
        data_dir = f"data/{slug}" if slug else "data"
        return cls(
            slug=slug,
            access_token=env["IG_ACCESS_TOKEN"],
            # Si se deja vacío, se pregunta a la API al arrancar.
            ig_account_id=get("IG_ACCOUNT_ID"),
            db_path=get("DB_PATH", f"{data_dir}/bot.db"),
            prompt_path=get("PROMPT_PATH", f"{client_dir}/system.md" if slug else "prompts/system.md"),
            # Paquetes y precios de las cotizaciones (si no existe, no hay cotizaciones).
            catalog_path=get("CATALOG_PATH", f"{client_dir}/catalogo.yaml" if slug else "negocio/catalogo.yaml"),
            quotes_dir=get("QUOTES_DIR", f"{data_dir}/cotizaciones"),
            # Tienda de productos: catálogo y existencias en Google Sheets (el link
            # de cada hoja, con su #gid=) y las reglas de precios por cantidad en YAML.
            sheet_catalog=get("SHEET_CATALOG"),
            sheet_inventory=get("SHEET_INVENTORY"),
            store_rules_path=get("STORE_RULES_PATH", f"{client_dir}/reglas.yaml" if slug else "negocio/reglas.yaml"),
            # Reglas de comentarios (palabra clave -> DM): link de la pestaña de la hoja.
            sheet_comments=get("SHEET_COMMENTS"),
            # Ocultar comentarios que son spam en todas las publicaciones.
            hide_spam=_bool(get("HIDE_SPAM")),
            # Botones de inicio (preguntas que salen al abrir un chat nuevo).
            buttons_path=get("BUTTONS_PATH", f"{client_dir}/botones.yaml" if slug else "negocio/botones.yaml"),
            # Ids de Instagram de quienes, al escribirle al bot, hablan con el
            # asistente interno (publicar, reactivar chats). Cambian en cada cuenta.
            admin_ig_ids=_ids(get("ADMIN_IG_IDS")),
            # Fotos que mandan los administradores; se sirven en
            # PUBLIC_URL/media/<media_slug>/<nombre>.jpg.
            media_slug=slug or "principal",
            media_dir=f"{data_dir}/media",
            # Reporte semanal por correo (si hay) y por DM a los administradores.
            report_email=get("REPORT_EMAIL"),
            report_day=int(get("REPORT_DAY", "1")),
            report_hour=int(get("REPORT_HOUR", "9")),
            model=get("CLAUDE_MODEL", "claude-opus-5-5"),
            effort=get("CLAUDE_EFFORT", "low"),
            # Mensajes recientes que el modelo ve tal cual; lo anterior va al resumen.
            history_window=int(get("HISTORY_WINDOW", "20")),
            mail_from_name=get("MAIL_FROM_NAME"),  # nombre que ve el cliente
            mail_cc=get("MAIL_CC"),  # copia extra de cada correo (opcional)
            # A quién avisar cuando alguien pide una persona (por defecto, tu Gmail).
            owner_email=get("OWNER_EMAIL", config.MAIL_FROM),
            timezone=get("TIMEZONE", "America/Mexico_City"),
            business_hours=get("BUSINESS_HOURS", "09:00-18:00"),
            business_days=[int(d) for d in get("BUSINESS_DAYS", "1,2,3,4,5").split(",")],  # 1=lunes
            meeting_minutes=int(get("MEETING_MINUTES", "30")),
            min_notice_hours=float(get("MIN_NOTICE_HOURS", "2")),
            max_meetings_per_user=int(get("MAX_MEETINGS_PER_USER", "2")),
            max_emails_per_user_per_day=int(get("MAX_EMAILS_PER_USER_PER_DAY", "3")),
            # Límites contra abuso: respuestas por persona al día, caracteres de
            # cada DM que se le pasan a Claude y gasto máximo diario (0 = sin límite).
            max_replies_per_user_per_day=int(get("MAX_REPLIES_PER_USER_PER_DAY", "30")),
            max_message_chars=int(get("MAX_MESSAGE_CHARS", "1000")),
            daily_budget_usd=float(get("DAILY_BUDGET_USD", "5")),
            # Horas que el bot se calla en una conversación que toma una persona.
            human_pause_hours=float(get("HUMAN_PAUSE_HOURS", "12")),
            # Agendar en tu calendario, mandar correos y cotizar con tu Google.
            # Las cuentas que se dan de alta solas desde el panel (Básico) no lo tienen.
            google_tools=_bool(get("GOOGLE_TOOLS", "1")),
            # Imágenes de contenido incluidas al mes (0 = su plan no incluye contenido;
            # Community 40, Pro 80). Las que pasen de aquí se cobran aparte.
            imagenes_mes=int(get("IMAGES_PER_MONTH", "0")),
            # Respuestas del asistente y posts que incluye el plan al mes (0 = sin tope).
            # Solo se muestran en el panel: pasarse no corta nada, se ve como extra.
            mensajes_mes=int(get("MESSAGES_PER_MONTH", "0")),
            posts_mes=int(get("POSTS_PER_MONTH", "0")),
        )


def de_carpeta(slug: str) -> Cuenta:
    """Cuenta de clientes/<slug>/.env; lo que no es de la cuenta sale del .env común."""
    propio = {k: v for k, v in dotenv_values(f"clientes/{slug}/.env").items() if v is not None}
    comun = {k: v for k, v in os.environ.items() if k not in config.ACCOUNT_KEYS}
    return Cuenta.desde_env(slug, {**comun, **propio})


def dadas_de_alta(db_path: str) -> list[str]:
    """Las cuentas que se dieron de alta solas al conectarse (ver panel.py)."""
    if not Path(db_path).is_file():
        return []
    try:
        with closing(sqlite3.connect(db_path)) as db:
            return [r[0] for r in db.execute(
                "SELECT slug FROM conexiones WHERE slug IS NOT NULL ORDER BY created_at")]
    except sqlite3.OperationalError:
        return []  # base de antes del panel (sin la columna slug)


def cargar() -> list[Cuenta]:
    """La cuenta de este proceso y, en el principal, las de CUENTAS y las del panel."""
    cuentas = [cuenta()]
    if config.CLIENTE:
        return cuentas  # proceso de una sola cuenta (igbot@<nombre>)
    for slug in dict.fromkeys(config.CUENTAS + dadas_de_alta(cuentas[0].db_path)):
        if not Path(f"clientes/{slug}/.env").is_file():
            log.error("CUENTAS incluye %s, pero no existe clientes/%s/.env; se omite", slug, slug)
            continue
        try:
            cuentas.append(de_carpeta(slug))
        except KeyError as e:
            log.error("A clientes/%s/.env le falta %s; se omite", slug, e)
    return cuentas


# La cuenta de este proceso (la de su .env); ver config.py.
_actual: ContextVar[Cuenta] = ContextVar("cuenta", default=Cuenta.desde_env(config.CLIENTE, os.environ))


def cuenta() -> Cuenta:
    return _actual.get()


@contextmanager
def usar(c: Cuenta) -> Iterator[Cuenta]:
    token = _actual.set(c)
    try:
        yield c
    finally:
        _actual.reset(token)
