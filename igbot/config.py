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
    "IMAGES_PER_MONTH", "MESSAGES_PER_MONTH", "POSTS_PER_MONTH", "PLAN", "EN_REVISION",
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
# Una cuenta que se conecta con más seguidores o publicaciones que esto no puede
# encender su asistente sola: queda en revisión (EN_REVISION=1 en su .env).
CUENTA_GRANDE_SEGUIDORES = int(os.getenv("CUENTA_GRANDE_SEGUIDORES", "10000"))
CUENTA_GRANDE_POSTS = int(os.getenv("CUENTA_GRANDE_POSTS", "1000"))
# Dirección pública del bot, para mandar el PDF por DM (Instagram lo descarga de ahí).
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# Tiempo máximo de una llamada a Claude: si no contesta, falla como cualquier error (y la pantalla
# "Generando…" muestra el aviso en vez de quedarse girando).
CLAUDE_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "240"))
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

# --- Imágenes para contenido (igbot/imagenes.py) ---
# Quién pinta las imágenes: "openai" (desde 2026-10-23) o "gemini" (el de antes, por si se vuelve).
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()
# Clave de la API de OpenAI (platform.openai.com/api-keys); el tope de gasto se pone en esa cuenta.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# gpt-image-2.5-flare: rápido y de buena calidad; gpt-image-2.5-sunburst: el más capaz.
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
# Vertical 2:3; el servidor la recorta a 4:5 (1080x1350).
OPENAI_IMAGE_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1536")
# low, medium o high (más calidad = más tokens = más caro).
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")
# Clave de la API de Gemini (aistudio.google.com/apikey; cuenta de prepago). Solo con IMAGE_PROVIDER=gemini.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")
# Lo que cuesta cada imagen, para el gasto diario de la cuenta (DAILY_BUDGET_USD).
IMAGE_COST_USD = float(os.getenv("IMAGE_COST_USD", "0.05"))
# Lo que se le cobra al cliente por imagen pasado su cupo del mes (solo se muestra).
EXTRA_IMAGE_USD = os.getenv("EXTRA_IMAGE_USD", "0.25")
# Quién escribe los posts del panel (título, texto, palabra clave): mejor redacción que Haiku.
CONTENT_MODEL = os.getenv("CONTENT_MODEL", "claude-sonnet-5-5")
# Flujo de campañas: Claude escribe el brief (concepto + dirección visual) y el plan editorial; los posts y el
# prompt de cada imagen los escribe "openai" o "claude" (sin OPENAI_API_KEY, siempre Claude).
POSTS_IA = os.getenv("POSTS_IA", "openai").strip().lower()
PROMPT_IA = os.getenv("PROMPT_IA", "openai").strip().lower()
# El modelo de texto de OpenAI (platform.openai.com/docs/models) y su esfuerzo de razonamiento ("" = no se manda).
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5")
OPENAI_TEXT_EFFORT = os.getenv("OPENAI_TEXT_EFFORT", "low").strip()
# Lo que cuesta, en US$ por millón de tokens de entrada y de salida, para el gasto diario de la cuenta.
OPENAI_TEXT_USD_IN = float(os.getenv("OPENAI_TEXT_USD_IN", "1.25"))
OPENAI_TEXT_USD_OUT = float(os.getenv("OPENAI_TEXT_USD_OUT", "10"))

# --- Tienda Shopify del cliente (igbot/tienda_shopify.py) ---
# Llaves de la app de Skandar en Shopify (dev.shopify.com > tu app > Configuración). Sin ellas, "Conecta tu
# Shopify" no sale; importar el catálogo público sigue igual.
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-10")
