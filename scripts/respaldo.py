"""Respaldo diario de las bases SQLite (conversaciones, leads, gasto). Se corre en el servidor:

    cd ~/ig-bot && .venv/bin/python scripts/respaldo.py

Copia cada data/**/*.db a ~/respaldos/AAAA-MM-DD/ con la API de backup de SQLite
(es segura aunque el bot esté escribiendo) y borra los respaldos de más de DIAS días.
Lo corre todos los días el timer deploy/igbot-respaldo.timer.
"""
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DESTINO = Path.home() / "respaldos"
DIAS = 14


def respaldar(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(origen) as src, sqlite3.connect(destino) as dst:
        src.backup(dst)


def main() -> int:
    hoy = DESTINO / date.today().isoformat()
    bases = sorted(DATA.rglob("*.db"))
    for base in bases:
        respaldar(base, hoy / base.relative_to(DATA))
    print(f"{len(bases)} base(s) respaldadas en {hoy}")

    limite = (date.today() - timedelta(days=DIAS)).isoformat()
    for carpeta in DESTINO.iterdir():
        if carpeta.is_dir() and carpeta.name < limite:
            shutil.rmtree(carpeta)
    return 0 if bases else 1


if __name__ == "__main__":
    sys.exit(main())
