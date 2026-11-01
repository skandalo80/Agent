"""El marco del panel (igbot/marco.py) no carga ninguna sección al importarse: las secciones lo importan a él."""
import os
import subprocess
import sys

SECCIONES = ("igbot.panel", "igbot.empresa", "igbot.asistente", "igbot.campanas", "igbot.contenido", "igbot.conversaciones",
             "igbot.skandi", "igbot.imagenes", "igbot.agent", "igbot.tools")


def test_marco_no_importa_secciones():
    codigo = "import sys, igbot.marco; print(' '.join(m for m in sys.modules if m.startswith('igbot.')))"
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=True,
                            env={**os.environ, "PYTHONPATH": os.getcwd()}).stdout
    cargados = set(salida.split())
    assert "igbot.marco" in cargados
    assert not cargados & set(SECCIONES), cargados & set(SECCIONES)


def test_panel_usa_el_marco():
    """La sección sigue teniendo lo suyo y lo común solo está en el marco."""
    from igbot import marco, panel
    assert hasattr(marco, "cuenta_de_sesion") and hasattr(marco, "pagina") and hasattr(marco, "trabajo_largo")
    assert not hasattr(panel, "cuenta_de_sesion") and not hasattr(panel, "menu_de")
    assert hasattr(panel, "generar") and hasattr(panel, "preparar_carpeta")
