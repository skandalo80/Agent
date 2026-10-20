"""Da de alta el bot de un cliente que ya conectó su Instagram en skandar.online/conectar.

    cd ~/ig-bot && .venv/bin/python scripts/alta_cliente.py <usuario> <nombre-corto> <puerto>
    p. ej.: .venv/bin/python scripts/alta_cliente.py cafe.lunar cafelunar 8003

Sin argumentos, lista las cuentas conectadas. Crea clientes/<nombre-corto>/.env
con su token, id y puerto (y un system.md para que lo completes si no existe) y
escribe los comandos que faltan para encender su proceso.
"""
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BASE = RAIZ / "data" / "bot.db"
SLUG = re.compile(r"^[a-z0-9_-]{2,30}$")

SYSTEM_MD = """Eres el asistente de Instagram de @{usuario}.

(Completa aquí: qué vende el negocio, horarios, precios, tono y qué hacer si piden hablar con una persona.)
"""


def conexiones(db: sqlite3.Connection) -> list[tuple]:
    return db.execute("SELECT usuario, ig_id, token, expires_at FROM conexiones ORDER BY created_at DESC").fetchall()


def main(args: list[str]) -> int:
    with sqlite3.connect(BASE) as db:
        filas = conexiones(db)
    if not args:
        if not filas:
            print("Todavía no hay cuentas conectadas.")
        for usuario, ig_id, _, expires_at in filas:
            vence = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d")
            print(f"@{usuario}  id {ig_id}  token vence {vence}")
        return 0
    if len(args) != 3 or not SLUG.match(args[1]) or not args[2].isdigit():
        print(__doc__)
        return 1
    usuario, slug, puerto = args[0].lower().removeprefix("@"), args[1], args[2]
    fila = next((f for f in filas if f[0] == usuario), None)
    if not fila:
        print(f"@{usuario} no ha conectado su Instagram. Mándale https://skandar.online/conectar")
        return 1
    carpeta = RAIZ / "clientes" / slug
    env = carpeta / ".env"
    if env.exists():
        print(f"{env} ya existe; no se toca. Borra o edita ese archivo a mano.")
        return 1
    carpeta.mkdir(parents=True, exist_ok=True)
    env.write_text(f"PORT={puerto}\nIG_ACCESS_TOKEN={fila[2]}\nIG_ACCOUNT_ID={fila[1]}\n")
    os.chmod(env, 0o600)
    if not (carpeta / "system.md").exists():
        (carpeta / "system.md").write_text(SYSTEM_MD.format(usuario=usuario))
    print(f"Listo: {env} (token e id de @{usuario}).")
    print(f"""
Falta:
1. Escribe el prompt del negocio en clientes/{slug}/system.md
   (y CLAUDE_MODEL=claude-haiku-5-5 en su .env si es paquete Básico).
2. Agrega http://127.0.0.1:{puerto}/webhook a FORWARD_WEBHOOKS en ~/ig-bot/.env
   (separado por comas si ya hay otra).
3. sudo systemctl enable --now igbot@{slug} && sudo systemctl restart igbot
4. sudo journalctl -u igbot@{slug} -f   # debe decir "Cuenta de Instagram: @{usuario}"
""")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
