"""Personaje de tu marca: un personalizador por secciones (como armar un avatar en un videojuego).

console.skandar.online/panel/contenido/personaje

A un lado las secciones (cabeza, arriba de la cabeza, ojos, expresión, cara, cuerpo, piel, ropa, accesorios,
extras, colores y nombre) y al otro el personaje, que cambia al momento. Todas las piezas son dibujos
vectoriales nuestros que encajan sobre la misma base: de la combinación sale un ajolote, un oso, una
criatura lisa o lo que el cliente arme. No hay IA en esta pantalla: no cuesta ni espera.

Sin JavaScript funciona igual: todas las secciones a la vista y «Aplicar» guarda y repinta el personaje.
panel.js solo cambia las piezas al tocar, muestra una sección a la vez y acerca la vista a la parte
que se edita. «Al azar» lo arma el servidor (sin guardar) con combinaciones que sí quedan bien.

«Dar vida» sí usa IA: con lo elegido se escribe la descripción del personaje y GPT Image genera su versión
en 3D; con esa imagen como referencia (fidelidad alta) se genera la hoja de personaje (poses y expresiones).
Son dos imágenes; no usan el cupo del plan pero sí su presupuesto de contenido, con tope de veces al mes
(config.PERSONAJE_VIDAS_MES). Las dos quedan en kv "personaje" → "vida" para usarlas en las campañas.

Todo se guarda en kv "personaje".
"""
import asyncio
import hashlib
import json
import logging
import random
import secrets
import time
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, imagenes, limites, marca, marco
from .contenido import TOKEN_FOTO, archivo, marca_de, preparar_foto, sin_plan, url_imagen
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.personaje")

router = APIRouter(prefix="/panel/contenido")
BASE = "/panel/contenido/personaje"
MAX_NOMBRE = 24
MAX_RASGOS = 3
RASGOS = ("Amable", "Alegre", "Claro", "Atento", "Curioso", "Tranquilo", "Ingenioso", "Directo", "Paciente",
          "Entusiasta", "Elegante", "Divertido")

# Los cuatro colores del personaje: (clave, nombre en el panel).
COLORES = (("cuerpo", "Cuerpo"), ("claro", "Cara y panza"), ("acento", "Detalles"), ("acento2", "Segundo detalle"))
PALETAS = {  # clave: (nombre, cuerpo, claro, acento, acento2)
    "durazno": ("Durazno", "#F4C4AC", "#FFF1E8", "#FF8A3D", "#C6FA2C"),
    "nube": ("Nube", "#F4F4F2", "#FFFFFF", "#8FA8E8", "#FF8A3D"),
    "lavanda": ("Lavanda", "#8FA8E8", "#F3E6D3", "#FF8A3D", "#FFD166"),
    "menta": ("Menta", "#8FD9C4", "#F2FFF9", "#FF7A90", "#FFD166"),
    "arena": ("Arena", "#E3D2B4", "#FBF3E4", "#C96F4A", "#7FB7A3"),
    "grafito": ("Grafito", "#4A4B55", "#F3E6D3", "#FF8A3D", "#C6FA2C"),
}
TINTA, TINTA_CLARA = "#1B1B22", "#F5F5F4"  # ojos y boca: oscuros sobre cara clara, claros sobre cara oscura


# --- piezas (todas sobre el mismo lienzo: cabeza centrada en 300,270; pies en y=600) ---

def _par(svg: str) -> str:
    """La pieza y su espejo del otro lado."""
    return f'{svg}<g transform="translate(600 0) scale(-1 1)">{svg}</g>'


CABEZAS = {
    "redonda": ("Redonda", '<ellipse class="pj-c" cx="300" cy="270" rx="150" ry="140"/>'),
    "ancha": ("Ancha", '<ellipse class="pj-c" cx="300" cy="272" rx="176" ry="142"/>'),
    "ovalada": ("Ovalada", '<ellipse class="pj-c" cx="300" cy="266" rx="134" ry="150"/>'),
    "gota": ("De gota", '<path class="pj-c" d="M300 120C392 120 462 214 462 300C462 378 390 412 300 412'
                        'C210 412 138 378 138 300C138 214 208 120 300 120Z"/>'),
}
_HOJA = "M0 0C-18 -40 -14 -92 0 -122C14 -92 18 -40 0 0Z"
ARRIBA = {
    "nada": ("Nada", ""),
    "branquias": ("Branquias", _par(
        f'<g transform="translate(156 232)"><path class="pj-a" d="{_HOJA}" transform="rotate(-38) scale(1.25)"/>'
        f'<path class="pj-b" d="{_HOJA}" transform="translate(-18 36) rotate(-72) scale(1.25)"/>'
        f'<path class="pj-a" d="{_HOJA}" transform="translate(-18 78) rotate(-105) scale(1.1)"/></g>')),
    "oso": ("Orejas de oso", _par('<circle class="pj-c" cx="186" cy="152" r="50"/>'
                                  '<circle class="pj-l" cx="186" cy="152" r="24"/>')),
    "gato": ("Orejas de gato", _par('<path class="pj-c pj-borde" d="M160 214L172 84L270 150Z"/>'
                                    '<path class="pj-a" d="M184 176L190 118L234 148Z"/>')),
    "conejo": ("Orejas de conejo", _par('<ellipse class="pj-c" cx="236" cy="96" rx="32" ry="90" transform="rotate(-10 236 96)"/>'
                                        '<ellipse class="pj-l" cx="236" cy="96" rx="14" ry="62" transform="rotate(-10 236 96)"/>')),
    "antenas": ("Antenas", _par('<path class="pj-linea" d="M252 150C244 110 236 86 226 62"/>'
                                '<circle class="pj-a" cx="224" cy="56" r="16"/>')),
    "cuernos": ("Cuernos chicos", _par('<path class="pj-l" d="M212 156C192 114 202 84 232 72C228 104 240 130 258 142Z"/>')),
    "cresta": ("Cresta", '<circle class="pj-a" cx="262" cy="134" r="22"/><circle class="pj-b" cx="300" cy="120" r="24"/>'
                         '<circle class="pj-a" cx="338" cy="134" r="22"/>'),
}
OJOS = {
    "punto": ("De punto", _par('<circle class="pj-t" cx="230" cy="278" r="15"/>')),
    "grandes": ("Grandes", _par('<circle class="pj-t" cx="230" cy="278" r="24"/>')
                + '<g class="pj-luz"><circle cx="238" cy="269" r="8"/><circle cx="378" cy="269" r="8"/>'
                  '<circle cx="222" cy="288" r="3.5"/><circle cx="362" cy="288" r="3.5"/></g>'),
    "ovalados": ("Ovalados", _par('<ellipse class="pj-t" cx="232" cy="276" rx="14" ry="23"/>')
                 + '<g class="pj-luz"><circle cx="236" cy="266" r="5"/><circle cx="372" cy="266" r="5"/></g>'),
    "entrecerrados": ("Entrecerrados", _par('<path class="pj-t" d="M208 270a22 22 0 0 0 44 0Z"/>'
                                            '<path class="pj-trazo" d="M202 268H258"/>')),
    "pestanas": ("Con pestañas", _par('<circle class="pj-t" cx="230" cy="280" r="18"/>'
                                      '<path class="pj-trazo fino" d="M210 266L200 254M222 260L216 246M236 259L236 245"/>')
                 + '<g class="pj-luz"><circle cx="236" cy="273" r="6"/><circle cx="376" cy="273" r="6"/></g>'),
}
BOCAS = {
    "amable": ("Amable", '<path class="pj-trazo" d="M274 320Q300 344 326 320"/>'),
    "alegre": ("Alegre", '<path class="pj-t" d="M268 316Q300 366 332 316Z"/><ellipse class="pj-lengua" cx="300" cy="340" rx="13" ry="8"/>'),
    "seria": ("Seria", '<path class="pj-trazo" d="M280 328H320"/><path class="pj-trazo fino" d="M204 240L250 248M396 240L350 248"/>'),
    "picara": ("Pícara", '<path class="pj-trazo" d="M278 324Q306 342 332 312"/><path class="pj-trazo fino" d="M346 236Q372 222 396 236"/>'),
    "sorprendida": ("Sorprendida", '<ellipse class="pj-t" cx="300" cy="330" rx="13" ry="16"/>'),
}
CARAS = {
    "directa": ("Directa", ""),
    "capucha": ("En capucha", '<ellipse class="pj-l" cx="300" cy="288" rx="114" ry="96"/>'),
}
MEJILLAS = '<ellipse class="pj-rosa" cx="210" cy="322" rx="26" ry="17"/><ellipse class="pj-rosa" cx="390" cy="322" rx="26" ry="17"/>'

# Cuerpos: (nombre, medio ancho, contorno, alto de las piernas). La panza, la ropa y los brazos se ajustan al ancho.
CUERPOS = {
    "pequeno": ("Pequeño", 92, "M300 380C380 380 400 470 392 530C386 580 350 596 300 596C250 596 214 580 208 530"
                               "C200 470 220 380 300 380Z", 22),
    "robusto": ("Robusto", 132, "M300 372C400 372 440 460 432 530C426 584 372 598 300 598C228 598 174 584 168 530"
                                "C160 460 200 372 300 372Z", 24),
    "redondo": ("Redondo", 118, "M300 376C372 376 418 428 418 488C418 552 368 598 300 598C232 598 182 552 182 488"
                                "C182 428 228 376 300 376Z", 22),
    "delgado": ("Delgado", 70, "M300 380C352 380 372 440 370 510C368 556 340 572 300 572C260 572 232 556 230 510"
                               "C228 440 248 380 300 380Z", 34),
}
ROPAS = {"ninguna": "Ninguna", "playera": "Playera", "overol": "Overol"}
PATRONES = {"liso": "Lisa", "panza": "Panza clara", "pecas": "Pecas", "manchas": "Manchas"}
MATERIALES = {"liso": "Liso mate", "peludo": "Peludo", "brillante": "Brillante"}
_PECAS = ('<g class="pj-sombra-piel"><circle cx="262" cy="196" r="4"/><circle cx="282" cy="182" r="3"/>'
          '<circle cx="300" cy="172" r="3"/><circle cx="318" cy="182" r="3"/><circle cx="338" cy="196" r="4"/></g>')
_MANCHAS = ('<g class="pj-sombra-piel"><ellipse cx="216" cy="190" rx="26" ry="18" transform="rotate(-30 216 190)"/>'
            '<ellipse cx="392" cy="206" rx="20" ry="14" transform="rotate(35 392 206)"/><circle cx="344" cy="168" r="11"/></g>')
ACCESORIOS = {
    "nada": ("Nada", ""),
    "audifono": ("Audífono", '<path class="pj-linea gris" d="M150 236C150 122 450 122 450 236"/>'
                             '<rect class="pj-g" x="126" y="228" width="40" height="70" rx="18"/>'
                             '<rect class="pj-g" x="434" y="228" width="40" height="70" rx="18"/>'
                             '<path class="pj-linea gris fino" d="M146 298C146 352 190 366 232 362"/>'
                             '<ellipse class="pj-a" cx="240" cy="361" rx="15" ry="10"/>'),
    "lentes": ("Lentes", '<g class="pj-trazo"><circle cx="232" cy="278" r="40"/><circle cx="368" cy="278" r="40"/>'
                         '<path d="M272 274Q300 262 328 274"/></g>'),
    "gorra": ("Gorra", '<path class="pj-a" d="M176 190C180 84 420 84 424 190Z"/>'
                       '<path class="pj-b" d="M300 176C380 164 462 176 486 204C430 196 366 192 300 196Z"/>'
                       '<circle class="pj-b" cx="300" cy="106" r="11"/>'),
}
CUELLOS = {
    "nada": ("Nada", ""),
    "bufanda": ("Bufanda", '<path class="pj-b" d="M196 378C250 408 350 408 404 378L412 412C350 446 250 446 188 412Z"/>'
                           '<path class="pj-b oscuro" d="M344 426L384 420L398 512L358 518Z"/>'),
    "mono": ("Moño", '<path class="pj-a" d="M300 404L252 380V432Z"/><path class="pj-a" d="M300 404L348 380V432Z"/>'
                     '<circle class="pj-a oscuro" cx="300" cy="405" r="12"/>'),
}
EXTRAS = {
    "nada": ("Nada", ""),
    "aleta": ("Cola con aleta", '<path class="pj-a" d="M352 524C440 524 484 480 474 424C450 460 406 470 352 468Z"/>'),
    "bolita": ("Cola redonda", '<circle class="pj-l" cx="430" cy="540" r="30"/>'),
    "alas": ("Alas chicas", _par('<path class="pj-b" d="M214 430C150 396 110 420 104 468C140 462 150 488 200 482Z"/>')),
}


def cuerpo_svg(k: str, vivo: bool = True) -> str:
    """El cuerpo con sus piernas, brazos, panza y ropa. Con `vivo`, la panza y la ropa llevan data-pieza
    para mostrarse según lo elegido; sin él (las miniaturas) va solo el cuerpo."""
    _, w, contorno, pierna = CUERPOS[k]
    pies = "".join(f'<ellipse class="pj-c oscuro" cx="{300 + s * round(w * .46)}" cy="{600 - pierna // 2}" '
                   f'rx="{round(w * .4)}" ry="{pierna}"/>' for s in (-1, 1))
    brazo = (f'<path class="pj-c oscuro" d="M{300 - w + 8} 452C{300 - w - 30} 462 {300 - w - 40} 506 {300 - w - 22} 524'
             f'C{300 - w - 6} 536 {300 - w + 14} 520 {300 - w + 20} 492Z"/>')
    extra = ""
    if vivo:
        extra = (f'<clipPath id="pj-cl-{k}"><path d="{contorno}"/></clipPath>'
                 f'<ellipse class="pj-l" data-pieza="patron:panza" cx="300" cy="{512 if k != "delgado" else 492}" '
                 f'rx="{round(w * .62)}" ry="{round(w * .62) + 6}"/>'
                 f'<g clip-path="url(#pj-cl-{k})"><rect class="pj-a" data-pieza="ropa:playera" x="100" y="360" width="400" height="128"/>'
                 f'<g data-pieza="ropa:overol"><rect class="pj-b" x="100" y="476" width="400" height="140"/>'
                 f'<rect class="pj-b" x="{300 - round(w * .5)}" y="360" width="18" height="130"/>'
                 f'<rect class="pj-b" x="{300 + round(w * .5) - 18}" y="360" width="18" height="130"/></g></g>')
    return f'{pies}<path class="pj-c" d="{contorno}"/>{extra}{_par(brazo)}'


# Secciones en el orden en que se recorren: (clave, nombre, a dónde se acerca la vista, grupos).
# Cada grupo: (campo del formulario, título o "", {valor: (nombre, miniatura)}, encuadre de la miniatura,
# silueta gris de la miniatura: "antes" o "despues" de la pieza, según si la pieza va encima o detrás).
def _nombres(d: dict) -> dict:
    return {k: (v, "") for k, v in d.items()}


_SILUETA_CABEZA = '<ellipse class="pj-silueta" cx="300" cy="270" rx="150" ry="140"/>'
_SILUETA = f'<path class="pj-silueta" d="{CUERPOS["pequeno"][2]}"/>{_SILUETA_CABEZA}'
SECCIONES = [
    ("cabeza", "Cabeza", "cabeza", [("cabeza", "", CABEZAS, "110 110 380 320", "")]),
    ("arriba", "Arriba de la cabeza", "cabeza", [("arriba", "", ARRIBA, "40 30 520 400", "despues")]),
    ("ojos", "Ojos", "cara", [("ojos", "", OJOS, "170 190 260 180", "antes")]),
    ("expresion", "Expresión", "cara", [("boca", "", BOCAS, "170 200 260 180", "antes")]),
    ("cara", "Cara", "cara", [("cara", "", _nombres({k: v[0] for k, v in CARAS.items()}), "", "")]),
    ("cuerpo", "Cuerpo", "todo", [("cuerpo", "", {k: (v[0], cuerpo_svg(k, vivo=False)) for k, v in CUERPOS.items()},
                                   "130 350 340 280", "")]),
    ("piel", "Piel y material", "todo", [("material", "Material", _nombres(MATERIALES), "", ""),
                                         ("patron", "Dibujo de la piel", _nombres(PATRONES), "", "")]),
    ("ropa", "Ropa", "todo", [("ropa", "", _nombres(ROPAS), "", "")]),
    ("accesorios", "Accesorios", "cabeza", [("accesorio", "En la cabeza", ACCESORIOS, "90 70 440 360", "antes"),
                                            ("cuello", "En el cuello", CUELLOS, "110 240 380 300", "antes")]),
    ("extras", "Extras", "todo", [("extras", "", EXTRAS, "60 100 500 520", "despues")]),
]
GRUPOS = {campo: opciones for *_, grupos in SECCIONES for campo, _, opciones, *_ in grupos}
INICIAL = {"cabeza": "ancha", "arriba": "branquias", "ojos": "grandes", "boca": "amable", "cara": "directa",
           "mejillas": True, "cuerpo": "pequeno", "material": "liso", "patron": "panza", "ropa": "ninguna",
           "accesorio": "audifono", "cuello": "nada", "extras": "aleta", "paleta": "durazno",
           "colores": dict(zip((k for k, _ in COLORES), PALETAS["durazno"][1:])), "nombre": "", "rasgos": []}


# --- «Dar vida»: lo elegido, descrito para el generador de imágenes (texto nuestro, nada del cliente) ---

DESCRIPCION = {
    "cabeza": {"redonda": "a big round head", "ancha": "a big wide oval head, wider than the body",
               "ovalada": "a tall oval head", "gota": "a big head shaped like a drop, narrower at the top"},
    "arriba": {"nada": "nothing on top of the head, no ears",
               "branquias": "three leaf-shaped external gills on each side of the head like an axolotl "
                            "(top one {acento}, middle one {acento2}, bottom one {acento})",
               "oso": "two round bear ears with {claro} inner ear", "gato": "two pointed cat ears with {acento} inner ear",
               "conejo": "two long upright rabbit ears with {claro} inner ear",
               "antenas": "two thin antennae ending in a small {acento} ball", "cuernos": "two tiny {claro} horns",
               "cresta": "a small crest of three round bumps on top of the head ({acento}, {acento2}, {acento})"},
    "ojos": {"punto": "small black dot eyes set wide apart", "grandes": "big round black eyes with white highlights",
             "ovalados": "tall oval black eyes with a small highlight", "entrecerrados": "half-closed relaxed eyes",
             "pestanas": "round black eyes with three small eyelashes each"},
    "boca": {"amable": "a small gentle smile", "alegre": "a wide open happy smile",
             "seria": "a small straight serious mouth and slightly lowered brows",
             "picara": "a mischievous half smile with one raised eyebrow", "sorprendida": "a small round surprised mouth"},
    "cara": {"directa": "the face sits directly on the {cuerpo} head",
             "capucha": "the {cuerpo} skin frames the face like a hood, with a {claro} oval face area"},
    "cuerpo": {"pequeno": "a small pear-shaped body, smaller than the head, with short arms and short legs",
               "robusto": "a chubby wide body with short arms and short legs",
               "redondo": "a round ball-like body with short arms and tiny feet",
               "delgado": "a slim small body with short arms and slightly longer legs"},
    "material": {"liso": "smooth matte vinyl toy skin", "peludo": "soft fluffy plush fur all over",
                 "brillante": "glossy smooth vinyl skin with soft highlights"},
    "patron": {"liso": "plain skin with no markings", "panza": "a {claro} oval belly patch",
               "pecas": "a few small freckles on the forehead", "manchas": "a few soft darker spots on the head"},
    "ropa": {"ninguna": "no clothes", "playera": "a plain {acento} t-shirt", "overol": "plain {acento2} overalls"},
    "accesorio": {"nada": "", "audifono": "a light grey call-center headset with a small {acento} microphone",
                  "lentes": "round thin-framed glasses", "gorra": "a {acento} baseball cap with a {acento2} visor"},
    "cuello": {"nada": "", "bufanda": "a {acento2} scarf around the neck", "mono": "a {acento} bow tie"},
    "extras": {"nada": "no tail", "aleta": "a short tail with an {acento} fin", "bolita": "a small round {claro} tail",
               "alas": "two tiny {acento2} wings on the back"},
}
ESTILO_3D = ("Original 3D animated mascot character in the style of a modern animated film: soft rounded shapes, "
             "simple friendly design, soft studio lighting, subtle ambient occlusion.")
SIN_TEXTO = "No text, no letters, no logos, no watermark."
HOJA = ("Character reference sheet of this exact same character, same colors, same proportions, same material and "
        "same accessories, on a plain light grey background: full body front view, side view and back view in the top "
        "row; four head close-ups with different expressions (happy, thinking, surprised, winking) in the bottom row. "
        "Do not change the design. " + SIN_TEXTO)


def descripcion(p: dict) -> str:
    """El personaje en palabras para el generador: solo textos nuestros y los colores elegidos."""
    colores = p["colores"]
    partes = [DESCRIPCION[campo][p[campo]].format(**colores) for campo in DESCRIPCION]
    if p["mejillas"]:
        partes.append("soft pink blush on the cheeks")
    rasgos = f" Personality: {', '.join(p['rasgos']).lower()}." if p["rasgos"] else ""
    return (f"A small cute creature. Main body and head color {colores['cuerpo']}. It has "
            + "; ".join(x for x in partes if x) + "." + rasgos)


def prompt_retrato(p: dict) -> str:
    return (f"{ESTILO_3D} {descripcion(p)} Full body, standing, facing the camera, centered, whole character visible "
            f"with margin around it. Plain soft neutral background. {SIN_TEXTO}")


def firma(p: dict) -> str:
    """Cambia cuando cambia algo que se ve (piezas o colores): para avisar que el 3D ya no es el de la pantalla."""
    visto = {k: p[k] for k in (*GRUPOS, "mejillas", "colores")}
    return hashlib.sha256(json.dumps(visto, sort_keys=True).encode()).hexdigest()[:12]


async def _guardar_imagen(c: Cuenta, data: bytes) -> str:
    """La imagen como JPG en la carpeta de la cuenta (nombre al azar), reabierta con Pillow."""
    token = secrets.token_urlsafe(18)
    archivo(c, token).parent.mkdir(parents=True, exist_ok=True)
    archivo(c, token).write_bytes(await asyncio.to_thread(preparar_foto, data))
    return token


async def sin_presupuesto(c: Cuenta) -> bool:
    with usar(c):
        return await limites.sin_presupuesto(c.store)


def tokens_de(guardado: dict) -> list[str]:
    """Las imágenes de «Dar vida» que tiene guardadas el personaje (nombres válidos solamente)."""
    vida = guardado.get("vida") if isinstance(guardado.get("vida"), dict) else {}
    return [t for t in (vida.get("retrato"), vida.get("hoja")) if isinstance(t, str) and TOKEN_FOTO.match(t)]


def vidas_del_mes(guardado: dict, ahora: float | None = None) -> list[int]:
    """Cuándo se le dio vida en los últimos 30 días (solo las que sí salieron cuentan para el tope)."""
    desde = (time.time() if ahora is None else ahora) - 30 * config.DIA
    return [t for t in guardado.get("vidas", []) if isinstance(t, (int, float)) and t >= desde]


def dar_vida(c: Cuenta, p: dict):
    """El trabajo largo: el personaje en 3D y, con él de referencia, su hoja. Si la hoja falla, el 3D se queda."""
    async def correr():
        yield "Modelando tu personaje en 3D…"
        with usar(c):  # el costo se anota en esta cuenta (presupuesto de contenido)
            retrato = await imagenes.proveedor.generar(prompt_retrato(p))
        vida = {"retrato": await _guardar_imagen(c, retrato), "hoja": "", "firma": firma(p), "cuando": int(time.time())}
        yield "Dibujando su hoja de poses y expresiones…"
        try:
            with usar(c):
                hoja = await imagenes.proveedor.generar(HOJA, [retrato], fiel=True)
            vida["hoja"] = await _guardar_imagen(c, hoja)
        except imagenes.ErrorImagen as e:
            log.warning("@%s: la hoja de personaje no salió: %s", c.slug, e)
        antes = await de(c)
        for token in tokens_de(antes):  # las de antes ya no se usan: no se acumulan
            archivo(c, token).unlink(missing_ok=True)
        await c.store.set("personaje", json.dumps({**antes, "vida": vida, "vidas": [*vidas_del_mes(antes), vida["cuando"]]},
                                                  ensure_ascii=False))
        log.info("@%s dio vida a su personaje (hoja: %s)", c.slug, "sí" if vida["hoja"] else "no")
    return correr


# --- datos ---

def limpio(datos: dict) -> dict:
    """El personaje con cada campo en una opción válida (lo que falte o no exista, lo de fábrica)."""
    p = {campo: datos.get(campo) if datos.get(campo) in opciones else INICIAL[campo] for campo, opciones in GRUPOS.items()}
    colores = datos.get("colores") if isinstance(datos.get("colores"), dict) else {}
    p["colores"] = {k: str(colores.get(k)) if imagenes.HEX.match(str(colores.get(k, ""))) else INICIAL["colores"][k]
                    for k, _ in COLORES}
    p["mejillas"] = bool(datos.get("mejillas", INICIAL["mejillas"]))
    p["nombre"] = " ".join(str(datos.get("nombre", "")).split())[:MAX_NOMBRE]
    rasgos = datos.get("rasgos") if isinstance(datos.get("rasgos"), list) else []
    p["rasgos"] = [r for r in RASGOS if r in rasgos][:MAX_RASGOS]
    return p


async def de(c: Cuenta) -> dict:
    """El personaje guardado, o {} si la cuenta aún no arma uno."""
    return json.loads(await c.store.get("personaje") or "{}")


def del_form(f) -> dict:
    datos = {campo: str(f.get(campo, "")) for campo in GRUPOS}
    datos["colores"] = {k: str(f.get(f"c_{k}", "")) for k, _ in COLORES}
    datos["mejillas"] = f.get("mejillas") == "si"
    datos["nombre"] = str(f.get("nombre", ""))
    datos["rasgos"] = [str(r) for r in f.getlist("rasgos")]
    return limpio(datos)


def colores_marca(m: dict) -> dict[str, str] | None:
    """Los colores de Tu marca puestos en el personaje (si ya los eligió): el cuerpo con el principal."""
    c = marca.colores_de(m) if "colores" in marca.hechos(m) else {}
    if not c:
        return None
    return {"cuerpo": c["principal"], "claro": c["fondo"], "acento": c["acento"], "acento2": c["secundario"]}


# Lo que el azar no junta: la gorra tapa lo que sale de arriba de la cabeza.
SIN_GORRA = {"oso", "gato", "conejo", "antenas", "cuernos", "cresta"}


def al_azar(antes: dict, rng: random.Random | None = None, colores: bool = False) -> dict:
    """Otro personaje con las piezas al azar (el nombre, los rasgos y los colores se conservan). Con `colores`,
    solo cambia la paleta."""
    rng = rng or random.Random()
    p = limpio(antes)
    if colores:
        otras = [v[1:] for v in PALETAS.values() if list(v[1:]) != list(p["colores"].values())]
        p["colores"] = dict(zip((k for k, _ in COLORES), rng.choice(otras)))
        return p
    for campo, opciones in GRUPOS.items():
        p[campo] = rng.choice(list(opciones))
    if p["arriba"] in SIN_GORRA and p["accesorio"] == "gorra":
        p["accesorio"] = rng.choice([k for k in ACCESORIOS if k != "gorra"])
    p["mejillas"] = rng.random() < .5
    return p


def _luz(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return .299 * r + .587 * g + .114 * b


def tinta(p: dict) -> str:
    """El color de ojos y boca según el fondo de la cara (la capucha, o la piel si la cara es directa)."""
    cara = p["colores"]["claro" if p["cara"] == "capucha" else "cuerpo"]
    return TINTA_CLARA if _luz(cara) < .4 else TINTA


# --- dibujo ---

def _capa(campo: str, valor: str, svg: str, p: dict) -> str:
    if not svg:
        return ""
    return f'<g data-pieza="{campo}:{valor}"{"" if p[campo] == valor else " hidden"}>{svg}</g>'


def _capas(campo: str, piezas: dict, p: dict) -> str:
    return "".join(_capa(campo, k, v[1], p) for k, v in piezas.items())


def dibujo(p: dict) -> str:
    """El personaje completo: todas las piezas en el lienzo y a la vista solo las elegidas (panel.js las cambia)."""
    def suelta(pieza: str, svg: str) -> str:  # panza, ropa, pecas: piezas que dependen de otro campo
        campo, valor = pieza.split(":")
        return svg.replace(f'data-pieza="{pieza}"', f'data-pieza="{pieza}"{"" if p[campo] == valor else " hidden"}')

    cuerpos = ""
    for k in CUERPOS:
        svg = cuerpo_svg(k)
        for pieza in ("patron:panza", "ropa:playera", "ropa:overol"):
            svg = suelta(pieza, svg)
        cuerpos += _capa("cuerpo", k, svg, p)
    mejillas = f'<g data-pieza="mejillas:si"{"" if p["mejillas"] else " hidden"}>{MEJILLAS}</g>'
    return (
        f'<svg class="pj-svg" viewBox="-70 0 740 640" data-material="{p["material"]}" data-zoom="todo" role="img" '
        f'aria-label="Tu personaje">'
        '<defs><filter id="pj-pelo" x="-10%" y="-10%" width="120%" height="120%"><feTurbulence type="fractalNoise" '
        'baseFrequency=".9" numOctaves="2" seed="4"/><feDisplacementMap in="SourceGraphic" scale="9"/></filter></defs>'
        '<ellipse class="pj-piso" cx="300" cy="606" rx="160" ry="16"/>'
        '<g class="pj-piel">' + _capas("extras", EXTRAS, p) + _capas("arriba", ARRIBA, p) + cuerpos
        + _capas("cabeza", CABEZAS, p) + "</g>"
        + '<ellipse class="pj-brillo" cx="250" cy="196" rx="74" ry="46" transform="rotate(-24 250 196)"/>'
        + _capas("cara", CARAS, p)
        + _capa("patron", "pecas", _PECAS, p) + _capa("patron", "manchas", _MANCHAS, p)
        + mejillas + _capas("ojos", OJOS, p) + _capas("boca", BOCAS, p)
        + _capas("cuello", CUELLOS, p) + _capas("accesorio", ACCESORIOS, p) + "</svg>")


def variables(p: dict) -> str:
    return ";".join(f"--pj-{k}:{v}" for k, v in p["colores"].items()) + f";--pj-tinta:{tinta(p)}"


# --- página ---

def _opcion(campo: str, valor: str, nombre: str, svg: str, caja: str, silueta: str, elegido: bool) -> str:
    fondo = _SILUETA if silueta else ""
    dentro = svg + fondo if silueta == "despues" else fondo + svg
    mini = f'<svg class="pj-mini" viewBox="{caja}" aria-hidden="true">{dentro}</svg>' if caja else ""
    return (f'<label class="pj-op{"" if mini else " texto"}"><input type="radio" name="{campo}" value="{valor}"'
            f'{" checked" if elegido else ""}>{mini}<span>{escape(nombre)}</span></label>')


def seccion(i: int, clave: str, nombre: str, zoom: str, grupos: list, p: dict) -> str:
    cuerpo = ""
    for campo, titulo, opciones, caja, silueta in grupos:
        cuerpo += (f'<h4 class="pj-grupo">{titulo}</h4>' if titulo else "") + '<div class="pj-ops">' + "".join(
            _opcion(campo, k, v[0], v[1], caja, silueta, p[campo] == k) for k, v in opciones.items()) + "</div>"
    if clave == "cara":
        cuerpo += ('<label class="pj-si"><input type="checkbox" name="mejillas" value="si"'
                   f'{" checked" if p["mejillas"] else ""}><span>Mejillas rosadas</span></label>')
    return (f'<fieldset class="pj-sec{" on" if i == 0 else ""}" id="pj-{clave}" data-zoom="{zoom}">'
            f"<legend>{nombre}</legend>{cuerpo}</fieldset>")


def seccion_colores(p: dict, de_marca: dict | None) -> str:
    paletas = dict(PALETAS)
    if de_marca:
        paletas = {"marca": ("Los de Tu marca", *de_marca.values()), **paletas}
    actuales = list(p["colores"].values())
    ops = "".join(
        f'<label class="pj-op pj-paleta"><input type="radio" name="paleta" value="{k}" data-colores="{",".join(cs)}"'
        f'{" checked" if list(cs) == actuales else ""}><span class="pj-muestra">'
        + "".join(f'<i style="background:{c}"></i>' for c in cs) + f"</span><span>{escape(nombre)}</span></label>"
        for k, (nombre, *cs) in paletas.items())
    campos = "".join(f'<label class="pj-color"><input type="color" name="c_{k}" value="{p["colores"][k]}">'
                     f"<span>{nombre}</span></label>" for k, nombre in COLORES)
    return ('<fieldset class="pj-sec" id="pj-colores" data-zoom="todo"><legend>Colores</legend>'
            f'<div class="pj-ops">{ops}</div><h4 class="pj-grupo">O tus colores exactos</h4>'
            f'<div class="pj-colores">{campos}</div>'
            '<button class="boton secundario pj-azar-color" name="accion" value="azar_colores" formnovalidate>'
            "Colores al azar</button></fieldset>")


def seccion_nombre(p: dict) -> str:
    rasgos = "".join(f'<label class="wiz-chip"><input type="checkbox" name="rasgos" value="{r}"'
                     f'{" checked" if r in p["rasgos"] else ""}><span>{r}</span></label>' for r in RASGOS)
    return ('<fieldset class="pj-sec" id="pj-nombre" data-zoom="todo"><legend>Nombre y personalidad</legend>'
            '<label class="pj-campo"><span>Nombre</span>'
            f'<input type="text" name="nombre" maxlength="{MAX_NOMBRE}" value="{escape(p["nombre"])}" autocomplete="off"></label>'
            f'<h4 class="pj-grupo">Elige hasta {MAX_RASGOS} rasgos</h4><div class="wiz-chips" data-max="{MAX_RASGOS}">'
            f"{rasgos}</div></fieldset>")


def tarjeta_vida(c: Cuenta, p: dict, guardado: dict) -> str:
    """«Dar vida»: el botón y, si ya se generó, el personaje en 3D con su hoja."""
    tokens = tokens_de(guardado)
    vida = guardado.get("vida") or {}
    fotos = "".join(f'<a href="{url_imagen(c, t)}" target="_blank" rel="noopener"><img src="{url_imagen(c, t)}" '
                    f'alt="{alt}" loading="lazy"></a>'
                    for t, alt in zip(tokens, ("Tu personaje en 3D", "Hoja de personaje")))
    nota = ""
    if tokens and vida.get("firma") != firma(p):
        nota = '<p class="pj-vida-nota">Cambiaste el personaje después de darle vida. Genéralo otra vez para actualizarlo.</p>'
    elif tokens and len(tokens) < 2:
        nota = '<p class="pj-vida-nota">La hoja de poses no salió esta vez. Vuelve a generar para obtenerla.</p>'
    boton = "Volver a generar en 3D" if tokens else "Dar vida en 3D"
    return ('<section class="pj-vida"><h3>Tu personaje en 3D</h3>'
            + (f'<div class="pj-vida-fotos">{fotos}</div>{nota}' if tokens else
               '<p class="pj-vida-nota">Cuando te guste cómo quedó, generamos su versión en 3D y su hoja de poses.'
               " Tarda cerca de dos minutos.</p>")
            + f'<button class="boton secundario pj-vida-boton" name="accion" value="dar_vida">{boton}</button></section>')


async def pagina(c: Cuenta, p: dict | None = None, aviso: str = ""):
    m = await marca_de(c)
    guardado = await de(c)
    p = limpio(p if p is not None else guardado or INICIAL)
    nombres = [(clave, nombre) for clave, nombre, *_ in SECCIONES] + [("colores", "Colores"), ("nombre", "Nombre")]
    nav = "".join(f'<a href="#pj-{k}" data-sec="{i}"{" class=on" if i == 0 else ""}>{n}</a>'
                  for i, (k, n) in enumerate(nombres))
    secciones = ("".join(seccion(i, *s, p) for i, s in enumerate(SECCIONES))
                 + seccion_colores(p, colores_marca(m)) + seccion_nombre(p))
    pie = marco.pie_config(
        '<button class="boton secundario pj-ant" type="button" hidden>← Anterior</button>',
        '<button class="boton secundario pj-sig" type="button" hidden>Siguiente →</button>'
        '<button class="boton" name="accion" value="guardar">Guardar personaje</button>',
        "Puedes cambiarlo cuando quieras.")
    contenido = (
        f'<form method="post" action="{BASE}" class="pj" data-personaje style="{variables(p)}">'
        '<button class="oculto" name="accion" value="guardar" tabindex="-1" aria-hidden="true">Guardar</button>'
        '<div class="wiz-cab pj-cab"><div><p class="ante">Personaje</p><h2>Arma el personaje de tu marca</h2></div>'
        '<button class="boton pj-azar" name="accion" value="azar" formnovalidate>Al azar</button></div>'
        f'<div class="pj-dos"><div class="pj-lado"><nav class="pj-nav" aria-label="Secciones">{nav}</nav>{secciones}</div>'
        f'<div class="pj-esc"><div class="pj-marco">{dibujo(p)}</div>'
        f'<p class="pj-nom">{escape(p["nombre"]) or "Sin nombre"}</p></div></div>'
        f"{tarjeta_vida(c, p, guardado)}{pie}</form>")
    falta = await marco.pendiente(c, "marca")
    cuerpo = (f'<div class="wiz mrc">{marca.menu_pasos("personaje", marca.hechos(m), bool(guardado))}'
              f'<div class="wiz-main">{falta}{contenido}</div></div>')
    return marco.pagina(
        await marco.menu_de(c, "cuenta", sub="marca")
        + marco.encabezado("Tu cuenta · Tu marca")
        + '<main class="oscuro-pag"><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + cuerpo + "</div></main>")


@router.get("/personaje")
async def ver_personaje(request: Request, c: marco.Sesion):
    if c.imagenes_mes <= 0:
        return sin_plan()
    return await pagina(c, aviso="Guardamos tu personaje." if request.query_params.get("guardado") else "")


@router.post("/personaje")
async def guardar_personaje(request: Request, c: marco.Formulario):
    if c.imagenes_mes <= 0:
        return sin_plan()
    f = await request.form()
    p, accion = del_form(f), str(f.get("accion", "guardar"))
    if accion in ("azar", "azar_colores"):  # se muestra sin guardar: lo ajusta y guarda si le gusta
        return await pagina(c, al_azar(p, colores=accion == "azar_colores"),
                            "Este salió al azar. Cambia lo que quieras y guárdalo, o prueba otra vez.")
    antes = await de(c)
    for k in ("vida", "vidas"):  # las imágenes de «Dar vida» no vienen en el formulario: se conservan
        if antes.get(k):
            p[k] = antes[k]
    await c.store.set("personaje", json.dumps(p, ensure_ascii=False))
    log.info("@%s guardó su personaje", await marco.usuario(c))
    if accion == "dar_vida":
        if marco.en_curso(c, "personaje"):  # doble clic o recarga: el mismo trabajo, sin gastar otro intento
            pass
        elif not c.slug:  # la cuenta principal (@skandar.online) genera aunque se pase: decisión del dueño
            pass
        elif await sin_presupuesto(c):
            return await pagina(c, p, limites.SIN_PRESUPUESTO)
        elif len(vidas_del_mes(antes)) >= config.PERSONAJE_VIDAS_MES:
            return await pagina(c, p, f"Ya le diste vida a tu personaje {config.PERSONAJE_VIDAS_MES} veces este mes."
                                      " Podrás generarlo otra vez más adelante; el que ya tienes sigue aquí.")
        return await marco.trabajo_largo(
            c, "personaje", "Dando vida a tu personaje", "Generamos su versión en 3D y su hoja de poses.", BASE,
            dar_vida(c, p), menu=("cuenta", "marca"), boton="Ver tu personaje", segundos=110,
            pasos=("Modelando tu personaje en 3D…", "Dibujando su hoja de poses y expresiones…"))
    if accion == "aplicar":  # guarda y se queda (sin JavaScript, además repinta el personaje)
        return await pagina(c, p, "Cambios aplicados.")
    return RedirectResponse(f"{BASE}?guardado=1", status_code=303)
