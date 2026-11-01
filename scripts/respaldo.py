"""Respaldo diario de las bases SQLite (conversaciones, leads, gasto). Se corre en el servidor:

    cd ~/ig-bot && .venv/bin/python scripts/respaldo.py [--correo]

Copia cada data/**/*.db a ~/respaldos/AAAA-MM-DD/ con la API de backup de SQLite
(es segura aunque el bot esté escribiendo) y borra los respaldos de más de DIAS días.
Lo corre todos los días el timer deploy/igbot-respaldo.timer.

Fuera del disco: los domingos (o con --correo) manda ese respaldo comprimido en un ZIP
por correo a RESPALDO_EMAIL (si no está, a MAIL_FROM), con el Gmail del bot. El ZIP va
cifrado (AES-256) con RESPALDO_CLAVE del .env: las bases llevan los tokens de Instagram,
Facebook, Google, Shopify, Stripe y Mercado Pago de cada cliente, y el correo se queda
guardado para siempre. Sin RESPALDO_CLAVE no se manda nada: llega solo un aviso para
ponerla. Se abre con 7-Zip, Keka o `7z x respaldo.zip`. Si el ZIP pasa de MAX_ZIP_MB,
manda solo un aviso: entonces toca llevarlo a otro lado.

Limpieza: borra los PDF de cotización de más de DIAS_PDF días. Su nombre es secreto pero
el enlace es público, así que no conviene que vivan para siempre.
"""
import asyncio
import io
import os
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
DESTINO = Path.home() / "respaldos"
DIAS = 14
DIAS_PDF = 90
MAX_ZIP_MB = 20  # Gmail acepta hasta 25 MB por correo


def respaldar(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(origen) as src, sqlite3.connect(destino) as dst:
        src.backup(dst)


def comprimir(carpeta: Path, clave: str = "") -> bytes:
    """El ZIP con las bases; con `clave`, cifrado con AES-256 (pyzipper), que abren 7-Zip y Keka."""
    out = io.BytesIO()
    if clave:
        import pyzipper
        z = pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED, compresslevel=9,
                                encryption=pyzipper.WZ_AES)
        z.setpassword(clave.encode())
    else:
        z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
    with z:
        for f in sorted(carpeta.rglob("*.db")):
            z.write(f, f.relative_to(carpeta))
    return out.getvalue()


def borrar_pdfs_viejos(data: Path = DATA, dias: int = DIAS_PDF, ahora: float | None = None) -> int:
    limite = (time.time() if ahora is None else ahora) - dias * 86400
    viejos = [f for f in data.glob("**/cotizaciones/*.pdf") if f.stat().st_mtime < limite]
    for f in viejos:
        f.unlink()
    return len(viejos)


async def mandar_por_correo(carpeta: Path) -> str:
    sys.path.insert(0, str(RAIZ))
    from igbot import config
    from igbot.google import Google

    para = os.getenv("RESPALDO_EMAIL") or config.MAIL_FROM
    if not (config.GOOGLE_REFRESH_TOKEN and para):
        return "sin Google o sin correo en .env: no se mandó fuera del disco"
    google = Google(config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.GOOGLE_REFRESH_TOKEN)
    clave = os.getenv("RESPALDO_CLAVE", "").strip()
    if not clave:
        await google.send_email(config.MAIL_FROM, "Respaldo Skandar", para, "",
                                f"Respaldo {carpeta.name}: falta RESPALDO_CLAVE",
                                "El respaldo semanal no se mandó por correo porque las bases llevan los tokens de"
                                " los clientes y no hay clave para cifrarlo. Pon RESPALDO_CLAVE=<una frase larga>"
                                " en ~/ig-bot/.env (guárdala en tu gestor de contraseñas) y vuelve a correr"
                                " `.venv/bin/python scripts/respaldo.py --correo`. Sigue en el servidor en"
                                f" {carpeta}.")
        return "sin RESPALDO_CLAVE en .env: no se mandó (solo el aviso)"
    datos = comprimir(carpeta, clave)
    mb = len(datos) / 1e6
    if mb > MAX_ZIP_MB:
        await google.send_email(config.MAIL_FROM, "Respaldo Skandar", para, "",
                                f"Respaldo {carpeta.name}: muy grande para el correo",
                                f"El respaldo comprimido pesa {mb:.1f} MB y el correo acepta hasta {MAX_ZIP_MB}."
                                f" Sigue en el servidor en {carpeta}; hay que llevarlo a otro lado.")
        return f"ZIP de {mb:.1f} MB: solo se mandó el aviso"
    await google.send_email(config.MAIL_FROM, "Respaldo Skandar", para, "",
                            f"Respaldo {carpeta.name}",
                            "Respaldo semanal de las bases de Skandar, cifrado con tu RESPALDO_CLAVE (se abre con"
                            " 7-Zip, Keka o `7z x`). Guárdalo; para restaurar, copia cada .db a ~/ig-bot/data/ con"
                            " el bot detenido.",
                            [(f"respaldo-{carpeta.name}.zip", datos)])
    return f"ZIP de {mb:.1f} MB mandado a {para}"


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

    print(f"{borrar_pdfs_viejos()} PDF de cotización de más de {DIAS_PDF} días borrados")

    if bases and ("--correo" in sys.argv or date.today().weekday() == 6):
        try:
            print(asyncio.run(mandar_por_correo(hoy)))
        except Exception as e:  # el respaldo en disco ya quedó; que el timer no lo marque como fallido
            print(f"No se pudo mandar el respaldo por correo: {e}")
    return 0 if bases else 1


if __name__ == "__main__":
    sys.exit(main())
