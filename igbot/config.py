"""Configuración leída de .env (ver .env.example)."""
import os

from dotenv import dotenv_values, load_dotenv

# Cada cuenta de Instagram corre como un proceso aparte. La cuenta principal
# (el vendedor) usa solo .env; otra cuenta usa CLIENTE=<nombre>, que lee
# primero clientes/<nombre>/.env y completa lo que falte con el .env común
# (llaves de Meta y de Claude). Sus datos quedan en data/<nombre>/.
CLIENTE = os.getenv("CLIENTE", "")
CLIENT_DIR = f"clientes/{CLIENTE}" if CLIENTE else ""
# Lo que es de cada cuenta nunca se toma del .env común: si a Nortex le faltara
# su token, contestaría como el vendedor (y con FORWARD_WEBHOOKS se reenviaría
# los avisos a sí mismo).
ACCOUNT_KEYS = {
    "IG_ACCESS_TOKEN", "IG_ACCOUNT_ID", "FORWARD_WEBHOOKS",
    "DB_PATH", "PROMPT_PATH", "CATALOG_PATH", "QUOTES_DIR",
}
if CLIENTE:
    load_dotenv(f"{CLIENT_DIR}/.env")
    for key, value in dotenv_values(".env").items():
        if key not in ACCOUNT_KEYS and value is not None:
            os.environ.setdefault(key, value)
else:
    load_dotenv()
DATA_DIR = f"data/{CLIENTE}" if CLIENTE else "data"

VERIFY_TOKEN = os.environ["IG_VERIFY_TOKEN"]
APP_SECRET = os.environ["IG_APP_SECRET"]
# Token inicial. Después la app lo renueva sola y guarda el nuevo en la base.
ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
API_VERSION = os.getenv("IG_API_VERSION", "v23.0")
# Id de la cuenta de Instagram de este proceso (si se deja vacío se pregunta a
# la API al arrancar). Con él se ignoran los avisos de Meta de otras cuentas.
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "")
# Meta manda los avisos de todas las cuentas de la app al mismo webhook. El
# proceso que los recibe se los reenvía tal cual a los de las otras cuentas
# (lista separada por comas, p. ej. http://127.0.0.1:8002/webhook).
FORWARD_WEBHOOKS = [u.strip() for u in os.getenv("FORWARD_WEBHOOKS", "").split(",") if u.strip()]

DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/bot.db")
PROMPT_PATH = os.getenv("PROMPT_PATH", f"{CLIENT_DIR}/system.md" if CLIENTE else "prompts/system.md")
# Paquetes y precios de las cotizaciones (si no existe, no hay cotizaciones).
CATALOG_PATH = os.getenv("CATALOG_PATH", f"{CLIENT_DIR}/catalogo.yaml" if CLIENTE else "negocio/catalogo.yaml")
QUOTES_DIR = os.getenv("QUOTES_DIR", f"{DATA_DIR}/cotizaciones")
# Dirección pública del bot, para mandar el PDF por DM (Instagram lo descarga de ahí).
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "low")

# Mensajes recientes que el modelo ve tal cual; lo anterior va al resumen.
HISTORY_WINDOW = int(os.getenv("HISTORY_WINDOW", "20"))
# Segundos que se espera a que el usuario termine de escribir antes de contestar.
DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "5"))
# Conversaciones que se atienden a la vez (cada una abre un proceso del Agent SDK).
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))

# --- Google (opcional: sin esto el bot no agenda ni manda correos) ---
# Credenciales OAuth de tu cuenta de Gmail; ver "Agenda y correo" en el README.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")  # tu dirección de Gmail
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "")  # nombre que ve el cliente
MAIL_CC = os.getenv("MAIL_CC", "")  # copia extra de cada correo (opcional)

TIMEZONE = os.getenv("TIMEZONE", "America/Mexico_City")
BUSINESS_HOURS = os.getenv("BUSINESS_HOURS", "09:00-18:00")
BUSINESS_DAYS = [int(d) for d in os.getenv("BUSINESS_DAYS", "1,2,3,4,5").split(",")]  # 1=lunes
MEETING_MINUTES = int(os.getenv("MEETING_MINUTES", "30"))
MIN_NOTICE_HOURS = float(os.getenv("MIN_NOTICE_HOURS", "2"))
MAX_MEETINGS_PER_USER = int(os.getenv("MAX_MEETINGS_PER_USER", "2"))
MAX_EMAILS_PER_USER_PER_DAY = int(os.getenv("MAX_EMAILS_PER_USER_PER_DAY", "3"))
# A quién avisar cuando alguien pide hablar con una persona (por defecto, tu Gmail).
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "") or MAIL_FROM

# Horas que el bot se queda callado en una conversación cuando la toma una
# persona (porque se pidió o porque contestaste tú desde Instagram).
HUMAN_PAUSE_HOURS = float(os.getenv("HUMAN_PAUSE_HOURS", "12"))
