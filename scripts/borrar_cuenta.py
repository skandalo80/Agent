"""Borra todo lo de una cuenta de cliente: cuando pide borrar sus datos desde Meta (/conectar/borrar-datos)
o cuando deja el servicio. Se corre en el servidor CON EL BOT DETENIDO:

    cd ~/ig-bot && sudo systemctl stop igbot && .venv/bin/python scripts/borrar_cuenta.py <nombre-corto> && sudo systemctl start igbot

Borra data/<nombre-corto>/ (bases, medios, cotizaciones), clientes/<nombre-corto>/ (.env con su token, system.md)
y su fila en conexiones de la base principal. Con --ver solo dice qué borraría. Después quita el nombre de
CUENTAS en .env si está ahí. Los respaldos en ~/respaldos se van solos a los 14 días.
"""
import re
import shutil
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SLUG = re.compile(r"^[a-z0-9_-]{2,30}$")


def borrar(slug: str, raiz: Path = RAIZ, ver: bool = False) -> list[str]:
    """Qué se borró (o se borraría con ver=True)."""
    if not SLUG.match(slug):
        raise SystemExit(f"Nombre inválido: {slug!r}")
    hechos = []
    for carpeta in (raiz / "data" / slug, raiz / "clientes" / slug):
        if carpeta.is_dir():
            hechos.append(f"carpeta {carpeta.relative_to(raiz)}")
            if not ver:
                shutil.rmtree(carpeta)
    principal = raiz / "data" / "bot.db"
    if principal.is_file():
        with sqlite3.connect(principal) as db:
            filas = db.execute("SELECT ig_id, usuario FROM conexiones WHERE slug = ?", (slug,)).fetchall()
            for ig_id, usuario in filas:
                hechos.append(f"conexión de @{usuario} ({ig_id})")
            if filas and not ver:
                db.execute("DELETE FROM conexiones WHERE slug = ?", (slug,))
    return hechos


def main(args: list[str]) -> int:
    ver = "--ver" in args
    args = [a for a in args if a != "--ver"]
    if len(args) != 1:
        print(__doc__)
        return 2
    hechos = borrar(args[0], ver=ver)
    if not hechos:
        print(f"No hay nada de {args[0]}.")
        return 1
    for h in hechos:
        print(("Se borraría: " if ver else "Borrado: ") + h)
    if not ver:
        print(f"Listo. Si {args[0]} está en CUENTAS del .env, quítalo y reinicia.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
