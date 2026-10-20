"""Piezas de contenido: la imagen la pinta Gemini y el texto lo pone el servidor.

Gemini recibe la idea (y, si hay, las fotos reales del producto) y devuelve una
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
MARGEN = 84
ACENTO = "#C2410C"
TINTA = (29, 29, 31)
BLANCO_CALIDO = (247, 245, 241)


class ErrorImagen(Exception):
    """Algo que se le puede decir al cliente tal cual."""


class SinCupo(ErrorImagen):
    pass


# --- proveedor ---

class Gemini:
    """Imágenes con la API de Gemini (aistudio.google.com). Cuenta de prepago:
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
proveedor = Gemini(config.GEMINI_API_KEY, config.GEMINI_IMAGE_MODEL)


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


def prompt_final(idea: str, plantilla: str) -> str:
    return (f"{idea.strip()}\n\nVertical 4:5 Instagram image. {ESPACIO[plantilla]} "
            "Do not include any text, letters, numbers, logos or watermarks.")


def _fuente(tam: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("Inter-SemiBold.ttf" if negrita else "Inter-Regular.ttf")), tam)


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


def _titulo(texto: str, ancho: int, maximo: int, minimo: int = 48, renglones: int = 3):
    """La fuente más grande con la que el título cabe en `renglones` líneas."""
    tam = maximo
    while True:
        fuente = _fuente(tam, negrita=True)
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
    capa = Image.new("L", (1, ALTO))
    inicio = int(ALTO * desde)
    for y in range(ALTO):
        capa.putpixel((0, y), 0 if y < inicio else int(opacidad * ((y - inicio) / (ALTO - inicio)) ** 0.8))
    negro = Image.new("RGB", img.size, (0, 0, 0))
    img.paste(negro, (0, 0), capa.resize(img.size))


def _es_clara(img: Image.Image, caja: tuple[int, int, int, int]) -> bool:
    return ImageStat.Stat(img.crop(caja).convert("L")).mean[0] > 140


def _textos(d, x, y, ancho, texto: Texto, color, secundario, acento, tam_titulo=96) -> int:
    fuente, lineas = _titulo(texto.titulo, ancho, tam_titulo)
    y = _bloque(d, x, y, lineas, fuente, color)
    if texto.subtitulo:
        sub = _fuente(36)
        y = _bloque(d, x, y + 18, _lineas(texto.subtitulo, sub, ancho)[:3], sub, secundario, 1.3)
    if texto.precio:
        y += 30
        d.rectangle((x, y, x + 44, y + 4), fill=acento)
        y = _bloque(d, x, y + 22, [texto.precio], _fuente(44, negrita=True), color)
    return y


def _alto_textos(ancho: int, texto: Texto, tam_titulo=96) -> int:
    fuente, lineas = _titulo(texto.titulo, ancho, tam_titulo)
    alto = _alto_bloque(lineas, fuente)
    if texto.subtitulo:
        sub = _fuente(36)
        alto += 18 + _alto_bloque(_lineas(texto.subtitulo, sub, ancho)[:3], sub, 1.3)
    if texto.precio:
        alto += 30 + 22 + _alto_bloque(["x"], _fuente(44, negrita=True))
    return alto


def _firma(d, texto: Texto, color, y: int = ALTO - MARGEN + 10) -> None:
    if texto.firma:
        d.text((MARGEN, y), texto.firma, font=_fuente(28), fill=color, anchor="ls")


def rotular(imagen: bytes, plantilla: str, texto: Texto, acento: str = "") -> bytes:
    """La pieza final en JPG 1080x1350: la imagen con el texto de la plantilla."""
    if plantilla not in PLANTILLAS:
        raise ValueError(f"Plantilla desconocida: {plantilla}")
    base = ImageOps.exif_transpose(Image.open(io.BytesIO(imagen)))
    color_acento = _rgb(acento)
    ancho_texto = ANCHO - 2 * MARGEN

    if plantilla == "editorial":
        img = _cubrir(base, ANCHO, ALTO)
        y = ALTO - MARGEN - 40 - _alto_textos(ancho_texto, texto)
        _degradado(img, min(0.5, max(0.2, (y - 220) / ALTO)), 225)  # empieza arriba del texto
        d = ImageDraw.Draw(img)
        _textos(d, MARGEN, y, ancho_texto, texto, (255, 255, 255), (225, 225, 228), color_acento)
        _firma(d, texto, (200, 200, 205))

    elif plantilla == "minimal":
        img = _cubrir(base, ANCHO, ALTO)
        clara = _es_clara(img, (0, 0, ANCHO, ALTO // 3))
        color, secundario = (TINTA, (90, 90, 95)) if clara else ((255, 255, 255), (230, 230, 232))
        d = ImageDraw.Draw(img)
        _textos(d, MARGEN, MARGEN + 10, ancho_texto, texto, color, secundario, color_acento)
        abajo_clara = _es_clara(img, (0, ALTO - 2 * MARGEN, ANCHO, ALTO))
        _firma(d, texto, (90, 90, 95) if abajo_clara else (230, 230, 232))

    elif plantilla == "marco":
        img = Image.new("RGB", (ANCHO, ALTO), BLANCO_CALIDO)
        alto_texto = _alto_textos(ancho_texto, texto, tam_titulo=72)
        alto_foto = ALTO - 2 * MARGEN - alto_texto - 56 - (40 if texto.firma else 0)
        img.paste(_cubrir(base, ancho_texto, alto_foto), (MARGEN, MARGEN))
        d = ImageDraw.Draw(img)
        _textos(d, MARGEN, MARGEN + alto_foto + 48, ancho_texto, texto, TINTA, (100, 100, 105),
                color_acento, tam_titulo=72)
        _firma(d, texto, (120, 120, 125), y=ALTO - MARGEN + 30)

    else:  # banda
        alto_banda = _alto_textos(ancho_texto, texto, tam_titulo=72) + 2 * 64 + (36 if texto.firma else 0)
        img = Image.new("RGB", (ANCHO, ALTO), color_acento)
        img.paste(_cubrir(base, ANCHO, ALTO - alto_banda), (0, 0))
        d = ImageDraw.Draw(img)
        # Sobre la banda de color, el precio se marca en blanco.
        _textos(d, MARGEN, ALTO - alto_banda + 64, ancho_texto, texto, (255, 255, 255), (255, 240, 232),
                (255, 255, 255), tam_titulo=72)
        _firma(d, texto, (255, 235, 225), y=ALTO - 48)

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


def _archivos(token: str) -> tuple[Path, Path]:
    """(pieza final, que se publica; imagen sin texto, que no se sirve)."""
    carpeta = Path(cuenta().media_dir)
    return carpeta / f"{token}.jpg", carpeta / f"{token}.base.jpg"


def _jpg(data: bytes) -> bytes:
    out = io.BytesIO()
    _cubrir(Image.open(io.BytesIO(data)), ANCHO, ALTO).save(out, "JPEG", quality=92)
    return out.getvalue()


async def crear_pieza(store: Store, user_id: str, idea: str, plantilla: str, texto: Texto,
                      fotos: list[bytes] = (), permitir_extra: bool = False, acento: str = "") -> dict:
    """Genera la imagen, la rotula y la deja en la tabla media (lista para publicar)."""
    if plantilla not in PLANTILLAS:
        raise ErrorImagen(f"Plantilla desconocida: {plantilla}")
    tipo = await reservar(store, permitir_extra)
    try:
        async with _turno:
            crudo = await proveedor.generar(prompt_final(idea, plantilla), list(fotos))
            base = await asyncio.to_thread(_jpg, crudo)
            final = await asyncio.to_thread(rotular, base, plantilla, texto, acento)
    except Exception:
        await devolver(store, tipo)
        raise
    await limites.add_cost(config.IMAGE_COST_USD)
    token = secrets.token_urlsafe(18)
    pieza, sin_texto = _archivos(token)
    pieza.parent.mkdir(parents=True, exist_ok=True)
    sin_texto.write_bytes(base)
    pieza.write_bytes(final)
    media_id = await store.add_media(user_id, token, f"{type(proveedor).__name__.lower()}:{getattr(proveedor, 'model', '')}")
    await store.add_event(user_id, "imagen", {"tipo": tipo, "media": media_id, "plantilla": plantilla})
    log.info("Pieza %s (%s) para %s", media_id, tipo, user_id)
    return {"media_id": media_id, "token": token, "tipo": tipo}


async def rerotular(store: Store, media_id: int, plantilla: str, texto: Texto, acento: str = "") -> None:
    """Cambia el texto o la plantilla de una pieza sin generar otra imagen (gratis)."""
    fila = await store.media(media_id)
    if not fila:
        raise ErrorImagen("No existe esa pieza.")
    pieza, sin_texto = _archivos(fila[0])
    if not sin_texto.is_file():
        raise ErrorImagen("Esa imagen no se generó aquí; no se le puede cambiar el texto.")
    async with _turno:
        final = await asyncio.to_thread(rotular, sin_texto.read_bytes(), plantilla, texto, acento)
    pieza.write_bytes(final)
