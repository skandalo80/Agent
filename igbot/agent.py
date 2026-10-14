"""El agente: arma el contexto de cada usuario y le pide la respuesta a Claude.

Usa el Claude Agent SDK con todas las herramientas de Claude Code apagadas
(nada de Bash ni archivos: lo que escribe un desconocido por DM nunca debe
poder tocar el servidor). Solo tiene las herramientas de tools.py.
"""
import logging
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from . import catalog, config, tienda, tools
from .google import Google
from .instagram import Instagram
from .store import Store

log = logging.getLogger("igbot.agent")

# Carpeta vacía como directorio de trabajo del SDK.
WORKDIR = Path(config.DB_PATH).parent / "agent-cwd"


async def run_claude(
    prompt: str, system: str, mcp=None, allowed=(), max_turns=5, model: str | None = None
) -> str:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        system_prompt=system,
        model=model or config.MODEL,
        effort=config.EFFORT,
        tools=[],  # sin herramientas de Claude Code
        mcp_servers=mcp or {},
        strict_mcp_config=True,
        allowed_tools=list(allowed),
        permission_mode="dontAsk",  # lo que no está permitido se rechaza
        setting_sources=[],  # no leer configuraciones del servidor
        max_turns=max_turns,
        cwd=str(WORKDIR),
        extra_args={"no-session-persistence": None},  # la memoria es nuestra
    )
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error or not result.result:
        raise RuntimeError(f"Claude no devolvió respuesta: {result}")
    log.info("Claude: %d turnos, US$%.4f", result.num_turns, result.total_cost_usd or 0)
    return result.result.strip()


def render(rows) -> str:
    return "\n".join(f"{'Usuario' if role == 'user' else 'Tú'}: {text}" for role, text in rows)


async def build_prompt(store: Store, user_id: str, new_messages: list[str]) -> str:
    facts = await store.facts(user_id)
    summary = await store.summary(user_id)
    history = await store.recent(user_id, config.HISTORY_WINDOW)
    parts = []
    if facts:
        parts.append(
            "<datos_guardados>\n"
            + "\n".join(f"- {k}: {v}" for k, v in facts.items())
            + "\n</datos_guardados>"
        )
    if summary:
        parts.append(f"<resumen_de_antes>\n{summary}\n</resumen_de_antes>")
    if history:
        parts.append(f"<conversacion_reciente>\n{render(history)}\n</conversacion_reciente>")
    parts.append(f"<ahora>{tools.fmt(tools.now())} ({config.TIMEZONE}), {tools.now():%Y-%m-%d}</ahora>")
    parts.append(
        "<mensajes_nuevos>\n" + "\n".join(new_messages) + "\n</mensajes_nuevos>\n\n"
        "Escribe la respuesta que se enviará por DM (solo el texto del mensaje)."
    )
    return "\n\n".join(parts)


def system_prompt() -> str:
    """Las instrucciones del bot más el catálogo del negocio, si hay."""
    system = Path(config.PROMPT_PATH).read_text(encoding="utf-8")
    if catalog.available():
        system += f"\n\n<catalogo>\n{catalog.prompt_text()}\n</catalogo>"
    if tienda.available():
        system += f"\n\n<tienda>\n{tienda.prompt_text()}\n</tienda>"
    return system


async def reply(
    store: Store,
    google: Google | None,
    user_id: str,
    new_messages: list[str],
    instagram: Instagram | None = None,
) -> tuple[str, bool]:
    """Devuelve (respuesta, si alguna herramienta hizo algo fuera del chat)."""
    system = system_prompt()
    state: dict = {}
    server, allowed = tools.build(store, google, user_id, state, instagram)
    prompt = await build_prompt(store, user_id, new_messages)
    text = await run_claude(prompt, system, {"bot": server}, allowed, max_turns=10)
    return text, state.get("acted", False)


SUMMARY_SYSTEM = (
    "Resumes conversaciones de Instagram entre un negocio y una persona. "
    "Escribe en español, máximo 150 palabras, solo hechos útiles para seguir "
    "atendiéndola: qué quiere, qué se le ofreció, qué quedó pendiente."
)


async def summarize_if_needed(store: Store, user_id: str) -> None:
    """Si la conversación ya pasa la ventana, condensa lo viejo en el resumen.

    Se espera a que sobren 10 mensajes para no resumir en cada respuesta.
    """
    old = await store.to_summarize(user_id, keep=config.HISTORY_WINDOW)
    if len(old) < 10:
        return
    previous = await store.summary(user_id)
    prompt = (
        (f"<resumen_anterior>\n{previous}\n</resumen_anterior>\n\n" if previous else "")
        + f"<mensajes>\n{render([(r, t) for _, r, t in old])}\n</mensajes>\n\n"
        "Escribe el resumen actualizado."
    )
    text = await run_claude(prompt, SUMMARY_SYSTEM, max_turns=1)
    await store.save_summary(user_id, text, upto_id=old[-1][0])
