"""Catálogo dentro del chat: tarjetas de productos que se deslizan, sus botones y las respuestas rápidas."""
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as bot
import test_contenido
from igbot import catalogo_chat, config, marco, tienda_shopify
from igbot.instagram import Instagram
from igbot.store import Store
from test_contenido import ORIGEN

cliente = test_contenido.cliente  # un cliente conectado con su panel
PUBLICO = "https://console.skandar.online"
PRODUCTOS = [
    {"id": "a1", "nombre": "Bolsa tote de piel", "precio": "1,450", "moneda": "MXN", "opciones": ["Café", "Negro"],
     "imagenes": ["f1", "f2", "f3"], "url": "https://cafelunar.mx/products/tote"},
    {"id": "b2", "nombre": "Bolsa cruzada", "precio": "980", "moneda": "MXN", "imagenes": ["f4"],
     "url": "https://otra.com/cruzada", "disponible": False},
    {"id": "c3", "nombre": "Taller de piel", "precio": "450", "desde": True, "moneda": "MXN"},
]


class IgFalso:
    def __init__(self):
        self.tarjetas, self.fotos, self.textos = [], [], []

    async def send_cards(self, recipient_id, tarjetas, rapidas=None):
        self.tarjetas.append((recipient_id, tarjetas, rapidas))

    async def send_image(self, recipient_id, url):
        self.fotos.append((recipient_id, url))

    async def send_text(self, recipient_id, text, humano=False):
        self.textos.append((recipient_id, text))


@pytest.fixture
def tienda(cuenta_de_prueba, monkeypatch, tmp_path):
    """La cuenta con tres productos (fotos en disco) y su Shopify conectado en cafelunar.mx."""
    c, correr = cuenta_de_prueba
    monkeypatch.setattr(config, "PUBLIC_URL", PUBLICO)
    monkeypatch.setattr(c, "media_dir", str(tmp_path))
    for t in ("f1", "f2", "f3", "f4"):
        Image.new("RGB", (4, 4)).save(tmp_path / f"{t}.jpg")
    correr(c.store.set("productos", json.dumps(PRODUCTOS)))
    correr(c.store.set("shopify_conexion", json.dumps({"tienda": "lunar.myshopify.com", "token": "t",
                                                       "dominios": ["cafelunar.mx"]})))
    consultas = []

    async def consultar(con, query, variables=None):
        consultas.append(query)
        return {"p0": {"nodes": [{"title": "Bolsa tote de piel", "status": "ACTIVE", "tracksInventory": True,
                                  "totalInventory": 2}]}, "p1": {"nodes": []}}

    monkeypatch.setattr(tienda_shopify, "consultar", consultar)
    return c, correr, consultas


def test_elige_solo_productos_del_catalogo_y_arma_las_tarjetas(tienda):
    c, correr, consultas = tienda
    texto = correr(catalogo_chat.elegir(c, "ana", ["bolsa TOTE de piel", "Bolsa cruzada", "Zapatos rojos"],
                                        ["Envíos", "Pagar aquí", "Envíos", "Una respuesta demasiado larga", "x"]))
    assert "2 tarjeta(s)" in texto and "No están en el catálogo (no se mandan): Zapatos rojos" in texto
    ig = IgFalso()
    elegido = catalogo_chat.tomar(c, "ana")
    linea = correr(catalogo_chat.enviar(c, ig, "ana", "ana", elegido))
    assert linea == "[Tarjetas: Bolsa tote de piel, Bolsa cruzada]"
    assert catalogo_chat.tomar(c, "ana") is None  # se toma una sola vez
    _, tarjetas, rapidas = ig.tarjetas[0]
    assert rapidas == ["Envíos", "Pagar aquí", "Una respuesta demas…"]
    tote, cruzada = tarjetas
    codigo = correr(c.store.codigo_chat("ana"))
    assert tote["title"] == "Bolsa tote de piel"
    # Precio y opciones de Tu empresa; si hay, de su Shopify en este momento (2 en existencia).
    assert tote["subtitle"] == "$1,450 MXN · Café, Negro · Quedan pocas"
    assert tote["image_url"].startswith(f"{PUBLICO}/media/{c.media_slug}/f1.jpg?v=")
    # Su tienda conectada lleva el código del chat; las 3 opciones porque tiene más de una foto.
    assert [b["title"] for b in tote["buttons"]] == ["Ver en la tienda", "Lo quiero", "Ver más fotos"]
    assert tote["buttons"][0]["url"] == f"https://cafelunar.mx/products/tote?sk={codigo}"
    assert tote["default_action"]["url"] == tote["buttons"][0]["url"]
    assert tote["buttons"][1]["payload"] == "SK_QUIERO:a1" and tote["buttons"][2]["payload"] == "SK_FOTOS:a1"
    # Agotada al importarla: sin "Lo quiero"; otro dominio, sin código; una sola foto, sin "Ver más fotos".
    assert cruzada["subtitle"] == "$980 MXN · Agotado"
    assert [b["title"] for b in cruzada["buttons"]] == ["Ver en la tienda"]
    assert cruzada["buttons"][0]["url"] == "https://otra.com/cruzada"
    # Una sola consulta a Shopify para todas las tarjetas, con el nombre como cadena.
    assert len(consultas) == 1 and '"title:\\"Bolsa tote de piel\\""' in consultas[0]


def test_sin_productos_conocidos_no_hay_tarjetas(tienda):
    c, correr, _ = tienda
    assert "No encontré esos productos" in correr(catalogo_chat.elegir(c, "ana", ["Zapatos"], []))
    assert catalogo_chat.tomar(c, "ana") is None
    # "Bolsa" es ambiguo (dos productos): no se adivina.
    assert "No encontré" in correr(catalogo_chat.elegir(c, "ana", ["Bolsa"], []))


def test_sin_foto_ni_tienda_la_tarjeta_sale_con_lo_que_hay(tienda, monkeypatch):
    c, correr, _ = tienda
    monkeypatch.setattr(tienda_shopify, "consultar", None)  # no se llama: no hay Shopify
    correr(c.store.set("shopify_conexion", ""))
    t = correr(catalogo_chat.tarjeta(c, "ana", PRODUCTOS[2]))
    assert t == {"title": "Taller de piel", "subtitle": "Desde $450 MXN",
                 "buttons": [{"type": "postback", "title": "Lo quiero", "payload": "SK_QUIERO:c3"}]}


def test_si_shopify_falla_las_tarjetas_salen_sin_existencias(tienda, monkeypatch):
    c, correr, _ = tienda

    async def falla(*a, **k):
        raise RuntimeError("caída")

    monkeypatch.setattr(tienda_shopify, "consultar", falla)
    assert correr(catalogo_chat.existencias(c, ["Bolsa tote de piel"])) == {}


def test_la_herramienta_solo_con_productos_y_direccion_publica(tienda, monkeypatch):
    c, correr, _ = tienda
    assert correr(catalogo_chat.con_productos(c))
    assert [t.name for t in catalogo_chat.herramientas(c, "ana")] == ["mostrar_productos"]
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    assert not correr(catalogo_chat.con_productos(c))
    monkeypatch.setattr(config, "PUBLIC_URL", PUBLICO)
    correr(c.store.set("productos", "[]"))
    assert not correr(catalogo_chat.con_productos(c))


def test_ver_mas_fotos_con_tope(tienda, monkeypatch):
    c, correr, _ = tienda
    monkeypatch.setattr(catalogo_chat, "FOTOS_POR_HORA", 1)
    ig = IgFalso()
    assert correr(catalogo_chat.mas_fotos(c, ig, "ana", "ana", "a1")) == "[Fotos de Bolsa tote de piel: 2]"
    assert [u.split("?")[0].rsplit("/", 1)[1] for _, u in ig.fotos] == ["f2.jpg", "f3.jpg"]
    assert correr(catalogo_chat.mas_fotos(c, ig, "ana", "ana", "a1")) == ""  # pasó su tope
    assert correr(catalogo_chat.mas_fotos(c, ig, "bea", "bea", "zz")) == ""  # no existe
    assert catalogo_chat.de_boton("SK_FOTOS:a1") == ("SK_FOTOS", "a1")
    assert catalogo_chat.de_boton("boton_1") is None and catalogo_chat.de_boton("SK_QUIERO:../x") is None


def test_las_tarjetas_van_despues_del_texto_y_quedan_en_el_historial(tienda, monkeypatch):
    c, correr, _ = tienda
    ig = IgFalso()
    monkeypatch.setattr(c, "instagram", ig)
    vueltas = []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        catalogo_chat.descartar(c, user_id)  # lo que hace agent.reply al empezar cada vuelta
        vueltas.append(new_messages)
        if len(vueltas) == 1:
            await catalogo_chat.elegir(c, user_id, ["Bolsa cruzada"], [])
            await store.add_incoming(user_id, "m2", "¿y en piel?")  # llegó otro: esta versión se tira
        else:
            await catalogo_chat.elegir(c, user_id, ["Bolsa tote de piel"], ["Envíos"])
        return "Te dejo las opciones con precio:", False

    async def nada(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nada)

    async def main():
        await c.store.add_incoming("ana", "m1", "¿qué bolsas tienen?")
        await bot.handle("ana", await c.store.pending("ana"))
        return await c.store.recent("ana", 10)

    historial = correr(main())
    assert len(vueltas) == 2
    assert ig.textos == [("ana", "Te dejo las opciones con precio:")]
    assert len(ig.tarjetas) == 1 and [t["title"] for t in ig.tarjetas[0][1]] == ["Bolsa tote de piel"]
    assert ig.tarjetas[0][2] == ["Envíos"]
    assert historial[-1] == ("assistant", "Te dejo las opciones con precio:\n[Tarjetas: Bolsa tote de piel]")


def test_si_las_tarjetas_fallan_el_texto_ya_salio(tienda, monkeypatch):
    c, correr, _ = tienda
    ig = IgFalso()

    async def falla(*a):
        raise httpx.HTTPError("caída")

    ig.send_cards = falla
    monkeypatch.setattr(c, "instagram", ig)

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        await catalogo_chat.elegir(c, user_id, ["Bolsa cruzada"], [])
        return "Mira:", False

    async def nada(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nada)

    async def main():
        await c.store.add_incoming("ana", "m1", "hola")
        await bot.handle("ana", await c.store.pending("ana"))
        return await c.store.recent("ana", 10)

    assert correr(main())[-1] == ("assistant", "Mira:") and ig.textos == [("ana", "Mira:")]


def test_instagram_manda_la_plantilla_y_sin_respuestas_rapidas_si_no_las_acepta():
    import asyncio

    enviados = []

    def answer(request):
        cuerpo = json.loads(request.content)
        enviados.append(cuerpo["message"])
        if "quick_replies" in cuerpo["message"]:
            return httpx.Response(400, json={"error": {"message": "no"}})
        return httpx.Response(200, json={"message_id": "mid1"})

    async def main():
        store = Store(":memory:")
        await store.open()
        ig = Instagram(store)
        ig.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        await ig.send_cards("ana", [{"title": "A"}], ["Envíos"])
        sent = await store.was_sent("mid1")
        await store.close()
        return sent

    assert asyncio.run(main())
    assert enviados[0]["quick_replies"] == [{"content_type": "text", "title": "Envíos", "payload": "Envíos"}]
    assert enviados[1] == {"attachment": {"type": "template", "payload": {
        "template_type": "generic", "elements": [{"title": "A"}]}}}


def _webhook(client, postback):
    body = json.dumps({"entry": [{"id": "negocio", "messaging": [{
        "sender": {"id": "ana"}, "recipient": {"id": "negocio"}, "timestamp": 1, "postback": postback}]}]}).encode()
    sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
    client.post("/webhook", content=body,
                headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"})


def test_botones_de_las_tarjetas_por_el_webhook(monkeypatch, tmp_path):
    calls, fotos = [], []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        calls.append(new_messages)
        return "ok", False

    async def nothing(*a, **k):
        pass

    async def send_image(recipient_id, url):
        fotos.append(url)

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", nothing)
    monkeypatch.setattr(bot.instagram, "send_image", send_image)
    monkeypatch.setattr(bot.instagram, "close", nothing)
    monkeypatch.setattr(bot.instagram, "subscribe", nothing)
    monkeypatch.setattr(bot.botones, "sync", nothing)
    monkeypatch.setattr(bot.principal, "ig_account_id", "negocio")
    monkeypatch.setattr(bot.principal, "media_dir", str(tmp_path))
    monkeypatch.setattr(bot, "MULTI_ACCOUNT", False)
    monkeypatch.setattr(config, "PUBLIC_URL", PUBLICO)
    for t in ("f1", "f2", "f3"):
        Image.new("RGB", (4, 4)).save(tmp_path / f"{t}.jpg")

    def esperar(cond):
        deadline = time.time() + 3
        while not cond() and time.time() < deadline:
            time.sleep(0.05)

    with TestClient(bot.app) as client:
        client.portal.call(bot.principal.store.set, "productos", json.dumps(PRODUCTOS))
        # "Lo quiero" entra a la conversación con el nombre del producto (lo atiende el asistente).
        _webhook(client, {"mid": "pb1", "title": "Lo quiero", "payload": "SK_QUIERO:a1"})
        esperar(lambda: calls)
        assert calls == [["Lo quiero: Bolsa tote de piel"]]
        # "Ver más fotos" se contesta sin Claude: las demás fotos, una vez aunque Meta reenvíe el aviso.
        _webhook(client, {"mid": "pb2", "title": "Ver más fotos", "payload": "SK_FOTOS:a1"})
        _webhook(client, {"mid": "pb2", "title": "Ver más fotos", "payload": "SK_FOTOS:a1"})
        esperar(lambda: len(fotos) >= 2)
        time.sleep(0.3)
        assert len(fotos) == 2 and calls == [["Lo quiero: Bolsa tote de piel"]]
        historial = client.portal.call(bot.principal.store.recent, "ana", 10)
        assert historial[-1] == ("assistant", "[Fotos de Bolsa tote de piel: 2]")
        # Sin más fotos que mandar, lo atiende el asistente.
        _webhook(client, {"mid": "pb3", "title": "Ver más fotos", "payload": "SK_FOTOS:c3"})
        esperar(lambda: len(calls) > 1)
        assert calls[-1] == ["Ver más fotos: Taller de piel"]


def test_opciones_apagadas(tienda, monkeypatch):
    c, correr, consultas = tienda
    perfil = {"reglas": {"links": False}, "catalogo_chat": {"quiero": False, "existencias": False, "rapidas": False}}
    correr(c.store.set("perfil", json.dumps(perfil)))
    opc = catalogo_chat.opciones_de(perfil)
    assert opc == {"tarjetas": True, "quiero": False, "fotos": True, "existencias": False, "rapidas": False,
                   "tienda": False}
    correr(catalogo_chat.elegir(c, "ana", ["Bolsa tote de piel", "Bolsa cruzada"], ["Envíos"]))
    ig = IgFalso()
    correr(catalogo_chat.enviar(c, ig, "ana", "ana", catalogo_chat.tomar(c, "ana")))
    _, (tote, cruzada), rapidas = ig.tarjetas[0]
    # Sin enlace a la tienda (regla "Comparte el link" apagada), sin "Lo quiero", sin existencias ni respuestas.
    assert rapidas == [] and consultas == []
    assert [b["title"] for b in tote["buttons"]] == ["Ver más fotos"] and "default_action" not in tote
    assert tote["subtitle"] == "$1,450 MXN · Café, Negro" and "buttons" not in cruzada
    # Sin "Ver más fotos", el botón no manda nada (lo atiende el asistente); sin tarjetas, no hay herramienta.
    correr(c.store.set("perfil", json.dumps({"catalogo_chat": {"fotos": False, "tarjetas": False}})))
    assert correr(catalogo_chat.mas_fotos(c, ig, "ana", "ana", "a1")) == "" and ig.fotos == []
    assert not correr(catalogo_chat.con_productos(c))


def test_se_configura_en_reglas_y_limites(cliente):
    client, c, *_ = cliente
    r = client.get("/panel/asistente/configura?paso=reglas")
    assert "Tus productos en el chat" in r.text and 'name="cc_quiero" value="1" checked' in r.text
    assert r.text.index('name="no_hacer"') < r.text.index("Tus productos en el chat")
    client.post("/panel/asistente/paso", headers=ORIGEN, data={
        "paso": "reglas", "r_links": "1", "cc": "1", "cc_tarjetas": "1", "cc_fotos": "1"})
    perfil = client.portal.call(marco.perfil, c)
    assert perfil["catalogo_chat"] == {"tarjetas": True, "quiero": False, "fotos": True, "existencias": False,
                                       "rapidas": False}
    r = client.get("/panel/asistente/configura?paso=reglas")
    assert 'name="cc_quiero" value="1">' in r.text and 'name="cc_fotos" value="1" checked' in r.text
    r = client.get("/panel/asistente/configura?paso=resumen")
    assert "Muestra tus productos en tarjetas con Ver en la tienda, Ver más fotos" in r.text
    client.post("/panel/asistente/paso", headers=ORIGEN, data={"paso": "reglas", "cc": "1"})
    assert "No muestra tus productos en tarjetas" in client.get("/panel/asistente/configura?paso=resumen").text
    # Sin Origin no se guarda.
    assert client.post("/panel/asistente/paso", data={"paso": "reglas", "cc": "1"}).status_code == 403
