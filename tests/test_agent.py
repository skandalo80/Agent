"""Las llamadas de un turno van directo a la API de Claude; las que usan herramientas, por el Agent SDK."""
import asyncio
import os
from types import SimpleNamespace

os.environ.update(IG_VERIFY_TOKEN="vt", IG_APP_SECRET="secreto", IG_ACCESS_TOKEN="tok", DB_PATH=":memory:")

import pytest  # noqa: E402

from igbot import agent, limites  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402


class StreamFalso:
    def __init__(self, mensaje):
        self.mensaje = mensaje

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self.mensaje


class ApiFalsa:
    def __init__(self, texto="Hola", stop="end_turn"):
        self.pedidos = []
        self.mensaje = SimpleNamespace(
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=f"  {texto} ")],
            stop_reason=stop, usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000))
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, **kwargs):
        self.pedidos.append(kwargs)
        return StreamFalso(self.mensaje)


def preparar(monkeypatch, api):
    gastos = []

    async def gasto(usd):
        gastos.append(usd)
    monkeypatch.setattr(agent, "_api", api)
    monkeypatch.setattr(limites, "add_cost", gasto)
    monkeypatch.setattr(cuenta(), "effort", "low")

    def sin_sdk(**kwargs):
        raise AssertionError("no debe abrir Claude Code")
    monkeypatch.setattr(agent, "query", sin_sdk)
    return gastos


def test_un_turno_va_directo_y_cobra_por_tokens(monkeypatch):
    api = ApiFalsa()
    gastos = preparar(monkeypatch, api)
    texto = asyncio.run(agent.run_claude("¿Qué tal?", "Eres breve.", max_turns=1, model="claude-haiku-5-5"))
    assert texto == "Hola"
    pedido = api.pedidos[0]
    assert pedido["model"] == "claude-haiku-5-5" and pedido["system"] == "Eres breve."
    assert pedido["messages"] == [{"role": "user", "content": "¿Qué tal?"}]
    assert pedido["output_config"] == {"effort": "low"} and "thinking" not in pedido
    assert gastos == [pytest.approx(0.10 + 0.05)]  # 1 M de entrada + 100 mil de salida en Haiku


def test_rechazo_o_vacio_es_error(monkeypatch):
    preparar(monkeypatch, ApiFalsa(stop="refusal"))
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run_claude("x", "y", max_turns=1))
    preparar(monkeypatch, ApiFalsa(texto=""))
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run_claude("x", "y", max_turns=1))


def test_con_herramientas_sigue_en_el_sdk(monkeypatch):
    api = ApiFalsa()
    preparar(monkeypatch, api)
    with pytest.raises(AssertionError, match="Claude Code"):
        asyncio.run(agent.run_claude("x", "y", {"bot": object()}, ["mcp__bot__a"], max_turns=10))
    assert api.pedidos == []


def test_precio_de_modelo_desconocido_es_sonnet():
    assert agent.costo("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert agent.costo("otro", 0, 1_000_000) == 10.0
