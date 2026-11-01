"""Tu marca: cómo se ven las publicaciones, en cuatro pasos cortos y un resumen. Vive en el menú de
perfil (abajo en el menú lateral), junto a Perfil de empresa.

console.skandar.online/panel/contenido/marca?paso=N (1 a 4, o "resumen"):

1. Estilo visual: hasta 3 de 9 estilos en orden (principal, secundario y de apoyo), o «recomiéndame uno»
   según el giro de Perfil de empresa.
2. Colores: una paleta sugerida o los cuatro colores exactos, y cuánto color usar.
3. Tipografía: la letra de los títulos (las tarjetas usan la letra real).
4. Logotipo: el logo y cuánto se ve.

Cada paso es un formulario que guarda en kv "marca" y lleva al siguiente (sin JavaScript; panel.js
solo numera los estilos en el orden en que se tocan y llena los colores al elegir una paleta). Sin
"paso", abre el resumen si la marca ya está lista o el primer paso pendiente si no.

Las opciones y sus textos están en igbot/estilos.py. Lo que se elegía antes aquí y ya no se muestra
(nombre, frase y carácter, que pasan a Perfil de empresa; plantilla, modo y "Describe tu estilo",
que pasan a Crear campaña; recursos visuales y fotos del negocio, porque qué se ve se elige en cada
campaña) se conserva tal cual en kv "marca".
"""
import asyncio
import json
import logging
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import imagenes, marco
from .contenido import (N_HASHTAGS, archivo, estilos_de, guardar_fotos, hashtags_de, leer_hashtags, marca_de, n_hashtags_de,
                        png_logo, preparar_logo, sin_plan, url_imagen, usos_color)
from .cuenta import Cuenta
from .estilos import (COLORES, COMBINACION_INICIAL, COMBINACIONES, ESTILOS, LETRAS, LOGO_INICIAL, LOGO_PRESENCIA,
                      MAX_ESTILOS, MUESTRA_TEXTO, MUESTRA_TITULAR, PALETA_INICIAL, PALETAS, RECOMENDADOS,
                      RECOMENDADOS_INICIAL, ROLES, TARJETA_ESTILO, USO_COLOR, USO_INICIAL, USO_TARJETA)

log = logging.getLogger("igbot.marca")

router = APIRouter(prefix="/panel/contenido")
BASE = "/panel/contenido/marca"

PASOS = [  # (clave, nombre, de qué trata, título, bajada)
    ("estilo", "Estilo visual", "Cómo se ven tus diseños", "¿Cómo quieres que se vean los diseños de tu marca?",
     "Elige hasta 3 estilos. Te mostraremos cómo se ven para que puedas elegir los que mejor encajan con tu negocio."
     " La IA los utilizará como referencia al crear tus publicaciones."),
    ("colores", "Colores", "Tu paleta y cuánto color usar", "¿Qué colores son los de tu marca?",
     "Parte de una paleta sugerida o pon tus colores exactos. Después dinos cuánto color quieres en tus publicaciones."),
    ("tipografia", "Tipografía", "La letra de tus títulos y textos", "Una tipografía que encaje con tu marca.",
     "Según los estilos visuales que elegiste, hemos encontrado una combinación que encaja con tu identidad."),
    ("logotipo", "Logotipo", "Tu logo y cuánto se ve", "¿Cómo aparece tu logo?",
     "Sube tu logo y elige cuánto quieres que se vea. Lo usamos tal cual: nunca lo redibujamos."),
]
CLAVES = [p[0] for p in PASOS]
RESUMEN = "resumen"


# --- datos ---

def completa(marca: dict) -> bool:
    return set(CLAVES) <= hechos(marca)


def hechos(marca: dict) -> set[str]:
    """Los pasos ya completos. Una marca guardada con el formulario de antes cuenta lo que ya tenía."""
    pasos = set(marca.get("pasos", []))
    if estilos_de(marca):
        pasos.add("estilo")
    if imagenes.HEX.match(str(marca.get("principal", ""))):
        pasos.add("colores")
    if marca.get("fuente"):
        pasos.add("tipografia")
    if marca.get("logo"):
        pasos.add("logotipo")
    return pasos


def lista(marca: dict) -> bool:
    """Lista para crear campañas: al menos un estilo y sus colores (lo demás tiene valor de fábrica o es opcional)."""
    return {"estilo", "colores"} <= hechos(marca)


def colores_de(marca: dict) -> dict[str, str]:
    """Sus cuatro colores; los que falten, los de la paleta inicial (el secundario de antes no existía)."""
    fabrica = dict(zip((k for k, *_ in COLORES), PALETAS[PALETA_INICIAL][2]))
    return {k: marca[k].upper() if imagenes.HEX.match(str(marca.get(k, ""))) else fabrica[k] for k in fabrica}


def paleta_de(colores: dict[str, str]) -> str:
    """La paleta sugerida que coincide con sus colores, o "" si son propios."""
    valores = tuple(colores[k] for k, *_ in COLORES)
    return next((k for k, (_, _, cs) in PALETAS.items() if cs == valores), "")


def usos_de(marca: dict) -> list[str]:
    return usos_color(marca) or [USO_INICIAL]


def letra_de(marca: dict) -> str:
    return marca.get("fuente") if marca.get("fuente") in LETRAS else "Inter"


def combinacion(marca: dict) -> tuple[str, str, str]:
    """La tipografía recomendada (titulares, textos, por qué) según su estilo principal."""
    estilos = estilos_de(marca)
    return COMBINACIONES.get(estilos[0], COMBINACION_INICIAL) if estilos else COMBINACION_INICIAL


def letra_texto(marca: dict) -> str:
    """La letra de los textos: la elegida o, en una marca de antes, la de la combinación recomendada."""
    return marca["fuente_texto"] if marca.get("fuente_texto") in LETRAS else combinacion(marca)[1]


def logos_de(marca: dict) -> list[str]:
    """Cómo aparece el logo, en orden (se pueden elegir varios); una marca de antes tenía uno solo."""
    elegidos = [k for k in marca.get("logos_presencia", []) if k in LOGO_PRESENCIA]
    if not elegidos and marca.get("logo_presencia") in LOGO_PRESENCIA:
        elegidos = [marca["logo_presencia"]]
    return elegidos or [LOGO_INICIAL]


def color_elegido(f, k: str, antes: str) -> str:
    """El código escrito o el del selector: el que cambió (sin JavaScript no se mantienen iguales)."""
    validos = []
    for valor in (str(f.get(f"{k}_hex", "")).strip(), str(f.get(k, "")).strip()):
        valor = (valor if valor.startswith("#") else f"#{valor}").upper()
        if imagenes.HEX.match(valor):
            validos.append(valor)
    return next((v for v in validos if v != antes), validos[0] if validos else antes)


def en_orden(f, campo: str, orden: str, validos, maximo: int) -> list[str]:
    """Los marcados, en el orden en que los tocó (panel.js lo deja en `orden`); sin él, en el de la página."""
    marcados = [k for k in f.getlist(campo) if k in validos]
    tocados = [k for k in str(f.get(orden, "")).split(",") if k in marcados]
    return list(dict.fromkeys(tocados + marcados))[:maximo]


def estilos_del_form(f) -> list[str]:
    return en_orden(f, "estilos", "orden", ESTILOS, MAX_ESTILOS)


def marca_orden(n: int) -> str:
    """El número de orden de una tarjeta elegida (panel.js lo cambia al tocar)."""
    return f'<em class="wiz-orden"{"" if n else " hidden"}>{n or ""}</em>'


# --- piezas de la página ---

def imagen_tarjeta(clave: str, fondo: str) -> str:
    """La imagen de la tarjeta (igbot/static/tarjetas/marca/) o, mientras no exista, su color de respaldo."""
    for ext in ("webp", "png", "jpg"):
        if (marco.STATIC / "tarjetas" / "marca" / f"{clave}.{ext}").is_file():
            return f'<span class="wiz-img" style="--fondo:{fondo} url(/panel/static/tarjetas/marca/{clave}.{ext})"></span>'
    return f'<span class="wiz-img" style="--fondo:{fondo}"></span>'


def foto_o(clave: str, respaldo: str) -> str:
    """La imagen de la tarjeta si ya existe; si no, el dibujo de respaldo (la muestra con tus colores o tu logo)."""
    return imagen_tarjeta(clave, "#2a2a2d") if any(
        (marco.STATIC / "tarjetas" / "marca" / f"{clave}.{ext}").is_file() for ext in ("webp", "png", "jpg")) else respaldo


def etiquetas(lista_: tuple[str, ...]) -> str:
    return "".join(f"<em>{escape(e)}</em> " for e in lista_)


def menu_pasos(actual: str, hechos_: set[str]) -> str:
    items = ""
    for i, (k, nombre, *_) in enumerate(PASOS, 1):  # sin subtítulo en la barra de pasos
        clase = "actual" if k == actual else "hecho" if k in hechos_ else ""
        marca = "✓" if k in hechos_ and k != actual else str(i)
        items += (f'<a href="{BASE}?paso={i}" class="{clase}"><b>{marca}</b>'
                  f'<span>{nombre}</span></a>')
    clase = "actual" if actual == RESUMEN else ""
    items += (f'<a href="{BASE}?paso={RESUMEN}" class="{clase}"><b>★</b>'
              "<span>Resumen</span></a>")
    avance = len(hechos_ & set(CLAVES)) * 100 // len(CLAVES)
    return ('<details class="wiz-pasos" open><summary><span class="hamb"><i></i><i></i><i></i></span>Tu marca</summary>'
            f'<nav>{items}</nav>{marco.avance(avance)}<div class="mrc-tip"><b>Tú eliges, la IA diseña</b>Entra a cualquier paso cuando'
            " quieras. Lo que eliges aquí se aplica a todas tus campañas.</div></details>")


def cabeza(i: int) -> str:
    _, _, _, titulo, _ = PASOS[i - 1]  # la bajada ya no se muestra (sin subtítulos)
    return f'<div class="wiz-cab"><p class="ante">Paso {i} de {len(PASOS)}</p><h2>{titulo}</h2></div>'


def pie(i: int) -> str:
    anterior = f'<a class="boton secundario" href="{BASE}?paso={i - 1}">← Anterior</a>' if i > 1 else ""
    nombre = PASOS[i][1].lower() if i < len(PASOS) else "resumen"
    return marco.pie_config(anterior, f'<button class="boton mrc-sigue" name="accion" value="guardar">'
                                      f"Siguiente: {nombre} →</button>", "Puedes cambiarlo cuando quieras.")


def paso_estilo(marca: dict) -> str:
    elegidos = estilos_de(marca)
    tarjetas = ""
    for k, (nombre, desc, _) in ESTILOS.items():
        lema, sensacion, fondo = TARJETA_ESTILO[k]
        n = elegidos.index(k) + 1 if k in elegidos else 0
        tarjetas += (f'<label class="wiz-op"><input type="checkbox" name="estilos" value="{k}"{" checked" if n else ""}>'
                     f'<em class="wiz-orden"{"" if n else " hidden"}>{n or ""}</em>{imagen_tarjeta(k, fondo)}'
                     f'<span class="cuerpo"><b>{nombre}</b><span class="mrc-rol"{"" if n else " hidden"}>'
                     f'{f"{n}.º · {ROLES[n - 1]}" if n else ""}</span><i class="mrc-lema">{lema}</i>'
                     f'<small>{desc}</small><span class="mrc-sensa">Sensación: {" · ".join(sensacion)}</span>'
                     "</span></label>")
    tarjetas += ('<button class="wiz-op mrc-recomienda" name="accion" value="recomendar" formnovalidate>'
                 '<span class="ico">?</span><span class="cuerpo"><b>No estoy seguro, recomiéndame uno</b>'
                 "<small>Te sugerimos tres estilos según el giro de tu Perfil de empresa. Luego puedes cambiarlos.</small>"
                 "</span></button>")
    roles = "".join(f"<li><b>{i}.º · {r}:</b> {d}</li>" for i, (r, d) in enumerate(zip(ROLES, (
        "el que más debe notarse.", "complementa al principal.", "aporta variedad sin perder coherencia.")), 1))
    return (f'<input type="hidden" name="orden" value="{",".join(elegidos)}">'
            f'<h3 class="wiz-sub">Elige hasta {MAX_ESTILOS} estilos, en orden</h3>'
            f'<ul class="mrc-roles">{roles}</ul>'
            f'<div class="wiz-ops mrc-estilos" data-orden="orden" data-campo="estilos" data-max="{MAX_ESTILOS}"'
            f' data-roles="{",".join(ROLES)}">{tarjetas}</div>'
            '<p class="wiz-nota"><b>Cómo funciona:</b> toca los estilos en el orden que prefieras. Toca uno elegido para'
            " quitarlo.</p>")


def recomendados(giro: str) -> list[str]:
    return list(RECOMENDADOS.get(giro, RECOMENDADOS_INICIAL))[:MAX_ESTILOS]


def mini_post(uso: str) -> str:
    """Un post de muestra pintado con los colores de la página (panel.js los cambia al momento)."""
    return (f'<span class="mrc-post {uso}"><span class="mp"><span class="foto"></span>'
            '<span class="t">Tu título aquí</span><span class="l"></span></span></span>')


def paso_colores(marca: dict) -> str:
    colores = colores_de(marca)
    actual = paleta_de(colores)
    paletas = "".join(
        f'<label class="wiz-op"><input type="radio" name="paleta" value="{k}" data-colores="{",".join(cs)}"'
        f'{" checked" if k == actual else ""}><span class="mrc-paleta">'
        + "".join(f'<i style="background:{c}"></i>' for c in cs)
        + f'</span><span class="cuerpo"><b>{nombre}</b><small>{desc}</small></span></label>'
        for k, (nombre, desc, cs) in PALETAS.items())
    exactos = "".join(
        f'<div class="mrc-color"><input type="color" name="{k}" value="{colores[k].lower()}"'
        f' aria-label="Color {nombre.lower()}"><span><b>{nombre}</b><small>{uso}</small>'
        f'<input type="text" class="mrc-hex" name="{k}_hex" data-hex="{k}" value="{colores[k]}" maxlength="7"'
        f' spellcheck="false" autocomplete="off" aria-label="Código del color {nombre.lower()}"></span></div>'
        for k, nombre, uso in COLORES)
    elegidos = usos_de(marca)
    usos = "".join(
        f'<label class="wiz-op"><input type="checkbox" name="uso_color" value="{k}"{" checked" if k in elegidos else ""}>'
        + marca_orden(elegidos.index(k) + 1 if k in elegidos else 0)
        + ('<em class="mrc-reco">Recomendado</em>' if k == USO_INICIAL else "")
        + f'{foto_o(f"color-{k}", mini_post(k))}<span class="cuerpo"><b>{nombre}</b>'
        f'<i class="mrc-lema">{USO_TARJETA[k]}</i><small>{desc}</small></span></label>'
        for k, (nombre, desc, _) in USO_COLOR.items())
    return (f'<input type="hidden" name="paleta_antes" value="{actual}">'
            '<details class="mrc-otras"><summary>Elige una paleta</summary>'
            '<p class="ayuda">Cada paleta trae cuatro colores que combinan bien. Puedes ajustar cualquiera abajo.</p>'
            f'<div class="wiz-ops">{paletas}</div></details>'
            '<h3 class="wiz-sub">O usa tus colores exactos</h3>'
            '<p class="ayuda">Toca un color para cambiarlo o pega su código. Si tienes manual de marca, aquí van sus'
            " colores.</p>"
            f'<div class="mrc-colores">{exactos}</div>'
            '<h3 class="wiz-sub">¿Cuánto quieres que destaquen los colores de tu marca?</h3>'
            '<p class="ayuda">La misma paleta puede producir diseños muy diferentes. Elige cómo quieres que se utilicen'
            " tus colores en las publicaciones. Las muestras usan tus colores.</p>"
            f'<input type="hidden" name="orden_uso" value="{",".join(elegidos)}">'
            f'<div class="wiz-ops" data-orden="orden_uso" data-campo="uso_color" data-max="{len(USO_COLOR)}">{usos}</div>'
            '<p class="wiz-nota"><b>Puedes elegir varios,</b> en orden de preferencia: el primero es el que más usamos.'
            ' <b>Si dudas,</b> «Equilibrado» funciona para casi cualquier negocio. Elige «Natural»'
            " si prefieres que se vean los colores reales de tus productos y tu lugar.</p>")


def vista_par(titulo: str, texto: str, chica: bool = False) -> str:
    """El mismo titular y texto de muestra para todas: así se comparan solo las letras."""
    return (f'<span class="mrc-letra mrc-par{" chica" if chica else ""}">'
            f'<span class="h" style="font-family:\'{titulo}\';font-weight:{LETRAS[titulo][0]}">{MUESTRA_TITULAR}</span>'
            f'<span class="p" style="font-family:\'{texto}\';font-weight:400">{MUESTRA_TEXTO}</span></span>')


def paso_tipografia(marca: dict) -> str:
    titulo, texto, porque = combinacion(marca)
    actual = letra_de(marca) if marca.get("fuente") in LETRAS else ""
    otra = bool(actual) and (actual, letra_texto(marca)) != (titulo, texto)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="radio" name="fuente" value="{f}"{" checked" if f == actual and otra else ""}>'
        f'{vista_par(f, texto)}<span class="cuerpo"><b>{nombre}</b><small>{desc}</small>'
        f'<small class="mrc-fuente">{f} + {texto}</small></span></label>'
        for f, (_, nombre, desc, _, _) in LETRAS.items())
    usando = ('<span class="mrc-usando">✓ Es la que usas</span>' if actual and not otra else
              f'<span class="mrc-usando">Ahora usas {actual} + {letra_texto(marca)}</span>' if otra else "")
    return ('<div class="mrc-combi"><p class="ante">Recomendada para ti</p>'
            f'<h3>{titulo} + {texto}</h3><p class="ayuda">{porque}</p>'
            f'{vista_par(titulo, texto)}'
            f'<p class="mrc-combi-pie"><button class="boton" name="usar" value="1">✓ Usar esta combinación</button>{usando}</p>'
            "</div>"
            f'<details class="mrc-otras"><summary>Ver otras tipografías</summary>'
            '<p class="ayuda">La letra que elijas va en los titulares; los textos secundarios siguen con '
            f"{texto}. Todas muestran el mismo ejemplo para que compares.</p>"
            f'<div class="wiz-ops">{tarjetas}</div></details>')


def paso_logotipo(c: Cuenta, marca: dict) -> str:
    logo = marca.get("logo", "")
    url = url_imagen(c, logo) if logo and archivo(c, logo).is_file() else ""
    sello = (f'<img class="lg" src="{url}" alt="">' if url else '<span class="lg">TU LOGO</span>')
    elegidos = logos_de(marca)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="checkbox" name="logo_presencia" value="{k}"{" checked" if k in elegidos else ""}>'
        + marca_orden(elegidos.index(k) + 1 if k in elegidos else 0)
        + foto_o(f"logo-{k}", f'<span class="mrc-lienzo {k}">'
                 + ('<span class="cinta">Lanzamiento</span>' if k == "clave" else "")
                 + ("" if k == "sin" else sello) + "</span>")
        + f'<span class="cuerpo"><b>{nombre}</b><i class="mrc-lema">{lema}</i><small>{desc}</small></span></label>'
        for k, (nombre, lema, desc) in LOGO_PRESENCIA.items())
    subido = (f'<div class="mrc-logo"><img src="{url}" alt="Tu logo"><span><b>Este es tu logo</b>'
              '<small>Para cambiarlo, elige otro archivo abajo.</small></span>'
              '<button class="boton secundario chico" name="accion" value="quitar_logo" formnovalidate>Quitar</button></div>'
              if url else "")
    return ('<h3 class="wiz-sub">Tu logo <span class="suave">· opcional</span></h3>'
            '<p class="ayuda">PNG o JPG de hasta 10 MB. Si aún no lo tienes, puedes seguir y subirlo después.</p>'
            f'{subido}<label class="mrc-subir"><span class="ico">↑</span><span class="t"><b>Elige tu logo</b>'
            '<small>Lo conservamos tal cual: no lo redibujamos ni le cambiamos el color.</small></span>'
            '<input type="file" name="logo" accept="image/jpeg,image/png,image/webp"></label>'
            '<h3 class="wiz-sub">¿Cuánto quieres que se vea?</h3>'
            '<p class="ayuda">Elige su presencia en tus publicaciones. Puedes elegir varias, en orden de preferencia:'
            " la primera es la que más usamos.</p>"
            f'<input type="hidden" name="orden_logo" value="{",".join(elegidos)}">'
            f'<div class="wiz-ops mrc-logos" data-orden="orden_logo" data-campo="logo_presencia"'
            f' data-max="{len(LOGO_PRESENCIA)}">{tarjetas}</div>' + hashtags_html(marca))


def hashtags_html(marca: dict) -> str:
    """Tus hashtags (opcional): los de la marca, que van en cada post, y cuántos lleva cada post en total."""
    n = str(n_hashtags_de(marca))
    opciones = "".join(f'<label class="opc"><input type="radio" name="n_hashtags" value="{k}"{" checked" if k == n else ""}>{v}</label>'
                       for k, v in N_HASHTAGS.items())
    return ('<h3 class="wiz-sub">Tus hashtags <span class="suave">· opcional</span></h3>'
            '<p class="ayuda">Los de tu marca van en todos tus posts; el resto los elige la IA según cada post.</p>'
            '<label class="t" for="m-hashtags">Hashtags de tu marca</label>'
            f'<input type="text" id="m-hashtags" name="hashtags" maxlength="300" value="{escape(" ".join(hashtags_de(marca)))}"'
            ' placeholder="#tumarca #tuciudad">'
            f'<fieldset class="campo-o grupo-o"><legend class="t">¿Cuántos hashtags por post?</legend><div class="opcs">{opciones}</div></fieldset>')


def resumen(c: Cuenta, marca: dict) -> str:
    hechos_ = hechos(marca)

    def caja(i: int, titulo: str, desc: str, cuerpo: str, ancho: bool = False) -> str:
        k = CLAVES[i - 1]
        boton = "Editar" if k in hechos_ else "Completar"
        pendiente = "" if k in hechos_ else '<p class="ayuda">Aún no lo completas.</p>'
        return (f'<div class="wiz-res mrc-res{" ancho" if ancho else ""}">'
                f'{marco.cab_tarjeta(titulo, f"{BASE}?paso={i}", boton)}<p>{desc}</p>{pendiente or cuerpo}</div>')

    estilos = "".join(
        f'<div class="mrc-fila"><span class="num">{n}</span>{imagen_tarjeta(k, TARJETA_ESTILO[k][2])}'
        f"<span><b>{ESTILOS[k][0]}</b><small>{ROLES[n - 1]} · {TARJETA_ESTILO[k][0]}</small></span></div>"
        for n, k in enumerate(estilos_de(marca), 1))
    colores = colores_de(marca)
    paleta = paleta_de(colores)
    franja = ('<span class="mrc-paleta grande">' + "".join(f'<i style="background:{colores[k]}"></i>' for k, *_ in COLORES)
              + '</span><span class="mrc-codigos">'
              + "".join(f"<span><b>{n}</b>{colores[k]}</span>" for k, n, _ in COLORES) + "</span>")
    nombre_colores = f"{PALETAS[paleta][0] if paleta else 'Colores propios'} · {" + ".join(USO_COLOR[u][0] for u in usos_de(marca))}"
    fuente = letra_de(marca)
    logo = marca.get("logo", "")
    url = url_imagen(c, logo) if logo and archivo(c, logo).is_file() else ""
    logos = logos_de(marca)
    nombre_logo = " + ".join(LOGO_PRESENCIA[k][0] for k in logos)
    desc_logo = LOGO_PRESENCIA[logos[0]][1] if len(logos) == 1 else "Varía según la publicación; la primera es la que más usamos."
    logo_html = (f'<div class="mrc-fila">{f"<img class=mrc-logo-mini src={url} alt=>" if url else "<span class=mrc-logo-mini>Sin logo</span>"}'
                 f"<span><b>{nombre_logo}</b><small>{desc_logo}</small></span></div>"
                 f'<p class="mrc-hashtags"><b>Hashtags:</b> {N_HASHTAGS[str(n_hashtags_de(marca))].lower()} por post'
                 + (f" · {escape(' '.join(hashtags_de(marca)))}" if hashtags_de(marca) else "") + "</p>")
    return (
        '<div class="wiz-cab"><p class="ante">Resumen</p><h2>Así se verá tu contenido</h2></div>'
        '<div class="wiz-resumen">'
        + caja(1, "Estilo visual", "Tus estilos, en orden de preferencia.", estilos)
        + caja(2, "Colores", escape(nombre_colores), franja)
        + caja(3, "Tipografía", f"{fuente} + {letra_texto(marca)}", vista_par(fuente, letra_texto(marca), chica=True))
        + caja(4, "Logotipo", "Cómo aparece tu marca.", logo_html)
        + '</div><div class="wiz-pie"><span>Tu marca ya está guardada y se aplica sola en cada campaña nueva. El'
        " objetivo, los formatos y la variedad visual se eligen al crear cada campaña.</span>"
        '<a class="boton secundario" href="/panel/contenido/campanas/nueva">Crear una campaña</a></div>')


async def pagina_marca(c: Cuenta, paso: str, marca: dict | None = None, aviso: str = ""):
    marca = marca if marca is not None else await marca_de(c)
    hechos_ = hechos(marca)
    if paso == RESUMEN:
        actual, contenido = RESUMEN, resumen(c, marca)
    else:
        i = int(paso) if paso.isdigit() and 1 <= int(paso) <= len(PASOS) else 1
        actual = CLAVES[i - 1]
        hacer = {"estilo": lambda: paso_estilo(marca), "colores": lambda: paso_colores(marca),
                 "tipografia": lambda: paso_tipografia(marca), "logotipo": lambda: paso_logotipo(c, marca)}[actual]
        colores = colores_de(marca)
        estilo = ";".join(f"--m-{k}:{colores[k]}" for k, *_ in COLORES)
        contenido = (f'<form method="post" action="{BASE}" enctype="multipart/form-data" data-marca style="{estilo}">'
                     f'<input type="hidden" name="paso" value="{i}">'
                     # Enter en un campo de texto guarda y sigue (el primer botón del formulario).
                     '<button class="oculto" name="accion" value="guardar" tabindex="-1" aria-hidden="true">Guardar</button>'
                     + cabeza(i) + hacer()
                     + pie(i) + "</form>")
    # El aviso de lo que falta va dentro del paso, para que los pasos queden a la altura de las demás páginas.
    falta = await marco.pendiente(c, "marca")
    cuerpo = f'<div class="wiz mrc">{menu_pasos(actual, hechos_)}<div class="wiz-main">{falta}{contenido}</div></div>'
    # Tu marca es de la cuenta (Configuración, abajo en el menú), no una sección de Contenido.
    return marco.pagina(
        await marco.menu_de(c, "cuenta", sub="marca")
        + marco.encabezado("Tu cuenta · Tu marca")
        + '<main class="oscuro-pag"><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + cuerpo + "</div></main>")


# --- guardar ---

def guardar_paso(f, antes: dict, paso: str) -> tuple[dict, str]:
    """La marca con lo de este paso, y un aviso si algo no se puede guardar así."""
    datos = dict(antes)
    if paso == "estilo":
        estilos = estilos_del_form(f)
        if not estilos:
            return antes, "Elige al menos un estilo para seguir."
        datos["estilos"] = estilos
        datos.pop("estilo", None)  # el de antes (uno solo); "estilo" queda para lo que se cambia en un post
    elif paso == "colores":
        colores = colores_de(antes) if "colores" in hechos(antes) else {}
        paleta = str(f.get("paleta", ""))
        if paleta in PALETAS and paleta != f.get("paleta_antes"):  # sin JavaScript: la paleta que acaba de elegir
            datos.update(zip((k for k, *_ in COLORES), PALETAS[paleta][2]))
        else:
            for k, *_ in COLORES:
                datos[k] = color_elegido(f, k, colores.get(k, ""))
        usos = en_orden(f, "uso_color", "orden_uso", USO_COLOR, len(USO_COLOR)) or usos_de(antes)
        datos["usos_color"], datos["uso_color"] = usos, usos[0]  # uso_color: el principal, para lo de antes
    elif paso == "tipografia":
        titulo, texto, _ = combinacion(antes)
        # «Usar esta combinación», o seguir sin elegir otra: la recomendada.
        fuente = f.get("fuente") if f.get("fuente") in LETRAS and not f.get("usar") else titulo
        datos["fuente"], datos["peso"], datos["fuente_texto"] = fuente, LETRAS[fuente][0], texto
    elif paso == "logotipo":
        logos = en_orden(f, "logo_presencia", "orden_logo", LOGO_PRESENCIA, len(LOGO_PRESENCIA)) or logos_de(antes)
        datos["logos_presencia"], datos["logo_presencia"] = logos, logos[0]
        if "hashtags" in f:
            datos["hashtags"] = leer_hashtags(f.get("hashtags", ""))
        if f.get("n_hashtags") in N_HASHTAGS:
            datos["n_hashtags"] = f["n_hashtags"]
    datos["pasos"] = [k for k in CLAVES if k in set(antes.get("pasos", [])) | {paso}]
    return datos, ""


@router.get("/marca")
async def ver_marca(request: Request, c: marco.Sesion):
    if c.imagenes_mes <= 0:
        return sin_plan()
    paso = request.query_params.get("paso", "")
    if not paso:  # lista: el resumen; si no, el primer paso que falta
        marca = await marca_de(c)
        faltan = [i for i, k in enumerate(CLAVES, 1) if k not in hechos(marca)]
        paso = RESUMEN if lista(marca) or not faltan else str(faltan[0])
    return await pagina_marca(c, paso, aviso="Guardamos tus cambios." if request.query_params.get("guardado") else "")


@router.post("/marca")
async def guardar_marca(request: Request, c: marco.Formulario):
    if c.imagenes_mes <= 0:
        return sin_plan()
    f = await request.form()
    i = int(f.get("paso")) if str(f.get("paso", "")).isdigit() and 1 <= int(f.get("paso")) <= len(PASOS) else 1
    paso, accion = CLAVES[i - 1], str(f.get("accion", "guardar"))
    antes = await marca_de(c)
    if paso == "estilo" and accion == "recomendar":  # se marcan, sin guardar: los revisa y sigue
        from . import empresa  # import tardío: empresa importa contenido
        e = await empresa.de(c)
        giro = e.get("giro", "")
        elegidos = recomendados(giro)
        nombres = ", ".join(ESTILOS[k][0] for k in elegidos[:-1]) + f" y {ESTILOS[elegidos[-1]][0]}"
        por = (f"para un negocio de {giro.lower()}" if giro in RECOMENDADOS else
               "(llena el giro en tu Perfil de empresa para una recomendación a tu medida)")
        return await pagina_marca(c, "1", {**antes, "estilos": elegidos},
                                  f"Te recomendamos {nombres} {por}. Cámbialos si quieres y pulsa Aplicar.")
    datos, aviso = guardar_paso(f, antes, paso)
    if aviso:
        return await pagina_marca(c, str(i), aviso=aviso)

    # El logo (paso 4).
    if accion == "quitar_logo" and datos.get("logo"):
        png_logo(c, datos["logo"]).unlink(missing_ok=True)
        archivo(c, datos.pop("logo")).unlink(missing_ok=True)
        aviso = "Listo: quitamos tu logo."
    logo = f.get("logo")
    if getattr(logo, "filename", ""):
        tokens, _ = await guardar_fotos(c, [logo], 1)
        if tokens:
            if datos.get("logo"):
                archivo(c, datos["logo"]).unlink(missing_ok=True)
                png_logo(c, datos["logo"]).unlink(missing_ok=True)
            datos["logo"] = tokens[0]
            try:  # además del JPG (para verlo), el PNG con su transparencia: es el que va sobre las piezas
                await logo.seek(0)
                png_logo(c, tokens[0]).write_bytes(await asyncio.to_thread(preparar_logo, await logo.read()))
            except Exception:
                log.exception("No se pudo guardar el logo con transparencia")
        else:
            aviso = "No se pudo leer el logo (usa JPG o PNG de hasta 10 MB)."

    await c.store.set("marca", json.dumps(datos, ensure_ascii=False))
    log.info("@%s guardó Tu marca: %s", await marco.usuario(c), paso)
    if accion == "aplicar" and not aviso:
        aviso = "Cambios aplicados."
    if accion != "guardar" or aviso:  # Aplicar, o subió o quitó el logo: se queda en el paso para verlo
        return await pagina_marca(c, str(i), datos, aviso)
    return RedirectResponse(f"{BASE}?paso={i + 1 if i < len(PASOS) else RESUMEN}", status_code=303)
