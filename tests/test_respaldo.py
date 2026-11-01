"""Respaldo: borra los PDF de cotización viejos y el ZIP lleva las bases."""
import importlib.util
import os
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

spec = importlib.util.spec_from_file_location("respaldo", Path(__file__).resolve().parent.parent / "scripts" / "respaldo.py")
respaldo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(respaldo)


def test_borra_solo_los_pdf_viejos(tmp_path):
    carpeta = tmp_path / "cliente" / "cotizaciones"
    carpeta.mkdir(parents=True)
    viejo, nuevo = carpeta / "a.pdf", carpeta / "b.pdf"
    viejo.write_bytes(b"%PDF")
    nuevo.write_bytes(b"%PDF")
    ahora = 1_000_000_000
    os.utime(viejo, (ahora - 100 * 86400, ahora - 100 * 86400))
    os.utime(nuevo, (ahora - 10 * 86400, ahora - 10 * 86400))
    assert respaldo.borrar_pdfs_viejos(tmp_path, 90, ahora) == 1
    assert not viejo.exists() and nuevo.exists()


def test_el_zip_lleva_las_bases(tmp_path):
    base = tmp_path / "cliente" / "bot.db"
    base.parent.mkdir()
    with sqlite3.connect(base) as db:
        db.execute("create table t (x)")
    nombres = zipfile.ZipFile(BytesIO(respaldo.comprimir(tmp_path))).namelist()
    assert nombres == ["cliente/bot.db"]


def test_el_zip_cifrado_no_se_abre_sin_clave(tmp_path):
    import pyzipper
    base = tmp_path / "cliente" / "bot.db"
    base.parent.mkdir()
    with sqlite3.connect(base) as db:
        db.execute("create table t (x)")
    datos = respaldo.comprimir(tmp_path, "frase-secreta")
    with pyzipper.AESZipFile(BytesIO(datos)) as z:
        assert z.namelist() == ["cliente/bot.db"]
        try:
            z.read("cliente/bot.db")
            assert False, "se leyó sin clave"
        except RuntimeError:
            pass
        z.setpassword(b"frase-secreta")
        assert z.read("cliente/bot.db")[:15] == b"SQLite format 3"
