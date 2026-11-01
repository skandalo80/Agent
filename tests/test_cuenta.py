"""La cuenta actual: cada conversación lee los datos de su propia cuenta."""
import asyncio
import time
import dataclasses
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from igbot import limites  # noqa: E402
from igbot.cuenta import Cuenta, cuenta, usar  # noqa: E402


def test_cuenta_de_cliente_usa_su_carpeta():
    c = Cuenta.desde_env("cafelunar", {"IG_ACCESS_TOKEN": "t", "CLAUDE_MODEL": "claude-haiku-5-5"})
    assert c.prompt_path == "clientes/cafelunar/system.md"
    assert c.db_path == "data/cafelunar/bot.db"
    assert c.media_slug == "cafelunar" and c.model == "claude-haiku-5-5"
    principal = Cuenta.desde_env("", {"IG_ACCESS_TOKEN": "t"})
    assert principal.prompt_path == "prompts/system.md" and principal.media_slug == "principal"


def test_dos_cuentas_a_la_vez_no_se_mezclan():
    a = dataclasses.replace(cuenta(), slug="a", access_token="token-a", max_message_chars=5)
    b = dataclasses.replace(cuenta(), slug="b", access_token="token-b", max_message_chars=50)
    seen = {}

    async def conversacion(c):
        with usar(c):
            await asyncio.sleep(0.01)  # la otra conversación corre mientras tanto
            # También en las tareas que se crean dentro.
            token = await asyncio.create_task(asyncio.sleep(0, cuenta().access_token))
            seen[c.slug] = (token, limites.trim("hola a todos"))

    async def main():
        await asyncio.gather(conversacion(a), conversacion(b))

    principal = cuenta()
    asyncio.run(main())
    assert seen["a"] == ("token-a", "hola  […mensaje recortado]")
    assert seen["b"] == ("token-b", "hola a todos")
    assert cuenta() is principal  # al salir, vuelve la de antes


class FakeInstagram:
    """Cliente de Instagram de otra cuenta, sin red: anota lo que manda."""

    def __init__(self):
        self.sent = []

    async def send_text(self, user_id, text, humano=False):
        self.sent.append((user_id, text))

    async def subscribe(self, fields):
        pass

    async def set_ice_breakers(self, questions):
        pass

    async def close(self):
        pass


def test_un_proceso_atiende_dos_cuentas_sin_mezclarlas(monkeypatch):
    import hashlib
    import hmac
    import json
    import time

    from fastapi.testclient import TestClient

    import app as bot
    from igbot.dispatcher import Dispatcher
    from igbot.store import Store

    principal = bot.principal
    cliente = dataclasses.replace(principal, slug="cafelunar", ig_account_id="cafe",
                                  prompt_path="clientes/cafelunar/system.md")
    cliente.store, cliente.instagram = Store(":memory:"), FakeInstagram()
    principal_sent = []

    async def principal_send(user_id, text):
        principal_sent.append((user_id, text))

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        # Lo que el agente ve de la cuenta: su prompt y su base.
        assert store is cuenta().store
        return f"{cuenta().prompt_path}: {new_messages[0]}", False

    async def nothing(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", principal_send)
    monkeypatch.setattr(principal, "ig_account_id", "negocio")
    monkeypatch.setattr(bot, "cuentas", [principal, cliente])
    monkeypatch.setattr(bot, "MULTI_ACCOUNT", True)
    monkeypatch.setitem(bot.dispatchers, "cafelunar",
                        Dispatcher(cliente.store, bot.atender(cliente), 0.05, 2, shared=bot.shared_slots))

    def dm(account, sender, mid, text):
        return {"id": account, "messaging": [{
            "sender": {"id": sender}, "recipient": {"id": account}, "message": {"mid": mid, "text": text},
        }]}

    # Un mismo aviso de Meta puede traer entradas de varias cuentas.
    body = json.dumps({"entry": [
        dm("negocio", "ana", "m1", "hola vendedor"),
        dm("cafe", "beto", "m2", "hola café"),
        dm("desconocida", "carla", "m3", "nadie me contesta"),
    ]}).encode()
    sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
    with TestClient(bot.app) as client:
        client.post("/webhook", content=body,
                    headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"})
        deadline = time.time() + 3
        while (not principal_sent or not cliente.instagram.sent) and time.time() < deadline:
            time.sleep(0.05)

    assert principal_sent == [("ana", "prompts/system.md: hola vendedor")]
    assert cliente.instagram.sent == [("beto", "clientes/cafelunar/system.md: hola café")]


def test_bucle_de_fondo_pasa_por_cada_cuenta(monkeypatch):
    import app as bot

    otra = dataclasses.replace(bot.principal, slug="otra")
    monkeypatch.setattr(bot, "cuentas", [bot.principal, otra])
    vistas = []

    async def paso(c):
        vistas.append((c.slug, cuenta().slug))
        if c.slug == "":
            raise RuntimeError("falla en una cuenta")  # no detiene a las demás

    async def main():
        task = asyncio.create_task(bot.cada_cuenta(paso, 3600))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(main())
    assert vistas == [("", ""), ("otra", "otra")]


def test_el_bucle_de_fondo_atiende_las_cuentas_en_paralelo_y_corta_las_atascadas(monkeypatch):
    import app as bot

    lenta = dataclasses.replace(bot.principal, slug="lenta")
    otra = dataclasses.replace(bot.principal, slug="otra")
    monkeypatch.setattr(bot, "cuentas", [lenta, otra])
    vistas = []

    async def paso(c):
        if c.slug == "lenta":
            await asyncio.sleep(10)  # Meta caído para esta cuenta: no debe frenar a las demás
        vistas.append(c.slug)

    async def main():
        task = asyncio.create_task(bot.cada_cuenta(paso, 3600, a_la_vez=2, tope=0.1))
        await asyncio.sleep(0.05)
        assert vistas == ["otra"]  # la otra no esperó a la lenta
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(main())
    assert vistas == ["otra"]  # la lenta se cortó al tope sin anotar nada


def test_el_plan_pone_los_topes_y_el_env_gana():
    from igbot.cuenta import Cuenta
    c = Cuenta.desde_env("x", {"IG_ACCESS_TOKEN": "t", "PLAN": "community"})
    assert (c.imagenes_mes, c.mensajes_mes, c.posts_mes) == (40, 2500, 12)
    # El Pro CRM ya no existe: quien lo tenga queda como Community.
    c = Cuenta.desde_env("x", {"IG_ACCESS_TOKEN": "t", "PLAN": "pro_crm", "POSTS_PER_MONTH": "30"})
    assert (c.imagenes_mes, c.mensajes_mes, c.posts_mes) == (40, 2500, 30)
    c = Cuenta.desde_env("x", {"IG_ACCESS_TOKEN": "t"})  # sin plan: sin topes ni contenido
    assert (c.imagenes_mes, c.mensajes_mes, c.posts_mes) == (0, 0, 0)


def test_la_limpieza_borra_el_historial_viejo_y_deja_el_reciente():
    from igbot.store import Store

    async def main():
        store = Store(":memory:")
        await store.open()
        ahora = time.time()
        await store.add_incoming("ana", "m1", "hola")
        await store.add_event("ana", "interes", {})
        await store.add_sent("eco")
        await store.db.execute("UPDATE messages SET created_at = ?", (ahora - 400 * 86400,))
        await store.db.execute("UPDATE sent SET sent_at = ?", (ahora - 8 * 86400,))
        await store.db.commit()
        borrado = await store.purgar(365, ahora)
        await store.compactar()
        quedan = await (await store.db.execute("SELECT COUNT(*) FROM events")).fetchone()
        await store.close()
        return borrado, quedan[0]

    borrado, eventos = asyncio.run(main())
    assert borrado == {"sent": 1, "messages": 1, "events": 0, "comments": 0}
    assert eventos == 1


def test_con_retencion_cero_no_se_borra_el_historial():
    from igbot.store import Store

    async def main():
        store = Store(":memory:")
        await store.open()
        await store.add_incoming("ana", "m1", "hola")
        await store.db.execute("UPDATE messages SET created_at = 0")
        borrado = await store.purgar(0)
        await store.close()
        return borrado

    assert asyncio.run(main()) == {"sent": 0}
