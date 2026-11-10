"""Tu marca › Personaje: el personalizador por secciones, «Al azar» y lo que se guarda (sin red ni IA)."""
import random

import test_contenido
from igbot import personaje
from test_contenido import ORIGEN

cliente = test_contenido.cliente
RUTA = "/panel/contenido/personaje"


def test_sin_sesion_y_sin_origin(cliente):
    client, c, *_ = cliente
    assert client.post(RUTA, data={"accion": "guardar"}, follow_redirects=False).status_code == 403  # sin Origin
    client.cookies.clear()
    r = client.get(RUTA, follow_redirects=False)
    assert (r.status_code, r.headers.get("location")) == (303, "/panel")


def test_solo_con_contenido_en_el_plan(cliente):
    client, c, *_ = cliente
    assert "Tu plan actual es el Asistente virtual" in client.get(RUTA).text
    assert "Tu plan actual es el Asistente virtual" in client.post(RUTA, headers=ORIGEN, data={}).text


def test_armar_guardar_y_volver(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    r = client.get(RUTA)
    assert "Arma el personaje de tu marca" in r.text and 'value="azar"' in r.text
    # De fábrica: el ajolote. Todas las piezas van en la página y solo las elegidas a la vista.
    assert '<g data-pieza="arriba:branquias">' in r.text and '<g data-pieza="arriba:oso" hidden>' in r.text
    assert 'name="arriba" value="branquias" checked' in r.text and "autofocus" not in r.text
    assert 'href="/panel/contenido/personaje" class="actual"' in r.text  # en la barra de pasos de Tu marca
    assert client.portal.call(personaje.de, c) == {}

    datos = {"cabeza": "redonda", "arriba": "oso", "ojos": "entrecerrados", "boca": "picara", "cara": "capucha",
             "cuerpo": "robusto", "material": "peludo", "patron": "liso", "ropa": "ninguna", "accesorio": "lentes",
             "cuello": "bufanda", "extras": "nada", "c_cuerpo": "#8FA8E8", "c_claro": "#F3E6D3", "c_acento": "#FF8A3D",
             "c_acento2": "#FFD166", "nombre": "  Bruno   <b>  ", "rasgos": ["Amable", "Claro", "Atento", "Curioso", "x"]}
    r = client.post(RUTA, headers=ORIGEN, data={**datos, "accion": "guardar"}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, f"{RUTA}?guardado=1")
    p = client.portal.call(personaje.de, c)
    assert p["arriba"] == "oso" and p["mejillas"] is False and p["colores"]["cuerpo"] == "#8FA8E8"
    assert p["nombre"] == "Bruno <b>" and p["rasgos"] == ["Amable", "Claro", "Atento"]  # hasta 3, solo de la lista

    r = client.get(f"{RUTA}?guardado=1")
    assert "Guardamos tu personaje." in r.text and "Bruno &lt;b&gt;" in r.text and "Bruno <b>" not in r.text
    assert 'data-material="peludo"' in r.text and '<g data-pieza="arriba:oso">' in r.text
    assert "--pj-cuerpo:#8FA8E8" in r.text
    assert 'class="hecho"><b>✓</b><span>Personaje' in client.get("/panel/contenido/marca?paso=1").text

    # Aplicar guarda y se queda; lo que no es una opción válida vuelve a la de fábrica.
    r = client.post(RUTA, headers=ORIGEN, data={**datos, "arriba": "dragon", "c_cuerpo": "rojo", "accion": "aplicar"})
    assert "Cambios aplicados." in r.text
    p = client.portal.call(personaje.de, c)
    assert p["arriba"] == personaje.INICIAL["arriba"] and p["colores"]["cuerpo"] == personaje.INICIAL["colores"]["cuerpo"]


def test_al_azar_no_guarda(cliente, monkeypatch):
    client, c, *_ = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "azar", "nombre": "Bruno"})
    assert "Este salió al azar." in r.text and 'value="Bruno"' in r.text
    assert client.portal.call(personaje.de, c) == {}
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "azar_colores"})
    assert "Este salió al azar." in r.text and client.portal.call(personaje.de, c) == {}


def test_azar_con_reglas():
    antes = personaje.limpio({"nombre": "Bruno", "rasgos": ["Claro"], "colores": {"cuerpo": "#112233"}})
    vistos = set()
    for semilla in range(200):
        p = personaje.al_azar(antes, random.Random(semilla))
        assert all(p[campo] in opciones for campo, opciones in personaje.GRUPOS.items())
        assert not (p["accesorio"] == "gorra" and p["arriba"] in personaje.SIN_GORRA)
        assert (p["nombre"], p["rasgos"], p["colores"]) == ("Bruno", ["Claro"], antes["colores"])  # solo las piezas
        vistos.add((p["cabeza"], p["arriba"], p["cuerpo"]))
    assert len(vistos) > 50
    otro = personaje.al_azar(antes, random.Random(1), colores=True)
    assert otro["colores"] != antes["colores"] and otro["arriba"] == antes["arriba"]
    assert tuple(otro["colores"].values()) in [v[1:] for v in personaje.PALETAS.values()]


def test_tinta_y_colores_de_marca():
    claro = personaje.limpio({})
    assert personaje.tinta(claro) == personaje.TINTA
    oscuro = personaje.limpio({"colores": {**claro["colores"], "cuerpo": "#16324F"}})
    assert personaje.tinta(oscuro) == personaje.TINTA_CLARA  # cara directa sobre cuerpo oscuro: ojos claros
    assert personaje.tinta({**oscuro, "cara": "capucha"}) == personaje.TINTA  # en capucha manda el color claro
    assert personaje.colores_marca({}) is None
    marca = {"principal": "#16324F", "secundario": "#3B82C4", "acento": "#9ED8CF", "fondo": "#F5F8FB"}
    assert personaje.colores_marca(marca) == {"cuerpo": "#16324F", "claro": "#F5F8FB", "acento": "#9ED8CF",
                                               "acento2": "#3B82C4"}
