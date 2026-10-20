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
MARGEN = 84
ACENTO = "#C2410C"


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


# Diseño libre: el generador arma el post completo, con el texto dentro del diseño.
# El texto no se puede cambiar después sin generar otra imagen.
LIBRE = "libre"


def prompt_libre(idea: str, texto: "Texto", marca: "Marca | None" = None) -> str:
    m = marca or Marca()
    lineas = [f'- Headline (largest): "{texto.titulo}"']
    if texto.subtitulo:
        lineas.append(f'- Subtitle (smaller): "{texto.subtitulo}"')
    if texto.precio:
        lineas.append(f'- Price or detail, in the accent color: "{texto.precio}"')
    if texto.firma:
        lineas.append(f'- Small signature at the bottom: "{texto.firma}"')
    return (
        "Design a finished, professional vertical 4:5 Instagram post (a graphic design, not just a photo).\n\n"
        f"Visual: {idea.strip()}\n\n"
        "Write exactly this text in the design, in Spanish, spelled exactly as given (keep every accent and"
        " punctuation mark), and no other words:\n" + "\n".join(lineas) + "\n\n"
        f"Typography: in the spirit of the {m.fuente} typeface, {'bold' if m.peso >= 700 else 'semibold' if m.peso >= 600 else 'regular'} headline,"
        " strong hierarchy, generous margins, readable on a phone.\n"
        f"Brand colors: main {m.principal}, accent {m.acento}, background {m.fondo}.\n"
        "No logos, watermarks, fake buttons or extra text."
    )


# Tipografías de títulos que puede elegir el cliente (archivos en fonts/, todas OFL).
FUENTES = {"Inter": "Inter", "Montserrat": "Montserrat", "Poppins": "Poppins",
           "Playfair Display": "PlayfairDisplay", "Lora": "Lora"}
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
    capa = Image.new("L", (1, ALTO))
    inicio = int(ALTO * desde)
    for y in range(ALTO):
        capa.putpixel((0, y), 0 if y < inicio else int(opacidad * ((y - inicio) / (ALTO - inicio)) ** 0.8))
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


def rotular(imagen: bytes, plantilla: str, texto: Texto, marca: Marca | None = None) -> bytes:
    """La pieza final en JPG 1080x1350: la imagen con el texto de la plantilla."""
    if plantilla not in PLANTILLAS:
        raise ValueError(f"Plantilla desconocida: {plantilla}")
    m = marca or Marca()
    base = ImageOps.exif_transpose(Image.open(io.BytesIO(imagen)))
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
# Las llamadas a Gemini (esperan red, no ocupan memoria): dos a la vez como mucho.
_generando = asyncio.Semaphore(2)


def _archivos(token: str) -> tuple[Path, Path]:
    """(pieza final, que se publica; imagen sin texto, que no se sirve)."""
    carpeta = Path(cuenta().media_dir)
    return carpeta / f"{token}.jpg", carpeta / f"{token}.base.jpg"


def _jpg(data: bytes) -> bytes:
    out = io.BytesIO()
    _cubrir(Image.open(io.BytesIO(data)), ANCHO, ALTO).save(out, "JPEG", quality=92)
    return out.getvalue()


async def crear_pieza(store: Store, user_id: str, idea: str, plantilla: str, texto: Texto,
                      fotos: list[bytes] = (), permitir_extra: bool = False, marca: Marca | None = None) -> dict:
    """Genera la imagen, la rotula y la deja en la tabla media (lista para publicar)."""
    if plantilla not in PLANTILLAS and plantilla != LIBRE:
        raise ErrorImagen(f"Plantilla desconocida: {plantilla}")
    tipo = await reservar(store, permitir_extra)
    try:
        # La espera a Gemini va aparte: mientras tanto, las vistas previas siguen rotulando.
        async with _generando:
            prompt = prompt_libre(idea, texto, marca) if plantilla == LIBRE else prompt_final(idea, plantilla)
            crudo = await proveedor.generar(prompt, list(fotos))
        async with _turno:
            base = await asyncio.to_thread(_jpg, crudo)
            # En diseño libre el texto ya viene en el diseño: no se rotula.
            final = base if plantilla == LIBRE else await asyncio.to_thread(rotular, base, plantilla, texto, marca)
    except Exception:
        await devolver(store, tipo)
        raise
    await limites.add_cost(config.IMAGE_COST_USD)
    token = secrets.token_urlsafe(18)
    pieza, sin_texto = _archivos(token)
    pieza.parent.mkdir(parents=True, exist_ok=True)
    if plantilla != LIBRE:  # sin texto, para poder cambiarlo gratis después
        sin_texto.write_bytes(base)
    pieza.write_bytes(final)
    media_id = await store.add_media(user_id, token, f"{type(proveedor).__name__.lower()}:{getattr(proveedor, 'model', '')}")
    await store.add_event(user_id, "imagen", {"tipo": tipo, "media": media_id, "plantilla": plantilla})
    log.info("Pieza %s (%s) para %s", media_id, tipo, user_id)
    return {"media_id": media_id, "token": token, "tipo": tipo}


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
