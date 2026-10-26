"""Tu marca: cómo se ven las publicaciones, en cinco pasos cortos y un resumen.

console.skandar.online/panel/contenido/marca?paso=N (1 a 5, o "resumen"):

1. Estilo visual: hasta 3 estilos en orden de preferencia (el primero manda en la imagen).
2. Colores: una paleta sugerida o los cuatro colores exactos, y cuánto color usar.
3. Tipografía: la letra de los títulos (las tarjetas usan la letra real).
4. Logotipo: el logo y cuánto se ve.
5. Recursos visuales: qué mostrar y las fotos reales del negocio.

Cada paso es un formulario que guarda en kv "marca" y lleva al siguiente (sin JavaScript; panel.js
solo numera los estilos en el orden en que se tocan y llena los colores al elegir una paleta). Sin
"paso", abre el resumen si la marca ya está lista o el primer paso pendiente si no.

Las opciones y sus textos están en igbot/estilos.py. Lo que se elegía antes aquí y ya no se muestra
(nombre, frase y carácter, que pasan a Perfil de empresa; plantilla, modo y "Describe tu estilo",
que pasan a Crear campaña) se conserva tal cual en kv "marca".
"""
import json
import logging
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import imagenes, panel
from .contenido import (MAX_FOTOS, archivo, estilos_de, guardar_fotos, marca_de, pagina, sin_plan, url_imagen)
from .cuenta import Cuenta
from .estilos import (COLORES, ESTILOS, LETRAS, LOGO_INICIAL, LOGO_PRESENCIA, MAX_ESTILOS, PALETA_INICIAL, PALETAS,
                      RECURSOS, TARJETA_ESTILO, USO_COLOR, USO_INICIAL)

log = logging.getLogger("igbot.marca")

router = APIRouter(prefix="/panel/contenido")
BASE = "/panel/contenido/marca"

PASOS = [  # (clave, nombre, de qué trata, título, bajada)
    ("estilo", "Estilo visual", "La sensación de tus imágenes", "¿Qué estilo visual representa a tu marca?",
     "Elige hasta tres estilos, en orden de preferencia. El primero es el que más se notará en tus imágenes."),
    ("colores", "Colores", "Tu paleta y cuánto color usar", "¿Qué colores son los de tu marca?",
     "Parte de una paleta sugerida o pon tus colores exactos. Después dinos cuánto color quieres en tus publicaciones."),
    ("tipografia", "Tipografía", "La letra de tus títulos", "¿Qué letra va con tu marca?",
     "Es la letra de los títulos en tus imágenes. Cada tarjeta muestra la letra real, tal como saldrá."),
    ("logotipo", "Logotipo", "Tu logo y cuánto se ve", "¿Cómo aparece tu logo?",
     "Sube tu logo y elige cuánto quieres que se vea. Lo usamos tal cual: nunca lo redibujamos."),
    ("recursos", "Recursos visuales", "Qué mostrar y tus fotos", "¿Qué quieres mostrar en tus publicaciones?",
     "Elige todo lo que aplique a tu negocio y sube fotos reales. Así las imágenes muestran lo que de verdad vendes."),
]
CLAVES = [p[0] for p in PASOS]
RESUMEN = "resumen"


# --- datos ---

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
    if marca.get("recursos") or marca.get("fotos"):
        pasos.add("recursos")
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


def uso_de(marca: dict) -> str:
    return marca.get("uso_color") if marca.get("uso_color") in USO_COLOR else USO_INICIAL


def letra_de(marca: dict) -> str:
    return marca.get("fuente") if marca.get("fuente") in LETRAS else "Inter"


def logo_de(marca: dict) -> str:
    return marca.get("logo_presencia") if marca.get("logo_presencia") in LOGO_PRESENCIA else LOGO_INICIAL


def recursos_de(marca: dict) -> list[str]:
    return [k for k in marca.get("recursos", []) if k in RECURSOS]


def color_elegido(f, k: str, antes: str) -> str:
    """El código escrito o el del selector: el que cambió (sin JavaScript no se mantienen iguales)."""
    validos = []
    for valor in (str(f.get(f"{k}_hex", "")).strip(), str(f.get(k, "")).strip()):
        valor = (valor if valor.startswith("#") else f"#{valor}").upper()
        if imagenes.HEX.match(valor):
            validos.append(valor)
    return next((v for v in validos if v != antes), validos[0] if validos else antes)


def estilos_del_form(f) -> list[str]:
    """Los marcados, en el orden en que los tocó (panel.js lo deja en "orden"); sin él, en el de la página."""
    marcados = [k for k in f.getlist("estilos") if k in ESTILOS]
    orden = [k for k in str(f.get("orden", "")).split(",") if k in marcados]
    return list(dict.fromkeys(orden + marcados))[:MAX_ESTILOS]


# --- piezas de la página ---

def imagen_tarjeta(clave: str, fondo: str) -> str:
    """La imagen de la tarjeta (igbot/static/tarjetas/marca/) o, mientras no exista, su color de respaldo."""
    for ext in ("webp", "png", "jpg"):
        if (panel.STATIC / "tarjetas" / "marca" / f"{clave}.{ext}").is_file():
            return f'<span class="wiz-img" style="--fondo:{fondo} url(/panel/static/tarjetas/marca/{clave}.{ext})"></span>'
    return f'<span class="wiz-img" style="--fondo:{fondo}"></span>'


def etiquetas(lista_: tuple[str, ...]) -> str:
    return "".join(f"<em>{escape(e)}</em> " for e in lista_)


def menu_pasos(actual: str, hechos_: set[str]) -> str:
    items = ""
    for i, (k, nombre, desc, *_) in enumerate(PASOS, 1):
        clase = "actual" if k == actual else "hecho" if k in hechos_ else ""
        marca = "✓" if k in hechos_ and k != actual else str(i)
        items += (f'<a href="{BASE}?paso={i}" class="{clase}"><b>{marca}</b>'
                  f'<span>{nombre}<small>{desc}</small></span></a>')
    clase = "actual" if actual == RESUMEN else ""
    items += (f'<div class="sep"></div><a href="{BASE}?paso={RESUMEN}" class="{clase}"><b>★</b>'
              "<span>Resumen<small>Revisa y edita</small></span></a>")
    return ('<details class="wiz-pasos" open><summary><span class="hamb"><i></i><i></i><i></i></span>Tu marca</summary>'
            f'<nav>{items}</nav><div class="mrc-tip"><b>Tú eliges, la IA diseña</b>Entra a cualquier paso cuando'
            " quieras. Lo que eliges aquí se aplica a todas tus campañas.</div></details>")


def cabeza(i: int) -> str:
    _, _, _, titulo, bajada = PASOS[i - 1]
    return (f'<div class="wiz-cab"><p class="ante">Paso {i} de {len(PASOS)}</p><h2>{titulo}</h2>'
            f'<p class="baj">{bajada}</p></div><div class="wiz-barra"><i style="width:{i * 100 // len(PASOS)}%"></i></div>')


def pie(i: int, siguiente: str = "Guardar y continuar →") -> str:
    anterior = f'<a class="boton secundario" href="{BASE}?paso={i - 1}">← Anterior</a> ' if i > 1 else ""
    return (f'<div class="wiz-pie"><span>Puedes cambiarlo cuando quieras.</span><span class="mrc-botones">{anterior}'
            f'<button class="boton mrc-sigue" name="accion" value="guardar">{siguiente}</button></span></div>')


def paso_estilo(marca: dict) -> str:
    elegidos = estilos_de(marca)
    tarjetas = ""
    for k, (nombre, desc, _) in ESTILOS.items():
        tags, fondo = TARJETA_ESTILO[k]
        n = elegidos.index(k) + 1 if k in elegidos else 0
        tarjetas += (f'<label class="wiz-op"><input type="checkbox" name="estilos" value="{k}"{" checked" if n else ""}>'
                     f'<em class="wiz-orden"{"" if n else " hidden"}>{n or ""}</em>{imagen_tarjeta(k, fondo)}'
                     f'<span class="cuerpo"><b>{nombre}</b><small>{desc}</small>{etiquetas(tags)}</span></label>')
    return (f'<input type="hidden" name="orden" value="{",".join(elegidos)}">'
            f'<h3 class="wiz-sub">Elige hasta {MAX_ESTILOS} estilos</h3>'
            '<p class="ayuda">Cada tarjeta resume luz, composición, textura y sensación. El número es tu orden de'
            " preferencia.</p>"
            f'<div class="wiz-ops mrc-estilos" data-max="{MAX_ESTILOS}">{tarjetas}</div>'
            '<p class="wiz-nota"><b>Cómo funciona:</b> el primer estilo marca la pauta de tus imágenes; el segundo y'
            " el tercero aportan matices. Toca un estilo elegido para quitarlo.</p>")


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
    usos = "".join(
        f'<label class="wiz-op"><input type="radio" name="uso_color" value="{k}"{" checked" if k == uso_de(marca) else ""}>'
        + ('<em class="mrc-reco">Recomendado</em>' if k == USO_INICIAL else "")
        + f'{mini_post(k)}<span class="cuerpo"><b>{nombre}</b><small>{desc}</small></span></label>'
        for k, (nombre, desc, _) in USO_COLOR.items())
    return (f'<input type="hidden" name="paleta_antes" value="{actual}">'
            '<h3 class="wiz-sub">Elige una paleta</h3>'
            '<p class="ayuda">Cada paleta trae cuatro colores que combinan bien. Puedes ajustar cualquiera abajo.</p>'
            f'<div class="wiz-ops">{paletas}</div>'
            '<h3 class="wiz-sub">O usa tus colores exactos</h3>'
            '<p class="ayuda">Toca un color para cambiarlo o pega su código. Si tienes manual de marca, aquí van sus'
            " colores.</p>"
            f'<div class="mrc-colores">{exactos}</div>'
            '<h3 class="wiz-sub">¿Cuánto color quieres en tus posts?</h3>'
            '<p class="ayuda">Así se reparte tu paleta en cada imagen. Las muestras usan tus colores.</p>'
            f'<div class="wiz-ops">{usos}</div>'
            '<p class="wiz-nota"><b>Regla de oro:</b> el acento se usa poco para que destaque. Si dudas,'
            " «Equilibrado» funciona para casi cualquier negocio.</p>")


def muestra_letra(fuente: str, chica: bool = False) -> str:
    peso, _, _, ante, titulo = LETRAS[fuente]
    return (f'<span class="mrc-letra{" chica" if chica else ""}"><span class="k">{ante}</span>'
            f'<span class="h" style="font-family:\'{fuente}\';font-weight:{peso}">{titulo}</span></span>')


def paso_tipografia(marca: dict) -> str:
    actual = letra_de(marca)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="radio" name="fuente" value="{f}"{" checked" if f == actual else ""}>'
        f'{muestra_letra(f)}<span class="cuerpo"><b>{nombre}</b><small>{desc}</small>'
        f'<small class="mrc-fuente">{f}</small></span></label>'
        for f, (_, nombre, desc, _, _) in LETRAS.items())
    return ('<h3 class="wiz-sub">Elige una letra para tus títulos</h3>'
            '<p class="ayuda">No necesitas saber nombres de fuentes: elige la que se sienta como tu marca.</p>'
            f'<div class="wiz-ops">{tarjetas}</div>'
            '<p class="wiz-nota"><b>Lo que ves es lo que sale:</b> estas tarjetas usan la letra real. En los textos'
            " largos usamos una letra sencilla que combine.</p>")


def paso_logotipo(c: Cuenta, marca: dict) -> str:
    logo = marca.get("logo", "")
    url = url_imagen(c, logo) if logo and archivo(c, logo).is_file() else ""
    sello = (f'<img class="lg" src="{url}" alt="">' if url else '<span class="lg">TU LOGO</span>')
    actual = logo_de(marca)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="radio" name="logo_presencia" value="{k}"{" checked" if k == actual else ""}>'
        f'<span class="mrc-lienzo {k}">'
        + ('<span class="cinta">Lanzamiento</span>' if k == "clave" else "")
        + f'{sello}</span><span class="cuerpo"><b>{nombre}</b><small>{desc}</small></span></label>'
        for k, (nombre, desc) in LOGO_PRESENCIA.items())
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
            '<p class="ayuda">Elige su presencia en tus publicaciones.</p>'
            f'<div class="wiz-ops">{tarjetas}</div>')


def paso_recursos(c: Cuenta, marca: dict) -> str:
    elegidos = recursos_de(marca)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="checkbox" name="recursos" value="{k}"{" checked" if k in elegidos else ""}>'
        f'{imagen_tarjeta(k, fondo)}<span class="cuerpo"><b>{nombre}</b><small>{desc}</small>{etiquetas(tags)}</span></label>'
        for k, (nombre, desc, tags, fondo, _) in RECURSOS.items())
    fotos = "".join(
        f'<figure><img src="{url_imagen(c, t)}" alt="Foto {i + 1}">'
        f'<button class="quitar" name="accion" value="quitar_{i}" formnovalidate>Quitar</button></figure>'
        for i, t in enumerate(marca.get("fotos", [])) if archivo(c, t).is_file())
    sube = len(marca.get("fotos", [])) < MAX_FOTOS
    return ('<h3 class="wiz-sub">Elige lo que aplica a tu negocio</h3>'
            '<p class="ayuda">Puedes marcar varias. Entre más precisa tu selección, mejor orientamos cada imagen.</p>'
            f'<div class="wiz-ops">{tarjetas}</div>'
            '<h3 class="wiz-sub" id="fotos">Fotos reales de tu negocio</h3>'
            f'<p class="ayuda">Tu local, tu equipo o tus productos, hasta {MAX_FOTOS}. Las usamos como base para que en'
            " tus posts salga lo real.</p>"
            + (f'<div class="fotos">{fotos}</div>' if fotos else "")
            + ('<label class="mrc-subir"><span class="ico">+</span><span class="t"><b>Sube tus mejores fotos</b>'
               '<small>JPG o PNG, hasta 10 MB cada una.</small></span>'
               '<input type="file" name="fotos" accept="image/jpeg,image/png,image/webp" multiple></label>'
               '<div class="acciones"><button class="boton secundario" name="accion" value="subir" formnovalidate>'
               "Subir fotos</button></div>" if sube else "")
            + f'<label class="check"><input type="checkbox" name="fotos_estilo" value="1"'
            f'{" checked" if marca.get("fotos_estilo", True) else ""}> Copiar su estilo (luz, colores y fondos) en todos'
            " mis posts</label>")


def resumen(c: Cuenta, marca: dict) -> str:
    hechos_ = hechos(marca)

    def caja(i: int, titulo: str, desc: str, cuerpo: str, ancho: bool = False) -> str:
        k = CLAVES[i - 1]
        boton = "Editar" if k in hechos_ else "Completar"
        pendiente = "" if k in hechos_ else '<p class="ayuda">Aún no lo completas.</p>'
        return (f'<div class="wiz-res mrc-res{" ancho" if ancho else ""}"><h4>{i:02d} · {titulo}</h4>'
                f'<p>{desc}</p><a href="{BASE}?paso={i}">{boton}</a>{pendiente or cuerpo}</div>')

    estilos = "".join(
        f'<div class="mrc-fila"><span class="num">{n}</span>{imagen_tarjeta(k, TARJETA_ESTILO[k][1])}'
        f"<span><b>{ESTILOS[k][0]}</b><small>{ESTILOS[k][1]}</small></span></div>"
        for n, k in enumerate(estilos_de(marca), 1))
    colores = colores_de(marca)
    paleta = paleta_de(colores)
    franja = ('<span class="mrc-paleta grande">' + "".join(f'<i style="background:{colores[k]}"></i>' for k, *_ in COLORES)
              + '</span><span class="mrc-codigos">'
              + "".join(f"<span><b>{n}</b>{colores[k]}</span>" for k, n, _ in COLORES) + "</span>")
    nombre_colores = f"{PALETAS[paleta][0] if paleta else 'Colores propios'} · {USO_COLOR[uso_de(marca)][0]}"
    fuente = letra_de(marca)
    logo = marca.get("logo", "")
    url = url_imagen(c, logo) if logo and archivo(c, logo).is_file() else ""
    nombre_logo, desc_logo = LOGO_PRESENCIA[logo_de(marca)]
    logo_html = (f'<div class="mrc-fila">{f"<img class=mrc-logo-mini src={url} alt=>" if url else "<span class=mrc-logo-mini>Sin logo</span>"}'
                 f"<span><b>{nombre_logo}</b><small>{desc_logo}</small></span></div>")
    n_fotos = sum(1 for t in marca.get("fotos", []) if archivo(c, t).is_file())
    chips = "".join(f"<span>{RECURSOS[k][0]}</span>" for k in recursos_de(marca))
    if n_fotos:
        chips += f"<span>{n_fotos} foto{'s' if n_fotos != 1 else ''} tuya{'s' if n_fotos != 1 else ''}"
        chips += " · estilo copiado</span>" if marca.get("fotos_estilo", True) else "</span>"
    return (
        '<div class="wiz-cab"><p class="ante">Resumen</p><h2>Así se verá tu contenido</h2>'
        '<p class="baj">Esta es tu identidad visual. La usamos cada vez que creamos contenido para ti. Toca Editar para'
        " cambiar cualquier parte.</p></div><div class=\"wiz-barra\"><i style=\"width:100%\"></i></div>"
        '<div class="wiz-resumen">'
        + caja(1, "Estilo visual", "Tus estilos, en orden de preferencia.", estilos)
        + caja(2, "Colores", escape(nombre_colores), franja)
        + caja(3, "Tipografía", f"{LETRAS[fuente][1]} · {fuente}", muestra_letra(fuente, chica=True))
        + caja(4, "Logotipo", "Cómo aparece tu marca.", logo_html)
        + caja(5, "Recursos visuales", "Lo que mostramos en tus publicaciones.",
               f'<div class="mrc-chips">{chips}</div>', ancho=True)
        + '</div><div class="wiz-pie"><span>Tu marca se aplica sola en cada campaña. El objetivo, los formatos y la'
        " variedad visual se eligen al crear cada campaña.</span>"
        '<a class="boton" href="/panel/contenido/campanas/nueva">Crear una campaña →</a></div>')


async def pagina_marca(c: Cuenta, paso: str, marca: dict | None = None, aviso: str = ""):
    marca = marca if marca is not None else await marca_de(c)
    hechos_ = hechos(marca)
    if paso == RESUMEN:
        actual, contenido = RESUMEN, resumen(c, marca)
    else:
        i = int(paso) if paso.isdigit() and 1 <= int(paso) <= len(PASOS) else 1
        actual = CLAVES[i - 1]
        hacer = {"estilo": lambda: paso_estilo(marca), "colores": lambda: paso_colores(marca),
                 "tipografia": lambda: paso_tipografia(marca), "logotipo": lambda: paso_logotipo(c, marca),
                 "recursos": lambda: paso_recursos(c, marca)}[actual]
        colores = colores_de(marca)
        estilo = ";".join(f"--m-{k}:{colores[k]}" for k, *_ in COLORES)
        contenido = (f'<form method="post" action="{BASE}" enctype="multipart/form-data" data-marca style="{estilo}">'
                     f'<input type="hidden" name="paso" value="{i}">'
                     # Enter en un campo de texto guarda y sigue (el primer botón del formulario).
                     '<button class="oculto" name="accion" value="guardar" tabindex="-1" aria-hidden="true">Guardar</button>'
                     + cabeza(i) + hacer()
                     + pie(i, "Ver resumen →" if i == len(PASOS) else "Guardar y continuar →") + "</form>")
    cuerpo = f'<div class="wiz mrc">{menu_pasos(actual, hechos_)}<div class="wiz-main">{contenido}</div></div>'
    return await pagina(c, "marca", cuerpo, aviso, oscuro=True)


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
        datos["uso_color"] = f.get("uso_color") if f.get("uso_color") in USO_COLOR else uso_de(antes)
    elif paso == "tipografia":
        fuente = f.get("fuente") if f.get("fuente") in LETRAS else letra_de(antes)
        datos["fuente"], datos["peso"] = fuente, LETRAS[fuente][0]
    elif paso == "logotipo":
        datos["logo_presencia"] = f.get("logo_presencia") if f.get("logo_presencia") in LOGO_PRESENCIA else logo_de(antes)
    elif paso == "recursos":
        datos["recursos"] = [k for k in RECURSOS if k in f.getlist("recursos")]
        datos["fotos_estilo"] = f.get("fotos_estilo") == "1"
    datos["pasos"] = [k for k in CLAVES if k in set(antes.get("pasos", [])) | {paso}]
    return datos, ""


@router.get("/marca")
async def ver_marca(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if c.imagenes_mes <= 0:
        return sin_plan()
    paso = request.query_params.get("paso", "")
    if not paso:  # lista: el resumen; si no, el primer paso que falta
        marca = await marca_de(c)
        faltan = [i for i, k in enumerate(CLAVES, 1) if k not in hechos(marca)]
        paso = RESUMEN if lista(marca) or not faltan else str(faltan[0])
    return await pagina_marca(c, paso)


@router.post("/marca")
async def guardar_marca(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if c.imagenes_mes <= 0:
        return sin_plan()
    f = await request.form()
    i = int(f.get("paso")) if str(f.get("paso", "")).isdigit() and 1 <= int(f.get("paso")) <= len(PASOS) else 1
    paso, accion = CLAVES[i - 1], str(f.get("accion", "guardar"))
    antes = await marca_de(c)
    datos, aviso = guardar_paso(f, antes, paso)
    if aviso:
        return await pagina_marca(c, str(i), aviso=aviso)

    # Archivos: el logo (paso 4) y las fotos del negocio (paso 5).
    if accion == "quitar_logo" and datos.get("logo"):
        archivo(c, datos.pop("logo")).unlink(missing_ok=True)
        aviso = "Listo: quitamos tu logo."
    if accion.startswith("quitar_") and accion[7:].isdigit():
        fotos = list(datos.get("fotos", []))
        if int(accion[7:]) < len(fotos):
            archivo(c, fotos.pop(int(accion[7:]))).unlink(missing_ok=True)
            datos["fotos"] = fotos
        aviso = "Listo: quitamos esa foto."
    logo = f.get("logo")
    if getattr(logo, "filename", ""):
        tokens, _ = await guardar_fotos(c, [logo], 1)
        if tokens:
            if datos.get("logo"):
                archivo(c, datos["logo"]).unlink(missing_ok=True)
            datos["logo"] = tokens[0]
        else:
            aviso = "No se pudo leer el logo (usa JPG o PNG de hasta 10 MB)."
    nuevas = [s for s in f.getlist("fotos") if getattr(s, "filename", "")]
    if nuevas:
        fotos = list(datos.get("fotos", []))
        tokens, fallidas = await guardar_fotos(c, nuevas, MAX_FOTOS - len(fotos))
        datos["fotos"] = fotos + tokens
        aviso = f"Listo: {len(tokens)} foto{'s' if len(tokens) != 1 else ''} nueva{'s' if len(tokens) != 1 else ''}."
        if fallidas:
            aviso += f" {fallidas} no se pudieron leer (usa JPG o PNG de hasta 10 MB)."
    elif accion == "subir":
        aviso = "Elige primero las fotos."

    await c.store.set("marca", json.dumps(datos, ensure_ascii=False))
    log.info("@%s guardó Tu marca: %s", await panel.usuario(c), paso)
    if accion != "guardar" or aviso:  # subió o quitó algo: se queda en el paso para verlo
        return await pagina_marca(c, str(i), datos, aviso)
    return RedirectResponse(f"{BASE}?paso={i + 1 if i < len(PASOS) else RESUMEN}", status_code=303)
