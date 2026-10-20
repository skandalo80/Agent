"""La cuenta actual: cada conversación lee los datos de su propia cuenta."""
import asyncio
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
