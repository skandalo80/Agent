"""Revisa que el bot pueda leer las hojas de la tienda. Se corre en el servidor:

    cd ~/ig-bot && CLIENTE=nortex .venv/bin/python scripts/check_sheets.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from igbot import config, tienda  # noqa: E402
from igbot.google import Google  # noqa: E402

HINTS = {
    400: "Pide un refresh token nuevo con scripts/google_auth.py.",
    401: "El GOOGLE_REFRESH_TOKEN no sirve: vuelve a correr google_auth.py y reemplázalo en .env.",
    403: "Falta permiso: habilita la Google Sheets API, vuelve a autorizar con google_auth.py"
         " (debe pedir 'ver tus hojas de cálculo') y revisa que la hoja sea de esa cuenta o esté compartida con ella.",
    404: "No existe la hoja: revisa SHEET_CATALOG y SHEET_INVENTORY en clientes/<nombre>/.env.",
}


async def main() -> None:
    if not config.GOOGLE_REFRESH_TOKEN:
        sys.exit("Falta GOOGLE_REFRESH_TOKEN en .env")
    if not (config.SHEET_CATALOG and config.SHEET_INVENTORY):
        sys.exit("Faltan SHEET_CATALOG y SHEET_INVENTORY (¿corriste con CLIENTE=nortex?)")
    g = Google(config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.GOOGLE_REFRESH_TOKEN)
    for name, load in (("Catálogo", tienda.products), ("Existencias", tienda.stock)):
        try:
            rows = await load(g)
            print(f"OK  {name}: {len(rows)} productos")
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            print(f"ERROR {name}: {code} {e.response.text[:300]}")
            print("  ->", HINTS.get(code, "Revisa el mensaje de arriba."))
        except Exception as e:  # p. ej. el refresh token venció o fue revocado
            print(f"ERROR {name}: {e!r}")
            print("  ->", HINTS[401])


asyncio.run(main())
