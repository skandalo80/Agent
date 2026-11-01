"""La etiqueta Human Agent solo va cuando lo escribió una persona y está activada."""
import asyncio
import os

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

from igbot import config, instagram as ig  # noqa: E402
from igbot.store import Store  # noqa: E402


class Resp:
    is_error = False
    status_code = 200

    def json(self):
        return {"message_id": "mid-1"}

    def raise_for_status(self):
        pass


def test_etiqueta_human_agent(monkeypatch):
    enviados = []

    async def main():
        store = Store(":memory:")
        await store.open()
        cliente = ig.Instagram(store)

        async def post(url, headers=None, json=None):
            enviados.append(json)
            return Resp()

        async def token():
            return "tok"

        monkeypatch.setattr(cliente.http, "post", post)
        monkeypatch.setattr(cliente, "token", token)
        monkeypatch.setattr(config, "HUMAN_AGENT_TAG", False)
        await cliente.send_text("ana", "hola", humano=True)  # sin permiso: sin etiqueta
        monkeypatch.setattr(config, "HUMAN_AGENT_TAG", True)
        await cliente.send_text("ana", "hola")  # el asistente: nunca lleva etiqueta
        await cliente.send_text("ana", "hola", humano=True)
        assert await store.was_sent("mid-1")
        await cliente.close()
        await store.close()

    asyncio.run(main())
    assert [("tag" in e, e.get("messaging_type")) for e in enviados] == [(False, None), (False, None), (True, "MESSAGE_TAG")]
    assert enviados[2]["tag"] == "HUMAN_AGENT"
