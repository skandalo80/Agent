"""Panel del cliente: bot.skandar.online/panel.

El cliente entra con su Instagram (el mismo botón de /conectar, sin
contraseñas) y desde ahí:
- llena los datos de su negocio (a qué se dedica, horarios, precios, preguntas
  frecuentes, políticas, qué hacer si piden una persona),
- elige un tono (amigable, profesional o formal) y con eso se arman las
  instrucciones de su asistente, que puede revisar y editar antes de guardar,
- enciende o pausa su asistente.

Una cuenta que se conecta por primera vez se da de alta sola (carpeta
clientes/<nombre>/ con su token, ver preparar_carpeta) y queda en pausa hasta
que la encienden aquí. Las reglas comunes (prompts/comun.md) siempre van
después de lo que escriba el cliente, así que no las puede quitar.

La sesión es una cookie firmada con IG_APP_SECRET que dura SESSION_DAYS; los
POST solo se aceptan si vienen de esta misma página (cabecera Origin) y la
cookie es SameSite=Lax.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.panel")

router = APIRouter(prefix="/panel")
# Los pone app.py al arrancar: la cuenta (ya cargada) de un id de Instagram.
buscar: Callable[[str], Cuenta | None] = lambda ig_id: None
# Y la que da de alta una cuenta nueva al conectarse (None si no tiene panel).
alta: Callable[[str, str, str], Awaitable[Cuenta | None]] | None = None

COOKIE = "panel"
SESSION_DAYS = 7
MAX_CAMPO = 3000
MAX_PROMPT = 12000
# Modelo del paquete Básico (solo chat).
MODELO_BASICO = "claude-haiku-5-5"

CAMPOS = [
    # (nombre, etiqueta, ayuda, filas)
    ("negocio", "¿A qué se dedica tu negocio?",
     "Qué vendes u ofreces, dónde estás y qué te hace diferente.", 4),
    ("horarios", "Horarios", "Por ejemplo: lunes a viernes de 9:00 a 18:00, sábados de 10:00 a 14:00.", 2),
    ("precios", "Precios", "Productos o servicios con su precio. Lo que no pongas aquí, el asistente no lo inventa.", 5),
    ("faq", "Preguntas frecuentes", "Una por línea con su respuesta. Ej.: ¿Hacen envíos? Sí, a todo México.", 6),
    ("politicas", "Políticas", "Envíos, cambios y devoluciones, formas de pago, apartados.", 4),
    ("humano", "Si piden hablar con una persona",
     "Qué debe decir. Ej.: que alguien del equipo le contesta hoy mismo.", 2),
]

TONOS = {
    "amigable": ("Amigable", "Cercano y cálido. Tuteas, usas frases cortas y puedes poner algún emoji de vez en cuando."),
    "profesional": ("Profesional", "Claro y profesional. Tuteas, vas directo al punto y no usas emojis."),
    "formal": ("Formal", "Formal y respetuoso. Hablas de usted y no usas emojis."),
}

SECCIONES = [
    ("negocio", "EL NEGOCIO"), ("horarios", "HORARIOS"), ("precios", "PRECIOS"),
    ("faq", "PREGUNTAS FRECUENTES"), ("politicas", "POLÍTICAS"),
]


def generar(usuario: str, perfil: dict) -> str:
    """Las instrucciones del asistente a partir de los datos y el tono."""
    _, tono = TONOS.get(perfil.get("tono"), TONOS["amigable"])
    out = [f"Eres el asistente de Instagram de @{usuario}. Contestas los mensajes directos de sus clientes.",
           "", "TONO", tono]
    for key, titulo in SECCIONES:
        if valor := perfil.get(key, "").strip():
            out += ["", titulo, valor]
    humano = perfil.get("humano", "").strip() or "Dile que alguien del equipo le escribe en cuanto pueda."
    out += ["", "SI PIDEN HABLAR CON UNA PERSONA", humano + " Usa la herramienta pasar_a_humano.",
            "", "REGLAS",
            "- Usa solo la información de arriba. Si no sabes algo, dilo y ofrece pasar con una persona.",
            "- No inventes precios, promociones, existencias ni horarios."]
    return "\n".join(out) + "\n"


# --- alta de una cuenta nueva ---

def preparar_carpeta(ig_id: str, usuario: str, token: str, correo: str, ocupados: set[str]) -> str:
    """Crea clientes/<nombre>/ (.env con su token y system.md) y devuelve el nombre."""
    base = re.sub(r"[^a-z0-9_-]", "_", usuario.lower())[:30].strip("_") or f"ig_{ig_id[-8:]}"
    slug, n = base, 2
    while slug in ocupados or Path(f"clientes/{slug}").exists():
        slug, n = f"{base}-{n}", n + 1
    carpeta = Path(f"clientes/{slug}")
    carpeta.mkdir(parents=True)
    lineas = [f"IG_ACCESS_TOKEN={token}", f"IG_ACCOUNT_ID={ig_id}", f"CLAUDE_MODEL={MODELO_BASICO}",
              # Sin tu agenda ni tu correo: el Básico solo contesta el chat.
              "GOOGLE_TOOLS=0"]
    if correo:
        lineas.append(f"OWNER_EMAIL={correo}")  # a quién avisar cuando piden una persona
    env = carpeta / ".env"
    env.write_text("\n".join(lineas) + "\n")
    os.chmod(env, 0o600)
    (carpeta / "system.md").write_text(generar(usuario, {}), encoding="utf-8")
    return slug


# --- sesión ---

def _key() -> bytes:
    return hmac.new(config.APP_SECRET.encode(), b"panel-sesion", hashlib.sha256).digest()


def _firma(ig_id: str, vence: int) -> str:
    return hmac.new(_key(), f"{ig_id}.{vence}".encode(), hashlib.sha256).hexdigest()


def iniciar_sesion(response, ig_id: str) -> None:
    vence = int(time.time()) + SESSION_DAYS * 86400
    response.set_cookie(COOKIE, f"{ig_id}.{vence}.{_firma(ig_id, vence)}", max_age=SESSION_DAYS * 86400,
                        path="/panel", secure=True, httponly=True, samesite="lax")


def sesion(request: Request) -> str | None:
    """El id de Instagram de quien entró, si la cookie es válida y no ha vencido."""
    try:
        ig_id, vence, firma = request.cookies.get(COOKIE, "").split(".")
        if int(vence) > time.time() and hmac.compare_digest(firma, _firma(ig_id, int(vence))):
            return ig_id
    except ValueError:
        pass
    return None


def de_aqui(request: Request) -> None:
    """Contra CSRF: el formulario tiene que venir de esta misma página.

    Lo dice la cabecera Origin o, si el navegador la manda como "null" (pasa
    con Referrer-Policy: no-referrer, la de Caddy), Sec-Fetch-Site.
    """
    h = request.headers
    if h.get("origin") != config.PUBLIC_URL.rstrip("/") and h.get("sec-fetch-site") != "same-origin":
        raise HTTPException(status_code=403)


def cuenta_de_sesion(request: Request) -> Cuenta | None:
    ig_id = sesion(request)
    return buscar(ig_id) if ig_id else None


async def form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode(errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


# --- datos de la cuenta ---

async def activo(c: Cuenta) -> bool:
    return await c.store.get("bot_activo") != "0"


async def perfil(c: Cuenta) -> dict:
    return json.loads(await c.store.get("perfil") or "{}")


async def usuario(c: Cuenta) -> str:
    return await c.store.get("account_username") or c.slug


def prompt_actual(c: Cuenta) -> str:
    path = Path(c.prompt_path)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# --- páginas ---

ESTILO = """
:root{color-scheme:light dark;--fondo:#f5f5f7;--tarjeta:#fff;--texto:#1d1d1f;--suave:#6e6e73;--borde:#d2d2d7;--acento:#0071e3}
@media (prefers-color-scheme:dark){:root{--fondo:#000;--tarjeta:#1c1c1e;--texto:#f5f5f7;--suave:#a1a1a6;--borde:#38383a;--acento:#2997ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--texto);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:44rem;margin:0 auto;padding:2rem 1rem 4rem}
header{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-bottom:1.5rem}
.marca{font-weight:600}
h1{font-size:2rem;line-height:1.15;margin:0 0 .5rem}
h2{font-size:1.15rem;margin:0 0 .75rem}
.tarjeta{background:var(--tarjeta);border:1px solid var(--borde);border-radius:14px;padding:1.25rem;margin-bottom:1rem}
.suave{color:var(--suave);font-size:.9rem;margin:.15rem 0 .5rem}
label{display:block;font-weight:600;margin-top:1rem}
textarea,input[type=text]{width:100%;font:inherit;color:inherit;background:transparent;border:1px solid var(--borde);border-radius:10px;padding:.6rem .75rem}
textarea.prompt{font:14px/1.45 ui-monospace,Menlo,monospace;min-height:18rem}
.tonos{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem}
.tonos label{font-weight:500;margin:0;border:1px solid var(--borde);border-radius:999px;padding:.35rem .9rem;cursor:pointer}
.botones{display:flex;flex-wrap:wrap;gap:.75rem;margin-top:1.25rem}
button,.boton{font:inherit;font-weight:600;border-radius:999px;padding:.6rem 1.2rem;border:1px solid var(--acento);cursor:pointer;text-decoration:none;display:inline-block}
.primario{background:var(--acento);color:#fff}
.secundario{background:transparent;color:var(--acento)}
.estado{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.punto{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;margin-right:.4rem;background:#ff9f0a}
.punto.on{background:#30d158}
.aviso{border-color:var(--acento)}
"""


def pagina(cuerpo: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex"><meta name="referrer" content="same-origin">'
        '<title>Tu asistente · Agentes IA by Skandar</title>'
        f"<style>{ESTILO}</style></head><body><main>{cuerpo}</main></body></html>"
    )


def entrar() -> HTMLResponse:
    return pagina(
        '<header><span class="marca">Agentes IA by Skandar</span></header>'
        '<h1>Tu asistente de Instagram</h1>'
        '<p class="suave">Entra con tu cuenta de Instagram para configurar lo que responde tu asistente.</p>'
        '<div class="botones"><a class="boton primario" href="/conectar/instagram">Entrar con Instagram</a></div>'
    )


async def panel_de(c: Cuenta, aviso: str = "", datos: dict | None = None, prompt: str | None = None) -> HTMLResponse:
    datos = datos if datos is not None else await perfil(c)
    prompt = prompt_actual(c) if prompt is None else prompt
    encendido = await activo(c)
    tono_actual = datos.get("tono", "amigable")
    campos = "".join(
        f'<label for="{k}">{etq}</label><p class="suave">{ayuda}</p>'
        f'<textarea id="{k}" name="{k}" rows="{filas}" maxlength="{MAX_CAMPO}">{escape(datos.get(k, ""))}</textarea>'
        for k, etq, ayuda, filas in CAMPOS
    )
    tonos = "".join(
        f'<label><input type="radio" name="tono" value="{k}"{" checked" if k == tono_actual else ""}> {nombre}</label>'
        for k, (nombre, _) in TONOS.items()
    )
    return pagina(
        f'<header><span class="marca">Agentes IA by Skandar</span>'
        f'<form method="post" action="/panel/salir"><button class="secundario">Salir</button></form></header>'
        f'<h1>@{escape(await usuario(c))}</h1>'
        + (f'<div class="tarjeta aviso">{escape(aviso)}</div>' if aviso else "")
        + f'<div class="tarjeta estado"><div><span class="punto{" on" if encendido else ""}"></span>'
        f'{"Tu asistente está contestando." if encendido else "Tu asistente está en pausa: no contesta mensajes."}</div>'
        f'<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="{"0" if encendido else "1"}">'
        f'<button class="{"secundario" if encendido else "primario"}">{"Pausar" if encendido else "Encender"}</button></form></div>'
        f'<form method="post" action="/panel/guardar">'
        f'<div class="tarjeta"><h2>Tu negocio</h2>{campos}'
        f'<label>Tono</label><div class="tonos">{tonos}</div>'
        f'<div class="botones"><button class="secundario" name="accion" value="generar">Generar instrucciones</button></div></div>'
        f'<div class="tarjeta"><h2>Instrucciones de tu asistente</h2>'
        f'<p class="suave">Se arman con tus datos y el tono. Puedes editarlas antes de guardar.</p>'
        f'<textarea class="prompt" name="prompt" maxlength="{MAX_PROMPT}">{escape(prompt)}</textarea>'
        f'<div class="botones"><button class="primario" name="accion" value="guardar">Guardar</button></div></div>'
        f'</form>'
    )


@router.get("")
async def ver(request: Request):
    c = cuenta_de_sesion(request)
    return await panel_de(c) if c else entrar()


@router.post("/guardar")
async def guardar(request: Request):
    de_aqui(request)
    if not (c := cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    f = await form(request)
    datos = {k: f.get(k, "").strip()[:MAX_CAMPO] for k, *_ in CAMPOS}
    datos["tono"] = f.get("tono") if f.get("tono") in TONOS else "amigable"
    with usar(c):
        await c.store.set("perfil", json.dumps(datos, ensure_ascii=False))
        nombre = await usuario(c)
        if f.get("accion") == "generar":
            return await panel_de(c, "Revisa las instrucciones y pulsa Guardar para que tu asistente las use.",
                                  datos, generar(nombre, datos))
        prompt = f.get("prompt", "").strip()[:MAX_PROMPT] or generar(nombre, datos)
        Path(c.prompt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(c.prompt_path).write_text(prompt + "\n", encoding="utf-8")
    log.info("@%s guardó las instrucciones de su asistente desde el panel", nombre)
    aviso = "Guardado. Tu asistente ya usa estas instrucciones."
    if not await activo(c):
        aviso += " Cuando quieras que conteste, pulsa Encender."
    return await panel_de(c, aviso)


@router.post("/estado")
async def estado(request: Request):
    de_aqui(request)
    if not (c := cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    encender = (await form(request)).get("activo") == "1"
    await c.store.set("bot_activo", "1" if encender else "0")
    log.info("@%s %s su asistente desde el panel", await usuario(c), "encendió" if encender else "pausó")
    return await panel_de(c, "Tu asistente está contestando." if encender else "Tu asistente quedó en pausa.")


@router.post("/salir")
async def salir(request: Request):
    de_aqui(request)
    response = RedirectResponse("/panel", status_code=303)
    response.delete_cookie(COOKIE, path="/panel")
    return response
