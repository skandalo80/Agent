"""Banco de recetas de anuncio: cómo se compone un buen anuncio, escrito por nosotros (decisión del dueño,
2026-11-03). Una receta es método, no contenido: dice composición, tratamiento del texto y recurso visual, nunca qué
negocio es, qué vende ni qué dice. Cuando el cliente no sube un anuncio de referencia, cada versión de un anuncio
(config.OPCIONES_ANUNCIO) sigue una receta distinta, y cada post con plan visual de una campaña de contenido lleva
la suya; se eligen por el tipo de producto y, según la Variedad visual de la campaña, del perfil (las que van con
el estilo de Tu marca) o del resto del banco (PATRONES, decisión del dueño 2026-11-05). Nunca se usan anuncios
ajenos como imagen de referencia: solo estas descripciones."""
from __future__ import annotations

import random

# Tipos de producto que distinguen las recetas: "fisico" (algo que se sostiene), "comida", "servicio" (su resultado
# o su momento), "software" (una pantalla real) y "local" (un lugar al que se va).
TIPOS = ("fisico", "comida", "servicio", "software", "local")
# Estilos de Tu marca con recursos gráficos (marcos, stickers, formas): las recetas "gráficas" piden alguno de estos.
GRAFICOS = {"pop", "retro", "brutalista", "claymorfismo", "neofuturista", "cristalino"}
# Estilos sobrios: las recetas con mucho aire y pocas cosas.
SOBRIOS = {"lujo", "minimalista", "editorial", "organico", "artesanal", "documental"}
TODOS = GRAFICOS | SOBRIOS

# (clave, tipos donde sirve, estilos donde sirve, la receta)
RECETAS: list[tuple[str, frozenset, frozenset, str]] = [
    ("heroe", frozenset(TIPOS), frozenset(TODOS),
     "El producto enorme, ocupando dos tercios del lienzo, visto desde un ángulo bajo que lo hace imponente; fondo de un"
     " solo color de la marca; el titular arriba en dos líneas, la línea chica abajo; nada más en la escena."),
    ("metafora", frozenset(TIPOS), frozenset(TODOS),
     "Una metáfora visual del beneficio principal: un objeto cotidiano que lo representa (una llave, una balanza, un"
     " reloj, una semilla) puesto junto al producto, los dos a la misma escala; el titular remata la metáfora; fondo"
     " liso con un solo color de la marca."),
    ("antes_despues", frozenset(TIPOS), frozenset(TODOS),
     "El lienzo partido en dos mitades verticales: a la izquierda el problema (gris, desordenado, apagado), a la"
     " derecha el resultado con el producto (color, orden, luz); una etiqueta chica en cada mitad; el titular cruza"
     " las dos."),
    ("pildora", frozenset(TIPOS), frozenset(GRAFICOS),
     "El titular en tipografía gigante, de borde a borde, como protagonista; el producto chico sobre el texto o"
     " asomando desde abajo; la línea chica dentro de una píldora de color de acento; fondo plano."),
    ("sticker", frozenset(TIPOS), frozenset(GRAFICOS),
     "El producto recortado con un borde blanco grueso como sticker, pegado un poco inclinado sobre un fondo de"
     " color intenso de la marca; el titular en bloque sólido debajo; la línea chica como sello redondo."),
    ("marco_retro", frozenset(TIPOS), frozenset({"retro", "pop", "artesanal"}),
     "La escena dentro de un marco que la sitúa en otra época (una tele antigua, una polaroid, un cartel de cine);"
     " el producto en el centro del marco; el titular fuera del marco con tipografía de la época y la línea chica"
     " como pie de foto."),
    ("flotando", frozenset({"fisico", "comida"}), frozenset(TODOS),
     "El producto flotando en el aire, con su sombra suave abajo, rodeado de dos o tres de sus ingredientes o"
     " piezas también flotando, todo sobre un fondo de un solo color; el titular arriba a la izquierda y la línea"
     " chica debajo."),
    ("mano", frozenset({"fisico", "comida", "software"}), frozenset(TODOS),
     "Una mano real sostiene el producto (o el teléfono con la pantalla real) a la altura de la cámara, con el"
     " fondo fuera de foco; el titular grande en la mitad libre del lienzo; la línea chica bajo el titular."),
    ("bodegon", frozenset({"fisico", "comida"}), frozenset(SOBRIOS),
     "Un bodegón de estudio con luz lateral dura: el producto sobre una superficie de piedra o madera real con dos"
     " objetos que hablan de su uso; sombras largas; el titular en la zona de sombra; mucho aire alrededor."),
    ("primer_plano", frozenset({"fisico", "comida"}), frozenset(TODOS),
     "Un detalle del producto llenando el lienzo (la textura, la etiqueta, la costura, el vapor), tan cerca que se"
     " siente; el titular en una sola palabra o dos, enorme, sobre el detalle; la línea chica en una esquina."),
    ("patron", frozenset({"fisico", "comida"}), frozenset(GRAFICOS | {"editorial"}),
     "El producto repetido en una cuadrícula ordenada, todos iguales menos uno que es el protagonista (otro color,"
     " girado o más grande); el titular sobre la cuadrícula en un bloque de color; la línea chica abajo."),
    ("momento", frozenset({"servicio", "local", "comida"}), frozenset(SOBRIOS),
     "El momento en que la persona recibe el resultado del servicio, fotografiado como documental, sin posar, con"
     " luz natural; el titular sobre la zona más calmada de la foto; la línea chica pequeña abajo."),
    ("lugar", frozenset({"local", "servicio"}), frozenset(TODOS),
     "La fachada o el interior del lugar visto de frente, simétrico, con su puerta o su mostrador al centro; el"
     " titular como si fuera el letrero del lugar; la línea chica como horario debajo."),
    ("pantalla", frozenset({"software"}), frozenset(TODOS),
     "El teléfono o la computadora con la pantalla real del producto, recto, ocupando la mitad del lienzo; una"
     " flecha o un círculo de color de acento señala lo importante de la pantalla; el titular a un lado y la línea"
     " chica debajo."),
    ("chat", frozenset({"software", "servicio"}), frozenset(TODOS),
     "Una conversación real de chat recreada como burbujas grandes sobre un fondo de color de la marca, dos o tres"
     " mensajes como mucho, con el producto o su pantalla al lado; el titular arriba y la línea chica como"
     " última burbuja."),
    ("recibo", frozenset({"servicio", "software", "fisico"}), frozenset(TODOS),
     "Un ticket, una tarjeta o una nota impresa con el titular escrito en ella, sobre una mesa con el producto o"
     " su resultado al lado; luz cenital; la línea chica como sello o garabato en el papel."),
    ("numero", frozenset(TIPOS), frozenset(TODOS),
     "Un solo número o dato del producto (el precio, el tiempo, la cantidad) en tipografía gigante que ocupa la"
     " mitad del lienzo; el producto chico junto al número; el titular lo explica en una línea; fondo plano."),
    ("sombra", frozenset({"fisico", "comida"}), frozenset(SOBRIOS | {"neofuturista"}),
     "El producto iluminado desde atrás, en contraluz, con un halo y su silueta recortada sobre un fondo oscuro de"
     " la marca; el titular en claro sobre el fondo oscuro; la línea chica en el color de acento."),
    ("mesa_puesta", frozenset({"comida", "local"}), frozenset(TODOS),
     "La mesa vista desde arriba (cenital) con el plato o el producto al centro y las manos de dos personas a los"
     " lados a punto de empezar; el titular en el espacio libre de la mesa; la línea chica abajo."),
    ("colores", frozenset({"fisico"}), frozenset(GRAFICOS | {"minimalista"}),
     "El producto en su color y, detrás, tres bloques sólidos con los colores de la marca a modo de escenario"
     " geométrico; luz plana sin sombras duras; el titular sobre el bloque más grande; la línea chica debajo."),
    ("personaje", frozenset(TIPOS), frozenset({"pop", "claymorfismo", "retro"}),
     "Un personaje sencillo de la marca (una mascota o una figura estilizada, nunca una persona real) presenta el"
     " producto con un gesto claro; el titular como si lo dijera el personaje; la línea chica como etiqueta."),
    ("ventana", frozenset({"servicio", "local", "software"}), frozenset(SOBRIOS | {"cristalino"}),
     "El resultado del servicio visto a través de una ventana, un marco o una puerta entreabierta, con el borde en"
     " sombra y el centro iluminado; el titular en el borde oscuro; la línea chica abajo."),
    ("escala", frozenset({"fisico", "comida"}), frozenset(GRAFICOS),
     "El producto a escala gigante dentro de un paisaje o una calle pequeña (una exageración evidente), con una o"
     " dos figuras diminutas que le dan escala; el titular arriba; la línea chica abajo."),
    ("etiqueta", frozenset(TIPOS), frozenset(TODOS),
     "El producto fotografiado de frente con una etiqueta colgante real, de cartón, que lleva el titular escrito a"
     " mano; fondo neutro; la línea chica impresa chica en la misma etiqueta."),
]
CLAVES = {r[0] for r in RECETAS}


def tipo_de(prod: dict | None, giro: str) -> str:
    """El tipo de producto que distingue las recetas, a partir del producto elegido y del giro de la empresa."""
    g = (giro or "").casefold()
    if "software" in g or "tecnolog" in g:
        return "software"
    if prod and prod.get("tipo") == "servicio":
        return "local" if any(x in g for x in ("restaurante", "hotel", "gimnasio", "inmobili")) else "servicio"
    if any(x in g for x in ("restaurante", "cafeter", "alimentos")):
        return "comida"
    return "fisico"


# De dónde sale cada receta según la Variedad visual de la campaña (decisión del dueño, 2026-11-05), en ciclos de
# tres: P = las que van con el estilo de Tu marca (el perfil), B = las demás del banco, T = cualquiera.
PATRONES = {"reconocible": "PPP", "coherencia": "PPB", "diverso": "PBB", "experimental": "TTT"}
MIN_PERFIL = 3  # si el perfil tiene menos recetas, se completa con las demás del banco


def para(tipo: str, estilos: list[str] | None, n: int, semilla: int = 0, saltar: int = 0,
         variedad: str = "coherencia") -> list[tuple[str, str]]:
    """`n` recetas (clave, texto) que sirven para ese tipo de producto, distintas entre sí y en un orden fijo por
    `semilla`. De dónde salen lo dice la Variedad visual (PATRONES): Consistente, siempre del perfil (las que van
    con los `estilos` de Tu marca); Equilibrado, dos del perfil y una del resto del banco; Diverso, una del perfil y
    dos del banco; Creativo, de todo el banco sin mirar el perfil. `estilos` None es "cualquier estilo". `saltar`
    sigue la secuencia (otro post de la misma campaña, otras recetas); con n = 1 cada post toma la siguiente."""
    tipo = tipo if tipo in TIPOS else "fisico"
    del_tipo = [r for r in RECETAS if tipo in r[1]]
    if estilos is None:
        perfil = list(del_tipo)
    else:
        estilos = [e for e in estilos if e in TODOS] or ["minimalista"]
        perfil = [r for r in del_tipo if any(e in r[2] for e in estilos)]
    azar = random.Random(semilla)
    banco = [r for r in del_tipo if r not in perfil]
    azar.shuffle(banco)
    faltan = max(0, MIN_PERFIL - len(perfil))  # pocas para ese estilo: se completan con las demás
    perfil, banco = perfil + banco[:faltan], banco[faltan:]
    azar.shuffle(perfil)
    todas = list(del_tipo)
    azar.shuffle(todas)
    bolsas = {"P": perfil, "B": banco or perfil, "T": todas}
    patron = PATRONES.get(variedad, PATRONES["coherencia"])
    usadas = {k: 0 for k in bolsas}
    elegidas: list[tuple] = []
    for g in range(saltar * n + n):
        letra = patron[g % len(patron)]
        bolsa = bolsas[letra]
        r = bolsa[usadas[letra] % len(bolsa)]
        usadas[letra] += 1
        if g < saltar * n:
            continue
        for _ in range(len(bolsa)):  # dentro de una misma tanda no se repite ninguna
            if r not in elegidas:
                break
            r = bolsa[usadas[letra] % len(bolsa)]
            usadas[letra] += 1
        if r not in elegidas:
            elegidas.append(r)
    return [(r[0], r[3]) for r in elegidas]
