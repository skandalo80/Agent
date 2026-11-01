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


def test_guardia_de_sesion_unica():
    """Sin sesión, toda ruta del panel lleva a entrar (/panel); un POST sin venir de aquí es 403 antes de mirar la sesión."""
    import app as bot
    from fastapi.testclient import TestClient
    from igbot import config

    with TestClient(bot.app, base_url="https://testserver") as client:
        for ruta in ("/panel/empresa", "/panel/asistente", "/panel/contenido/campanas", "/panel/resultados", "/panel/inicio",
                     "/panel/probar", "/panel/conexion", "/panel/trabajo/x"):
            r = client.get(ruta, follow_redirects=False)
            assert (r.status_code, r.headers.get("location")) == (303, "/panel"), ruta
        assert client.post("/panel/asistente/paso", data={"paso": "1"}, follow_redirects=False).status_code == 403
        r = client.post("/panel/asistente/paso", data={"paso": "1"}, follow_redirects=False,
                        headers={"origin": config.PUBLIC_URL.rstrip("/")})
        assert (r.status_code, r.headers.get("location")) == (303, "/panel")
