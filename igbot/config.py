"""Configuración leída de .env (ver .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()

VERIFY_TOKEN = os.environ["IG_VERIFY_TOKEN"]
APP_SECRET = os.environ["IG_APP_SECRET"]
# Token inicial. Después la app lo renueva sola y guarda el nuevo en la base.
ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
API_VERSION = os.getenv("IG_API_VERSION", "v23.0")

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
PROMPT_PATH = os.getenv("PROMPT_PATH", "prompts/system.md")

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "low")

# Mensajes recientes que el modelo ve tal cual; lo anterior va al resumen.
HISTORY_WINDOW = int(os.getenv("HISTORY_WINDOW", "20"))
# Segundos que se espera a que el usuario termine de escribir antes de contestar.
DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "3"))
# Conversaciones que se atienden a la vez (cada una abre un proceso del Agent SDK).
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
