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
    "SHEET_CATALOG", "SHEET_INVENTORY", "STORE_RULES_PATH", "SHEET_COMMENTS",
    "ADMIN_IG_IDS", "REPORT_EMAIL", "BUTTONS_PATH", "HIDE_SPAM", "GOOGLE_TOOLS",
}
if CLIENTE:
    load_dotenv(f"{CLIENT_DIR}/.env")
    for key, value in dotenv_values(".env").items():
        if key not in ACCOUNT_KEYS and value is not None:
            os.environ.setdefault(key, value)
else:
    load_dotenv()
# Lo que es de cada cuenta (token, prompt, catálogo, horario, límites...) se
# lee en igbot/cuenta.py; aquí queda solo lo común a todas.
VERIFY_TOKEN = os.environ["IG_VERIFY_TOKEN"]
APP_SECRET = os.environ["IG_APP_SECRET"]
API_VERSION = os.getenv("IG_API_VERSION", "v23.0")
# Cuentas de clientes que atiende este mismo proceso, además de la suya:
# nombres de carpetas de clientes/ con su .env (p. ej. CUENTAS=nortex,cafelunar).
CUENTAS = [c.strip() for c in os.getenv("CUENTAS", "").split(",") if c.strip()]
# Meta manda los avisos de todas las cuentas de la app al mismo webhook. El
# proceso que los recibe se los reenvía tal cual a los de las otras cuentas
# (lista separada por comas, p. ej. http://127.0.0.1:8002/webhook).
FORWARD_WEBHOOKS = [u.strip() for u in os.getenv("FORWARD_WEBHOOKS", "").split(",") if u.strip()]

# Modelo barato para reconocer variantes de las palabras clave en comentarios.
COMMENTS_MODEL = os.getenv("COMMENTS_MODEL", "claude-haiku-5-5")
# Segundos que se reutiliza lo leído de las hojas antes de volver a pedirlo.
SHEETS_CACHE_SECONDS = float(os.getenv("SHEETS_CACHE_SECONDS", "300"))
# Dirección pública del bot, para mandar el PDF por DM (Instagram lo descarga de ahí).
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# Segundos que se espera a que el usuario termine de escribir antes de contestar.
DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "5"))
# Conversaciones que se atienden a la vez (cada una abre un proceso del Agent SDK).
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
# Y de una misma cuenta, para que un cliente con mucho tráfico no frene a los demás.
MAX_CONCURRENT_PER_ACCOUNT = int(os.getenv("MAX_CONCURRENT_PER_ACCOUNT", "3"))

# --- Google (opcional: sin esto el bot no agenda ni manda correos) ---
# Credenciales OAuth de tu cuenta de Gmail; ver "Agenda y correo" en el README.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")  # tu dirección de Gmail

# --- Conectar cuentas de clientes (skandar.online/conectar) ---
# Instagram app ID y secreto de "API de Instagram con inicio de sesión de
# Instagram" en el panel de Meta. Sin IG_APP_ID, /conectar no se activa.
IG_APP_ID = os.getenv("IG_APP_ID", "")
IG_OAUTH_SECRET = os.getenv("IG_OAUTH_SECRET", "") or APP_SECRET
SITE_URL = os.getenv("SITE_URL", "https://skandar.online").rstrip("/")
