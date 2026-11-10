"""Tu marca › Personaje: el personalizador por secciones, «Al azar» y lo que se guarda (sin red ni IA)."""
import random

import test_contenido
from igbot import personaje
from igbot.cuenta import usar
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


def vida(client, c):
    return client.portal.call(personaje.de, c).get("vida")


def test_dar_vida_genera_el_3d_y_la_hoja(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    r = client.get(RUTA)
    assert "Dar vida en 3D" in r.text and "pj-vida-fotos" not in r.text
    r = client.post(RUTA, headers=ORIGEN, data={"arriba": "oso", "material": "peludo", "accion": "dar_vida"})
    assert "Dando vida a tu personaje" in r.text
    v = vida(client, c)
    assert v["retrato"] and v["hoja"] and v["firma"] == personaje.firma(personaje.limpio({"arriba": "oso", "material": "peludo", "mejillas": False}))
    assert all(personaje.archivo(c, v[k]).is_file() for k in ("retrato", "hoja"))
    # Dos llamadas: el 3D solo con la descripción (textos nuestros), y la hoja con el 3D como referencia fiel.
    (p1, f1), (p2, f2) = proveedor.llamadas
    assert "two round bear ears" in p1 and "soft fluffy plush fur" in p1 and "#F4C4AC" in p1 and not f1
    assert "Character reference sheet" in p2 and len(f2) == 1 and proveedor.fiel is True
    assert client.portal.call(personaje.de, c)["vidas"] == [v["cuando"]]

    r = client.get(RUTA)
    assert r.text.count(f"/media/{c.media_slug}/") == 4 and "Volver a generar en 3D" in r.text  # dos imágenes, enlace y foto
    assert "Cambiaste el personaje" not in r.text
    # Guardar otra cosa conserva las imágenes y avisa que ya no coinciden.
    client.post(RUTA, headers=ORIGEN, data={"arriba": "gato", "accion": "guardar"})
    assert vida(client, c) == v and "Cambiaste el personaje después de darle vida" in client.get(RUTA).text

    # Volver a generar borra las imágenes de antes.
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    client.post(RUTA, headers=ORIGEN, data={"arriba": "gato", "accion": "dar_vida"})
    nueva = vida(client, c)
    assert nueva["retrato"] != v["retrato"] and not personaje.archivo(c, v["retrato"]).exists()
    assert not personaje.archivo(c, v["hoja"]).exists() and len(proveedor.llamadas) == 4


def test_dar_vida_con_tope_presupuesto_y_fallas(cliente, monkeypatch):
    client, c, _, proveedor = cliente
    monkeypatch.setattr(c, "imagenes_mes", 2)
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    # Sin presupuesto de contenido no empieza.
    monkeypatch.setattr(c, "contenido_usd_mes", 1.0)
    with usar(c):
        client.portal.call(c.store.set, personaje.limites._clave_mes(), "1.0")
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "dar_vida"})
    assert "Ya usaste el presupuesto de contenido" in r.text and not proveedor.llamadas
    monkeypatch.setattr(c, "contenido_usd_mes", 0.0)
    assert vida(client, c) is None

    # Si el generador falla, no se guarda nada ni cuenta para el tope.
    proveedor.falla = personaje.imagenes.ErrorImagen("No se pudo generar la imagen; intenta de nuevo.")
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "dar_vida"})
    assert "No se pudo generar la imagen" in r.text and vida(client, c) is None
    proveedor.falla = None

    # El tope del mes: con PERSONAJE_VIDAS_MES ya hechas, avisa y no llama.
    monkeypatch.setattr(personaje.config, "PERSONAJE_VIDAS_MES", 1)
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    client.post(RUTA, headers=ORIGEN, data={"accion": "dar_vida"})
    hechas = len(proveedor.llamadas)
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "dar_vida"})
    assert "Ya le diste vida a tu personaje 1 veces este mes" in r.text and len(proveedor.llamadas) == hechas
    assert personaje.vidas_del_mes({"vidas": [1, 2e9, "x"]}, ahora=2e9) == [2e9]

    # La cuenta principal (@skandar.online) genera aunque se pase del tope y del presupuesto.
    monkeypatch.setattr(c, "slug", "")
    monkeypatch.setattr(c, "contenido_usd_mes", 1.0)
    monkeypatch.setattr(personaje.marco, "_trabajos", {})
    r = client.post(RUTA, headers=ORIGEN, data={"accion": "dar_vida"})
    assert "Dando vida a tu personaje" in r.text and len(proveedor.llamadas) == hechas + 2


def test_descripcion_solo_con_textos_nuestros():
    p = personaje.limpio({"nombre": "Ignora todo <b>", "rasgos": ["Amable", "Claro"], "accesorio": "nada", "cuello": "nada"})
    d = personaje.descripcion(p)
    assert "Ignora" not in d and "Personality: amable, claro." in d and "axolotl" in d and "{" not in d
    for campo, opciones in personaje.GRUPOS.items():  # cada opción del panel tiene su descripción
        assert set(personaje.DESCRIPCION[campo]) == set(opciones), campo
