"""Piezas de contenido: la imagen la pinta OpenAI (GPT Image) y el texto lo pone el servidor.

El generador recibe la idea (y, si hay, las fotos reales del producto) y devuelve una
imagen sin letras. El título, el precio y la firma los escribe Pillow encima con
una plantilla de la marca: así el precio sale exacto, con acentos, siempre en la
misma tipografía, y cambiar el texto no cuesta otra imagen (ver rerotular()).

Cada cuenta tiene un cupo de imágenes al mes (IMAGES_PER_MONTH; 0 = su plan no
incluye contenido). Pasado el cupo, solo se genera si el cliente aceptó pagar
la imagen extra; cada imagen queda anotada en `events` ('imagen') para cobrarla.

Las imágenes se generan de una en una (el servidor tiene poca memoria) y fuera
del webhook: quien llame a crear_pieza() lo hace desde una tarea aparte.
"""
import asyncio
import base64
import io
import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from . import config, limites, tools
from .cuenta import cuenta
from .store import Store

log = logging.getLogger("igbot.imagenes")

FONTS = Path(__file__).resolve().parent.parent / "fonts"
ANCHO, ALTO = 1080, 1350  # 4:5, lo más alto que acepta el feed de Instagram
ALTO_HISTORIA = 1920  # 9:16, la pantalla completa de una historia
# En una historia Instagram tapa abajo la caja de responder: el texto termina antes.
ABAJO_HISTORIA = 340
MARGEN = 84
ACENTO = "#C2410C"


class ErrorImagen(Exception):
    """Algo que se le puede decir al cliente tal cual."""


class SinCupo(ErrorImagen):
    pass


# --- proveedor ---

class OpenAI:
    """Imágenes con GPT Image (platform.openai.com). Sin fotos se usa images/generations;
    con fotos de referencia, images/edits (las fotos van como image[]). El tope de gasto lo
    pone Skandar en su cuenta de OpenAI: si se alcanza, OpenAI contesta con error de cuota
    y no se genera nada."""

    API = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str, size: str = "1024x1536", quality: str = "medium"):
        self.api_key, self.model, self.size, self.quality = api_key, model, size, quality

    async def generar(self, prompt: str, fotos: list[bytes] = ()) -> bytes:
        if not self.api_key:
            raise ErrorImagen("Falta OPENAI_API_KEY en la configuración del servidor.")
        campos = {"model": self.model, "prompt": prompt, "size": self.size, "quality": self.quality,
                  "n": "1", "output_format": "jpeg"}
        cabeceras = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=180) as http:
            if fotos:
                r = await http.post(f"{self.API}/images/edits", headers=cabeceras, data=campos,
                                    files=[("image[]", (f"ref{i}.jpg", f, "image/jpeg")) for i, f in enumerate(fotos)])
            else:
                r = await http.post(f"{self.API}/images/generations", headers=cabeceras,
                                    json={**campos, "n": 1})
        if r.status_code != 200:
            try:
                error = r.json().get("error") or {}
            except ValueError:
                error = {}
            codigo = f"{error.get('code') or ''} {error.get('type') or ''}"
            if "quota" in codigo or "billing" in codigo:
                log.error("OpenAI sin saldo o en el tope de gasto: %s", r.text[:300])
                raise ErrorImagen("El generador de imágenes no está disponible en este momento.")
            if "moderation" in codigo or "safety" in codigo or "content_policy" in codigo:
                raise ErrorImagen("Esa idea no se pudo ilustrar; prueba con otra descripción.")
            if r.status_code == 429:
                raise ErrorImagen("Hay muchas imágenes en proceso; intenta de nuevo en un minuto.")
            log.error("OpenAI respondió %s: %s", r.status_code, r.text[:500])
            raise ErrorImagen("No se pudo generar la imagen; intenta de nuevo.")
        datos = (r.json().get("data") or [{}])[0]
        if datos.get("b64_json"):
            return base64.b64decode(datos["b64_json"])
        log.warning("OpenAI no devolvió imagen: %s", r.text[:300])
        raise ErrorImagen("Esa idea no se pudo ilustrar; prueba con otra descripción.")


class Gemini:
    """Imágenes con la API de Gemini (aistudio.google.com). Ya no se usa (desde 2026-10-23 todo
    va con OpenAI); se deja por si se vuelve a él con IMAGE_PROVIDER=gemini. Cuenta de prepago:
    si se acaba el saldo, Google contesta 402 y no se genera nada."""

    API = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    async def generar(self, prompt: str, fotos: list[bytes] = ()) -> bytes:
        if not self.api_key:
            raise ErrorImagen("Falta GEMINI_API_KEY en la configuración del servidor.")
        partes = [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f).decode()}}
                  for f in fotos]
        cuerpo = {
            "contents": [{"parts": [*partes, {"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": "4:5"}},
        }
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(f"{self.API}/models/{self.model}:generateContent",
                                headers={"x-goog-api-key": self.api_key}, json=cuerpo)
        if r.status_code == 402:
            log.error("Gemini sin saldo: %s", r.text[:300])
            raise ErrorImagen("El generador de imágenes no está disponible en este momento.")
        if r.status_code == 429:
            raise ErrorImagen("Hay muchas imágenes en proceso; intenta de nuevo en un minuto.")
        if r.status_code != 200:
            log.error("Gemini respondió %s: %s", r.status_code, r.text[:500])
            raise ErrorImagen("No se pudo generar la imagen; intenta de nuevo.")
        datos = r.json()
        candidato = (datos.get("candidates") or [{}])[0]
        for parte in candidato.get("content", {}).get("parts", []):
            if "inlineData" in parte:
                return base64.b64decode(parte["inlineData"]["data"])
        log.warning("Gemini no devolvió imagen: %s / %s",
                    datos.get("promptFeedback"), candidato.get("finishReason"))
        raise ErrorImagen("Esa idea no se pudo ilustrar; prueba con otra descripción.")


# Para cambiar de proveedor basta otro objeto con el mismo generar().
proveedor = (Gemini(config.GEMINI_API_KEY, config.GEMINI_IMAGE_MODEL) if config.IMAGE_PROVIDER == "gemini"
             else OpenAI(config.OPENAI_API_KEY, config.OPENAI_IMAGE_MODEL, config.OPENAI_IMAGE_SIZE,
                         config.OPENAI_IMAGE_QUALITY))


# --- plantillas ---

@dataclass
class Texto:
    titulo: str
    subtitulo: str = ""
    precio: str = ""
    firma: str = ""  # p. ej. el @ de la cuenta


# Dónde dejar espacio vacío en la imagen para que el texto se lea.
ESPACIO = {
    "editorial": "Leave the lower third calm and uncluttered for a headline.",
    "minimal": "Leave the upper third as clean, empty background for a headline.",
    "marco": "Center the subject with some breathing room.",
    "banda": "Keep the subject in the upper two thirds.",
}
PLANTILLAS = tuple(ESPACIO)
# La que sale si el cliente no elige otra (la que escogió Skandar).
PLANTILLA_INICIAL = "editorial"


# OpenAI entrega 2:3 (1024x1536) y aquí se recorta a 4:5: se pierde un poco arriba y abajo.
RECORTE = "It will be cropped to 4:5, so keep every important element away from the top and bottom edges."
# Para una historia se recorta a 9:16: se pierde un poco a los lados.
RECORTE_HISTORIA = ("It is a full-screen Instagram story and will be cropped to 9:16, so keep every important element"
                    " away from the left and right edges, and keep the top 15% and bottom 20% calm.")
ESPACIO_HISTORIA = "Leave the lower third calm and uncluttered for a short headline."


def prompt_final(idea: str, plantilla: str, historia: bool = False) -> str:
    if historia:
        return (f"{idea.strip()}\n\nVertical full-screen Instagram story image. {RECORTE_HISTORIA} {ESPACIO_HISTORIA} "
                "Do not include any text, letters, numbers, logos or watermarks.")
    return (f"{idea.strip()}\n\nVertical Instagram image. {RECORTE} {ESPACIO[plantilla]} "
            "Do not include any text, letters, numbers, logos or watermarks.")


# Diseño libre: el generador arma el post completo, con el texto dentro del diseño.
# El texto no se puede cambiar después sin generar otra imagen.
LIBRE = "libre"


def prompt_libre(idea: str, texto: "Texto", marca: "Marca | None" = None, historia: bool = False) -> str:
    m = marca or Marca()
    lineas = [f'- Headline (largest): "{texto.titulo}"']
    if texto.subtitulo:
        lineas.append(f'- Subtitle (smaller): "{texto.subtitulo}"')
    if texto.precio:
        lineas.append(f'- Price or detail, in the accent color: "{texto.precio}"')
    if texto.firma:
        lineas.append(f'- Small signature at the bottom: "{texto.firma}"')
    que = "full-screen Instagram story" if historia else "vertical Instagram post"
    recorte = (f"{RECORTE_HISTORIA} Keep all text inside the central area." if historia
               else f"{RECORTE} Keep all text inside the central 4:5 area.")
    return (
        f"Design a finished, professional {que} (a graphic design, not just a photo).\n"
        f"{recorte}\n\n"
        f"Visual: {idea.strip()}\n\n"
        "Write exactly this text in the design, in Spanish, spelled exactly as given (keep every accent and"
        " punctuation mark), and no other words:\n" + "\n".join(lineas) + "\n\n"
        f"Typography: in the spirit of the {m.fuente} typeface, {'bold' if m.peso >= 700 else 'semibold' if m.peso >= 600 else 'regular'} headline,"
        " strong hierarchy, generous margins, readable on a phone.\n"
        f"Brand colors: main {m.principal}, accent {m.acento}, background {m.fondo}.\n"
        "No logos, watermarks, fake buttons or extra text."
    )


def prompt_infografia(idea: str, texto: "Texto", marca: "Marca | None" = None, historia: bool = False,
                      puntos: list[str] = ()) -> str:
    """Campañas: la IA diseña la pieza completa, estilo infografía, con el texto dentro (sin plantilla)."""
    m = marca or Marca()
    lineas = [f'- Headline (largest): "{texto.titulo}"'] if texto.titulo else []
    if texto.subtitulo:
        lineas.append(f'- Subtitle (smaller): "{texto.subtitulo}"')
    lineas += [f'- Short point (small, with a simple icon or graphic): "{x}"' for x in puntos]
    if texto.firma:
        lineas.append(f'- Small signature at the bottom: "{texto.firma}"')
    que = "full-screen Instagram story" if historia else "vertical Instagram post"
    recorte = (f"{RECORTE_HISTORIA} Keep all text inside the central area." if historia
               else f"{RECORTE} Keep all text inside the central 4:5 area.")
    return (
        f"Design a finished, professional infographic-style {que}: one complete graphic design with the text"
        " integrated in the layout (clear hierarchy, simple icons or data graphics where they help), not a photo with"
        f" a caption pasted on top.\n{recorte}\n\n"
        f"Design brief: {idea.strip()}\n\n"
        "Write exactly these texts in the design, in Spanish, spelled exactly as given (keep every accent and"
        " punctuation mark), and no other words, letters or numbers:\n" + "\n".join(lineas) + "\n\n"
        f"Typography: in the spirit of the {m.fuente} typeface, readable on a phone, generous margins.\n"
        f"Brand colors: main {m.principal}, accent {m.acento}, background {m.fondo}.\n"
        "No logos, watermarks or fake buttons."
    )


# Tipografías de títulos que puede elegir el cliente (archivos en fonts/, todas OFL).
# Nombre que ve el cliente -> archivo en fonts/ (Google Fonts, licencia OFL; 400, 600 y 700 cada una).
FUENTES = {"Inter": "Inter", "Montserrat": "Montserrat", "Poppins": "Poppins", "Raleway": "Raleway",
           "Nunito": "Nunito", "DM Sans": "DMSans", "Work Sans": "WorkSans", "Space Grotesk": "SpaceGrotesk", "Oswald": "Oswald",
           "Playfair Display": "PlayfairDisplay", "Lora": "Lora", "Cormorant Garamond": "CormorantGaramond"}
SERIF = {"Playfair Display", "Lora", "Cormorant Garamond"}
PESOS = {400: "Regular", 600: "SemiBold", 700: "Bold"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass
class Marca:
    """El kit de marca de la cuenta: con qué letra y colores se rotula todo."""
    fuente: str = "Inter"
    peso: int = 600
    principal: str = "#1D1D1F"  # texto sobre fondos claros
    acento: str = ACENTO  # precio y detalles; fondo de la plantilla Banda
    fondo: str = "#F7F5F1"  # fondo de la plantilla Marco

    @classmethod
    def de(cls, datos: dict) -> "Marca":
        """La marca guardada en el panel; lo que no sea válido se queda como viene de fábrica."""
        m = cls()
        if datos.get("fuente") in FUENTES:
            m.fuente = datos["fuente"]
        if str(datos.get("peso")) in map(str, PESOS):
            m.peso = int(datos["peso"])
        for campo in ("principal", "acento", "fondo"):
            if HEX.match(str(datos.get(campo, ""))):
                setattr(m, campo, datos[campo].upper())
        return m


def _fuente(tam: int, peso: int = 400, fuente: str = "Inter") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"{FUENTES[fuente]}-{PESOS[peso]}.ttf"), tam)


def _rgb(color: str) -> tuple[int, int, int]:
    color = (color or ACENTO).lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _lineas(texto: str, fuente, ancho: int) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if actual and fuente.getlength(prueba) > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    return lineas + ([actual] if actual else [])


def _titulo(texto: str, ancho: int, maximo: int, m: Marca, minimo: int = 48, renglones: int = 3):
    """La fuente más grande con la que el título cabe en `renglones` líneas."""
    tam = maximo
    while True:
        fuente = _fuente(tam, m.peso, m.fuente)
        lineas = _lineas(texto, fuente, ancho)
        if (len(lineas) <= renglones and all(fuente.getlength(l) <= ancho for l in lineas)) or tam <= minimo:
            return fuente, lineas
        tam -= 4


def _bloque(d: ImageDraw.ImageDraw, x: int, y: int, lineas: list[str], fuente, color, interlineado=1.12) -> int:
    """Escribe las líneas desde y (arriba) y devuelve dónde terminan."""
    alto = round(fuente.size * interlineado)
    for linea in lineas:
        d.text((x, y), linea, font=fuente, fill=color)
        y += alto
    return y


def _alto_bloque(lineas: list[str], fuente, interlineado=1.12) -> int:
    return round(fuente.size * interlineado) * len(lineas)


def _cubrir(img: Image.Image, ancho: int, alto: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (ancho, alto), Image.Resampling.LANCZOS)


def _degradado(img: Image.Image, desde: float, opacidad: int) -> None:
    """Oscurece la parte de abajo (de `desde` a 1) para que el texto blanco se lea."""
    alto = img.size[1]
    capa = Image.new("L", (1, alto))
    inicio = int(alto * desde)
    for y in range(alto):
        capa.putpixel((0, y), 0 if y < inicio else int(opacidad * ((y - inicio) / (alto - inicio)) ** 0.8))
    negro = Image.new("RGB", img.size, (0, 0, 0))
    img.paste(negro, (0, 0), capa.resize(img.size))


def _es_clara(img: Image.Image, caja: tuple[int, int, int, int]) -> bool:
    return ImageStat.Stat(img.crop(caja).convert("L")).mean[0] > 140


def _precio(m: Marca) -> ImageFont.FreeTypeFont:
    return _fuente(44, max(m.peso, 600), m.fuente)


def _textos(d, x, y, ancho, texto: Texto, m: Marca, color, secundario, acento, tam_titulo=96,
            color_precio=None) -> int:
    fuente, lineas = _titulo(texto.titulo, ancho, tam_titulo, m)
    y = _bloque(d, x, y, lineas, fuente, color)
    if texto.subtitulo:
        sub = _fuente(36, 400, m.fuente)
        y = _bloque(d, x, y + 18, _lineas(texto.subtitulo, sub, ancho)[:3], sub, secundario, 1.3)
    if texto.precio:
        y += 30
        d.rectangle((x, y, x + 44, y + 4), fill=acento)
        y = _bloque(d, x, y + 22, [texto.precio], _precio(m), color_precio or color)
    return y


def _alto_textos(ancho: int, texto: Texto, m: Marca, tam_titulo=96) -> int:
    fuente, lineas = _titulo(texto.titulo, ancho, tam_titulo, m)
    alto = _alto_bloque(lineas, fuente)
    if texto.subtitulo:
        sub = _fuente(36, 400, m.fuente)
        alto += 18 + _alto_bloque(_lineas(texto.subtitulo, sub, ancho)[:3], sub, 1.3)
    if texto.precio:
        alto += 30 + 22 + _alto_bloque(["x"], _precio(m))
    return alto


def _firma(d, texto: Texto, m: Marca, color, y: int = ALTO - MARGEN + 10) -> None:
    if texto.firma:
        d.text((MARGEN, y), texto.firma, font=_fuente(28, 400, m.fuente), fill=color, anchor="ls")


def _claro(color: tuple[int, int, int]) -> bool:
    r, g, b = color
    return 0.299 * r + 0.587 * g + 0.114 * b > 150


def es_historia(img: Image.Image) -> bool:
    """Una imagen de historia (9:16): mucho más alta que ancha."""
    return img.size[1] > img.size[0] * 1.6


def rotular(imagen: bytes, plantilla: str, texto: Texto, marca: Marca | None = None) -> bytes:
    """La pieza final en JPG 1080x1350: la imagen con el texto de la plantilla. Si la imagen es de historia
    (9:16), sale en 1080x1920 con el texto abajo, fuera de lo que tapa Instagram (rotular_historia)."""
    if plantilla not in PLANTILLAS:
        raise ValueError(f"Plantilla desconocida: {plantilla}")
    m = marca or Marca()
    base = ImageOps.exif_transpose(Image.open(io.BytesIO(imagen)))
    if es_historia(base):
        return rotular_historia(base, texto, m)
    principal, acento, fondo = _rgb(m.principal), _rgb(m.acento), _rgb(m.fondo)
    ancho_texto = ANCHO - 2 * MARGEN

    if plantilla == "editorial":
        img = _cubrir(base, ANCHO, ALTO)
        y = ALTO - MARGEN - 40 - _alto_textos(ancho_texto, texto, m)
        _degradado(img, min(0.5, max(0.2, (y - 220) / ALTO)), 225)  # empieza arriba del texto
        d = ImageDraw.Draw(img)
        # El detalle (precio) va en el acento si se lee sobre el degradado oscuro.
        claro = 0.299 * acento[0] + 0.587 * acento[1] + 0.114 * acento[2] > 90
        _textos(d, MARGEN, y, ancho_texto, texto, m, (255, 255, 255), (225, 225, 228), acento,
                color_precio=acento if claro else None)
        _firma(d, texto, m, (200, 200, 205))

    elif plantilla == "minimal":
        img = _cubrir(base, ANCHO, ALTO)
        clara = _es_clara(img, (0, 0, ANCHO, ALTO // 3))
        color, secundario = (principal, (90, 90, 95)) if clara else ((255, 255, 255), (230, 230, 232))
        d = ImageDraw.Draw(img)
        _textos(d, MARGEN, MARGEN + 10, ancho_texto, texto, m, color, secundario, acento)
        abajo_clara = _es_clara(img, (0, ALTO - 2 * MARGEN, ANCHO, ALTO))
        _firma(d, texto, m, (90, 90, 95) if abajo_clara else (230, 230, 232))

    elif plantilla == "marco":
        img = Image.new("RGB", (ANCHO, ALTO), fondo)
        alto_texto = _alto_textos(ancho_texto, texto, m, tam_titulo=72)
        alto_foto = ALTO - 2 * MARGEN - alto_texto - 56 - (40 if texto.firma else 0)
        img.paste(_cubrir(base, ancho_texto, alto_foto), (MARGEN, MARGEN))
        d = ImageDraw.Draw(img)
        # Si el fondo de la marca es oscuro, el texto va en blanco.
        color, secundario = (principal, (100, 100, 105)) if _claro(fondo) else ((255, 255, 255), (220, 220, 224))
        _textos(d, MARGEN, MARGEN + alto_foto + 48, ancho_texto, texto, m, color, secundario,
                acento, tam_titulo=72)
        _firma(d, texto, m, (120, 120, 125) if _claro(fondo) else (200, 200, 205), y=ALTO - MARGEN + 30)

    else:  # banda
        alto_banda = _alto_textos(ancho_texto, texto, m, tam_titulo=72) + 2 * 64 + (36 if texto.firma else 0)
        img = Image.new("RGB", (ANCHO, ALTO), acento)
        img.paste(_cubrir(base, ANCHO, ALTO - alto_banda), (0, 0))
        d = ImageDraw.Draw(img)
        # Sobre la banda de color, el precio se marca con el color del texto.
        color, secundario = ((255, 255, 255), (255, 240, 232)) if not _claro(acento) else (principal, (60, 60, 65))
        _textos(d, MARGEN, ALTO - alto_banda + 64, ancho_texto, texto, m, color, secundario,
                color, tam_titulo=72)
        _firma(d, texto, m, secundario, y=ALTO - 48)

    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def rotular_historia(base: Image.Image, texto: Texto, m: Marca) -> bytes:
    """Historia 1080x1920: la foto a pantalla completa, un degradado abajo y el título encima de la zona de
    responder (la misma para todas las plantillas: en una historia solo cabe una manera de leerla)."""
    img = _cubrir(base, ANCHO, ALTO_HISTORIA)
    ancho_texto = ANCHO - 2 * MARGEN
    y = ALTO_HISTORIA - ABAJO_HISTORIA - 40 - _alto_textos(ancho_texto, texto, m)
    _degradado(img, min(0.6, max(0.3, (y - 260) / ALTO_HISTORIA)), 225)
    d = ImageDraw.Draw(img)
    acento = _rgb(m.acento)
    claro = 0.299 * acento[0] + 0.587 * acento[1] + 0.114 * acento[2] > 90
    _textos(d, MARGEN, y, ancho_texto, texto, m, (255, 255, 255), (225, 225, 228), acento,
            color_precio=acento if claro else None)
    _firma(d, texto, m, (200, 200, 205), y=ALTO_HISTORIA - ABAJO_HISTORIA + 30)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


# --- cupo del mes ---

def _mes() -> str:
    return f"{tools.now():%Y-%m}"


async def usadas(store: Store) -> tuple[int, int]:
    """(incluidas en el plan, extra) que lleva la cuenta este mes."""
    return (int(await store.get(f"imagenes:{_mes()}") or 0),
            int(await store.get(f"imagenes_extra:{_mes()}") or 0))


async def _sumar(store: Store, clave: str, n: int) -> None:
    await store.set(clave, str(max(0, int(await store.get(clave) or 0) + n)))


async def reservar(store: Store, permitir_extra: bool = False) -> str:
    """Aparta una imagen del mes: 'incluida' o 'extra' (si el cliente aceptó pagarla)."""
    cupo = cuenta().imagenes_mes
    if cupo <= 0:
        raise SinCupo("Tu plan no incluye generación de contenido.")
    incluidas, _ = await usadas(store)
    if incluidas < cupo:
        await _sumar(store, f"imagenes:{_mes()}", 1)
        return "incluida"
    if not permitir_extra:
        raise SinCupo(f"Ya usaste las {cupo} imágenes de tu plan este mes.")
    await _sumar(store, f"imagenes_extra:{_mes()}", 1)
    return "extra"


async def devolver(store: Store, tipo: str) -> None:
    await _sumar(store, f"imagenes{'_extra' if tipo == 'extra' else ''}:{_mes()}", -1)


# --- piezas ---

# De una en una: cada imagen ocupa memoria al decodificarla y rotularla.
_turno = asyncio.Semaphore(1)
# Las llamadas al generador (esperan red, no ocupan memoria): dos a la vez como mucho.
_generando = asyncio.Semaphore(2)


def _archivos(token: str) -> tuple[Path, Path]:
    """(pieza final, que se publica; imagen sin texto, que no se sirve)."""
    carpeta = Path(cuenta().media_dir)
    return carpeta / f"{token}.jpg", carpeta / f"{token}.base.jpg"


def _jpg(data: bytes, historia: bool = False) -> bytes:
    out = io.BytesIO()
    _cubrir(Image.open(io.BytesIO(data)), ANCHO, ALTO_HISTORIA if historia else ALTO).save(out, "JPEG", quality=92)
    return out.getvalue()


async def crear_pieza(store: Store, user_id: str, idea: str, plantilla: str, texto: Texto,
                      fotos: list[bytes] = (), permitir_extra: bool = False, marca: Marca | None = None,
                      historia: bool = False, infografia: bool = False, puntos: list[str] = ()) -> dict:
    """Genera la imagen, la rotula y la deja en la tabla media (lista para publicar). Con `historia`, en 9:16.
    Con `infografia` (y plantilla LIBRE), la IA diseña la pieza con su texto y sus `puntos` dentro."""
    if plantilla not in PLANTILLAS and plantilla != LIBRE:
        raise ErrorImagen(f"Plantilla desconocida: {plantilla}")
    tipo = await reservar(store, permitir_extra)
    try:
        # La espera al generador va aparte: mientras tanto, las vistas previas siguen rotulando.
        async with _generando:
            prompt = (prompt_infografia(idea, texto, marca, historia, puntos) if plantilla == LIBRE and infografia
                      else prompt_libre(idea, texto, marca, historia) if plantilla == LIBRE
                      else prompt_final(idea, plantilla, historia))
            crudo = await proveedor.generar(prompt, list(fotos))
        async with _turno:
            base = await asyncio.to_thread(_jpg, crudo, historia)
            # En diseño libre el texto ya viene en el diseño, y una foto sin título va tal cual: no se rotula.
            final = (base if plantilla == LIBRE or not texto.titulo
                     else await asyncio.to_thread(rotular, base, plantilla, texto, marca))
    except Exception:
        await devolver(store, tipo)
        raise
    await limites.add_cost(config.IMAGE_COST_USD)
    hecha = await _guardar(store, user_id, final, None if plantilla == LIBRE else base,
                           f"{type(proveedor).__name__.lower()}:{getattr(proveedor, 'model', '')}")
    await store.add_event(user_id, "imagen", {"tipo": tipo, "media": hecha["media_id"], "plantilla": plantilla})
    log.info("Pieza %s (%s) para %s", hecha["media_id"], tipo, user_id)
    return {**hecha, "tipo": tipo}


async def _guardar(store: Store, user_id: str, final: bytes, base: bytes | None, origen: str) -> dict:
    token = secrets.token_urlsafe(18)
    pieza, sin_texto = _archivos(token)
    pieza.parent.mkdir(parents=True, exist_ok=True)
    if base is not None:  # sin texto, para poder cambiarlo gratis después
        sin_texto.write_bytes(base)
    pieza.write_bytes(final)
    return {"media_id": await store.add_media(user_id, token, origen), "token": token}


async def crear_subida(store: Store, user_id: str, data: bytes, plantilla: str, texto: Texto,
                       marca: Marca | None = None, historia: bool = False) -> dict:
    """Una foto que subió el dueño para su post: se recorta a 4:5 (9:16 si es historia) y, si lleva título, se
    rotula como las generadas. No usa el cupo de imágenes."""
    async with _turno:
        base = await asyncio.to_thread(_jpg, data, historia)
        final = (base if plantilla == LIBRE or not texto.titulo or plantilla not in PLANTILLAS
                 else await asyncio.to_thread(rotular, base, plantilla, texto, marca))
    hecha = await _guardar(store, user_id, final, base, "panel:subida")
    log.info("Pieza %s (subida, sin cupo) para %s", hecha["media_id"], user_id)
    return {**hecha, "tipo": "local"}


async def crear_local(store: Store, user_id: str, como: str, *args, historia: bool = False) -> dict:
    """Una pieza hecha aquí, sin generador (solo_texto o collage): no usa el cupo de imágenes."""
    hacer = {"texto": solo_texto, "collage": collage}[como]
    async with _turno:
        if historia and como == "collage":
            final = await asyncio.to_thread(collage, *args, alto=ALTO_HISTORIA)
        else:
            final = await asyncio.to_thread(hacer, *args)
            if historia:  # el solo texto, centrado sobre su mismo fondo
                final = await asyncio.to_thread(a_historia, final)
    hecha = await _guardar(store, user_id, final, None, f"panel:{como}")
    log.info("Pieza %s (%s, sin cupo) para %s", hecha["media_id"], como, user_id)
    return {**hecha, "tipo": "local"}


# --- piezas sin generador ---

def solo_texto(texto: Texto, marca: Marca | None = None, etiqueta: str = "") -> bytes:
    """Una frase o un dato sobre el fondo de la marca: la etiqueta arriba en el acento, el título grande
    y el subtítulo debajo, centrados a lo alto."""
    m = marca or Marca()
    fondo = _rgb(m.fondo)
    color, secundario = (_rgb(m.principal), (100, 100, 105)) if _claro(fondo) else ((255, 255, 255), (220, 220, 224))
    img = Image.new("RGB", (ANCHO, ALTO), fondo)
    d = ImageDraw.Draw(img)
    ancho = ANCHO - 2 * MARGEN
    fuente_et = _fuente(30, 600, m.fuente)
    alto_et = (fuente_et.size + 36) if etiqueta else 0
    y = (ALTO - alto_et - _alto_textos(ancho, Texto(texto.titulo, texto.subtitulo), m, tam_titulo=104)) // 2
    if etiqueta:
        d.text((MARGEN, y), etiqueta.upper(), font=fuente_et, fill=_rgb(m.acento))
        y += alto_et
    _textos(d, MARGEN, y, ancho, Texto(texto.titulo, texto.subtitulo), m, color, secundario, _rgb(m.acento),
            tam_titulo=104)
    _firma(d, texto, m, secundario)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def a_historia(pieza: bytes) -> bytes:
    """Un solo texto de 4:5 en 9:16: centrado sobre su mismo fondo (el color de la esquina)."""
    img = Image.open(io.BytesIO(pieza)).convert("RGB")
    fondo = img.getpixel((2, 2))
    lienzo = Image.new("RGB", (ANCHO, ALTO_HISTORIA), fondo)
    lienzo.paste(img, (0, (ALTO_HISTORIA - img.size[1]) // 2))
    out = io.BytesIO()
    lienzo.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def collage(fotos: list[bytes], alto: int = ALTO) -> bytes:
    """De 2 a 4 fotos en una cuadrícula con separación blanca: 2 una sobre otra, 3 una grande arriba y dos
    abajo, 4 en cuadrícula de 2x2. `alto`: ALTO_HISTORIA para una historia."""
    if not 2 <= len(fotos) <= 4:
        raise ErrorImagen("El collage necesita de 2 a 4 fotos.")
    imgs = [ImageOps.exif_transpose(Image.open(io.BytesIO(f))) for f in fotos]
    hueco, mitad_x, mitad_y = 10, (ANCHO - 10) // 2, (alto - 10) // 2
    if len(imgs) == 2:
        cajas = [(0, 0, ANCHO, mitad_y), (0, mitad_y + hueco, ANCHO, alto - mitad_y - hueco)]
    elif len(imgs) == 3:
        cajas = [(0, 0, ANCHO, mitad_y), (0, mitad_y + hueco, mitad_x, alto - mitad_y - hueco),
                 (mitad_x + hueco, mitad_y + hueco, ANCHO - mitad_x - hueco, alto - mitad_y - hueco)]
    else:
        cajas = [(x, y, w, h) for y, h in ((0, mitad_y), (mitad_y + hueco, alto - mitad_y - hueco))
                 for x, w in ((0, mitad_x), (mitad_x + hueco, ANCHO - mitad_x - hueco))]
    img = Image.new("RGB", (ANCHO, alto), (255, 255, 255))
    for foto, (x, y, w, h) in zip(imgs, cajas):
        img.paste(_cubrir(foto, w, h), (x, y))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


async def rerotular(store: Store, media_id: int, plantilla: str, texto: Texto, marca: Marca | None = None) -> None:
    """Cambia el texto o la plantilla de una pieza sin generar otra imagen (gratis)."""
    fila = await store.media(media_id)
    if not fila:
        raise ErrorImagen("No existe esa pieza.")
    pieza, sin_texto = _archivos(fila[0])
    if not sin_texto.is_file():
        raise ErrorImagen("Esa imagen no se generó aquí; no se le puede cambiar el texto.")
    async with _turno:
        final = await asyncio.to_thread(rotular, sin_texto.read_bytes(), plantilla, texto, marca)
    pieza.write_bytes(final)
