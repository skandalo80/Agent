"""Piezas de contenido: plantillas, cupo del mes y el proveedor de imágenes."""
import asyncio
import base64
import io
import json
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from igbot import imagenes  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from igbot.store import Store  # noqa: E402

TEXTO = imagenes.Texto("Envíos gratis a todo México en compras mayores a mil pesos",
                       "Solo esta semana.", "Desde $1,290 MXN", "@negocio")


def foto(w=900, h=1100) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 190, 180)).save(out, "PNG")
    return out.getvalue()


class Falso:
    model = "falso"

    def __init__(self, falla=None):
        self.llamadas, self.falla = [], falla

    async def generar(self, prompt, fotos=(), fiel=False):
        self.llamadas.append((prompt, fotos))
        self.fiel = fiel
        if self.falla:
            raise self.falla
        return foto()


async def nueva_base(monkeypatch, tmp_path, cupo):
    store = Store(":memory:")
    await store.open()
    monkeypatch.setattr(cuenta(), "store", store)
    monkeypatch.setattr(cuenta(), "media_dir", str(tmp_path))
    monkeypatch.setattr(cuenta(), "imagenes_mes", cupo)
    return store


@pytest.mark.parametrize("plantilla", imagenes.PLANTILLAS)
def test_cada_plantilla_da_un_jpg_de_feed(plantilla):
    img = Image.open(io.BytesIO(imagenes.rotular(foto(1600, 900), plantilla, TEXTO, imagenes.Marca(acento="#1F3A5F"))))
    assert (img.format, img.size) == ("JPEG", (1080, 1350))


def test_la_plantilla_inicial_es_editorial():
    assert imagenes.PLANTILLA_INICIAL == "editorial" in imagenes.PLANTILLAS


def test_el_prompt_pide_imagen_sin_letras():
    prompt = imagenes.prompt_final("Cartera sobre una mesa", "minimal")
    assert prompt.startswith("Cartera sobre una mesa")
    assert "upper third" in prompt and "Do not include any text" in prompt


def test_cupo_del_mes(monkeypatch, tmp_path):
    async def main():
        store = await nueva_base(monkeypatch, tmp_path, 2)
        assert [await imagenes.reservar(store) for _ in range(2)] == ["incluida", "incluida"]
        with pytest.raises(imagenes.SinCupo, match="las 2 imágenes"):
            await imagenes.reservar(store)
        assert await imagenes.reservar(store, permitir_extra=True) == "extra"
        assert await imagenes.usadas(store) == (2, 1)

    asyncio.run(main())


def test_sin_contenido_en_el_plan_ni_pagando_extra(monkeypatch, tmp_path):
    async def main():
        store = await nueva_base(monkeypatch, tmp_path, 0)
        with pytest.raises(imagenes.SinCupo, match="no incluye"):
            await imagenes.reservar(store, permitir_extra=True)

    asyncio.run(main())


def test_crear_pieza_la_deja_lista_para_publicar(monkeypatch, tmp_path):
    proveedor = Falso()
    monkeypatch.setattr(imagenes, "proveedor", proveedor)

    async def main():
        store = await nueva_base(monkeypatch, tmp_path, 5)
        pieza = await imagenes.crear_pieza(store, "admin", "Cartera", "banda", TEXTO, fotos=[b"real"])
        token, url, _ = await store.media(pieza["media_id"])
        assert token == pieza["token"] and url == "falso:falso" and pieza["tipo"] == "incluida"
        assert Image.open(tmp_path / f"{token}.jpg").size == (1080, 1350)
        assert (tmp_path / f"{token}.base.jpg").is_file()  # sin texto, para cambiarlo gratis
        assert proveedor.llamadas[0][1] == [b"real"]
        assert await imagenes.usadas(store) == (1, 0)
        assert len(await store.events("imagen", 0, 1e12)) == 1

        antes = (tmp_path / f"{token}.jpg").read_bytes()
        await imagenes.rerotular(store, pieza["media_id"], "marco", imagenes.Texto("Otro título"))
        assert (tmp_path / f"{token}.jpg").read_bytes() != antes
        assert len(proveedor.llamadas) == 1 and await imagenes.usadas(store) == (1, 0)

    asyncio.run(main())


def test_si_falla_la_imagen_no_gasta_cupo(monkeypatch, tmp_path):
    monkeypatch.setattr(imagenes, "proveedor", Falso(imagenes.ErrorImagen("no")))

    async def main():
        store = await nueva_base(monkeypatch, tmp_path, 5)
        with pytest.raises(imagenes.ErrorImagen):
            await imagenes.crear_pieza(store, "admin", "Cartera", "editorial", TEXTO)
        assert await imagenes.usadas(store) == (0, 0)
        assert list(tmp_path.iterdir()) == []

    asyncio.run(main())


def gemini_que_responde(monkeypatch, status, cuerpo):
    pedidos = []

    def responder(request):
        pedidos.append(request)
        return httpx.Response(status, json=cuerpo)

    real = httpx.AsyncClient
    monkeypatch.setattr(imagenes.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(responder), **kw))
    return imagenes.Gemini("clave", "gemini-3.1-flash-image"), pedidos


def test_gemini_devuelve_la_imagen(monkeypatch):
    png = foto()
    gemini, pedidos = gemini_que_responde(monkeypatch, 200, {"candidates": [{"content": {"parts": [
        {"text": "Aquí está"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode()}}]}}]})
    assert asyncio.run(gemini.generar("idea", [b"foto"])) == png
    assert pedidos[0].headers["x-goog-api-key"] == "clave"
    assert "gemini-3.1-flash-image:generateContent" in str(pedidos[0].url)


def openai_que_responde(monkeypatch, status, cuerpo):
    pedidos = []

    def responder(request):
        pedidos.append(request)
        return httpx.Response(status, json=cuerpo)

    real = httpx.AsyncClient
    monkeypatch.setattr(imagenes.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(responder), **kw))
    return imagenes.OpenAI("clave", "gpt-image-2.5-flare"), pedidos


def test_el_proveedor_es_openai():
    assert isinstance(imagenes.proveedor, imagenes.OpenAI)


def test_openai_genera_sin_fotos(monkeypatch):
    jpg = foto()
    ia, pedidos = openai_que_responde(monkeypatch, 200, {"data": [{"b64_json": base64.b64encode(jpg).decode()}]})
    assert asyncio.run(ia.generar("idea")) == jpg
    assert str(pedidos[0].url).endswith("/v1/images/generations")
    assert pedidos[0].headers["authorization"] == "Bearer clave"
    cuerpo = json.loads(pedidos[0].content)
    assert cuerpo["model"] == "gpt-image-2.5-flare" and cuerpo["size"] == "1024x1536" and cuerpo["n"] == 1


def test_openai_con_fotos_usa_edits(monkeypatch):
    jpg = foto()
    ia, pedidos = openai_que_responde(monkeypatch, 200, {"data": [{"b64_json": base64.b64encode(jpg).decode()}]})
    assert asyncio.run(ia.generar("idea", [b"foto1", b"foto2"])) == jpg
    assert str(pedidos[0].url).endswith("/v1/images/edits")
    cuerpo = pedidos[0].content
    assert cuerpo.count(b'name="image[]"') == 2 and b"gpt-image-2.5-flare" in cuerpo


@pytest.mark.parametrize("status,cuerpo,mensaje", [
    (429, {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}, "no está disponible"),
    (400, {"error": {"code": "billing_hard_limit_reached", "type": "invalid_request_error"}}, "no está disponible"),
    (400, {"error": {"code": "moderation_blocked", "type": "image_generation_user_error"}}, "otra descripción"),
    (429, {"error": {"code": "rate_limit_exceeded"}}, "intenta de nuevo en un minuto"),
    (500, {}, "No se pudo generar"),
    (200, {"data": []}, "otra descripción"),
])
def test_errores_de_openai_con_mensaje_para_el_cliente(monkeypatch, status, cuerpo, mensaje):
    ia, _ = openai_que_responde(monkeypatch, status, cuerpo)
    with pytest.raises(imagenes.ErrorImagen, match=mensaje):
        asyncio.run(ia.generar("idea"))


def test_openai_sin_clave():
    with pytest.raises(imagenes.ErrorImagen, match="OPENAI_API_KEY"):
        asyncio.run(imagenes.OpenAI("", "gpt-image-2.5-flare").generar("idea"))


@pytest.mark.parametrize("status,cuerpo,mensaje", [
    (402, {"error": {"message": "Your prepayment credits are depleted."}}, "no está disponible"),
    (429, {}, "intenta de nuevo en un minuto"),
    (200, {"candidates": [{"finishReason": "IMAGE_SAFETY"}]}, "prueba con otra descripción"),
])
def test_errores_de_gemini_con_mensaje_para_el_cliente(monkeypatch, status, cuerpo, mensaje):
    gemini, _ = gemini_que_responde(monkeypatch, status, cuerpo)
    with pytest.raises(imagenes.ErrorImagen, match=mensaje):
        asyncio.run(gemini.generar("idea"))


def test_las_historias_salen_en_9_16():
    base = imagenes._jpg(foto(900, 1100), historia=True)
    assert Image.open(io.BytesIO(base)).size == (1080, 1920)
    # Rotular una base de historia (Generar, el editor o su vista previa) la deja en 9:16, con cualquier plantilla.
    for plantilla in imagenes.PLANTILLAS:
        assert Image.open(io.BytesIO(imagenes.rotular(base, plantilla, TEXTO))).size == (1080, 1920)
    texto = imagenes.a_historia(imagenes.solo_texto(TEXTO, imagenes.Marca(fondo="#112233")))
    img = Image.open(io.BytesIO(texto))
    assert img.size == (1080, 1920) and img.getpixel((5, 5)) == img.getpixel((5, 1910))  # el mismo fondo
    assert Image.open(io.BytesIO(imagenes.collage([foto(), foto()], alto=imagenes.ALTO_HISTORIA))).size == (1080, 1920)
    assert "9:16" in imagenes.prompt_final("Una taza", "editorial", historia=True)
    assert "story" in imagenes.prompt_libre("Una taza", TEXTO, historia=True)


def test_historia_con_texto_no_se_corta_de_los_lados():
    """Un diseño 2:3 en historia queda a lo ancho completo, con franjas de su mismo color arriba y abajo."""
    from PIL import Image
    img = Image.new("RGB", (1024, 1536), (20, 20, 24))
    img.paste((255, 0, 0), (0, 760, 1024, 776))  # una franja roja de borde a borde: no debe perderse ningún extremo
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = Image.open(io.BytesIO(imagenes._historia_completa(buf.getvalue())))
    assert out.size == (imagenes.ANCHO, imagenes.ALTO_HISTORIA)
    y = imagenes.ALTO_HISTORIA // 2
    assert out.getpixel((2, y))[0] > 150 and out.getpixel((imagenes.ANCHO - 3, y))[0] > 150
    assert max(abs(a - b) for a, b in zip(out.getpixel((10, 5)), (20, 20, 24))) < 8
