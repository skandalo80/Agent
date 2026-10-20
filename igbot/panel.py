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
]
# Políticas por tema: así el cliente ve qué le falta contestar. Las vacías no van al prompt.
POLITICAS = [
    ("envios", "Envíos", "A dónde envías, cuánto cuesta y cuánto tarda."),
    ("cambios", "Cambios y devoluciones", "En qué casos y en cuántos días."),
    ("pagos", "Formas de pago", "Efectivo, transferencia, tarjeta, meses sin intereses."),
    ("apartados", "Apartados o reservaciones", "Si se puede apartar o reservar y cómo."),
    ("otras_politicas", "Otra política", "Cualquier otra regla que tus clientes deban saber."),
]
HUMANO = ("humano", "Si piden hablar con una persona",
          "Qué debe decir. Ej.: que alguien del equipo le contesta hoy mismo.", 2)
MIN_PREGUNTAS = 3
EJEMPLOS = [  # para que vean cómo llenar cada par
    ("¿Hacen envíos?", "Sí, a todo México por paquetería."),
    ("¿Tienen estacionamiento?", "Sí, gratis para clientes."),
    ("¿Dan factura?", "Sí, pídela al momento de pagar."),
]
MAX_PREGUNTAS = 40

TONOS = {
    "amigable": ("Amigable", "Cercano y cálido. Tuteas, usas frases cortas y puedes poner algún emoji de vez en cuando."),
    "profesional": ("Profesional", "Claro y profesional. Tuteas, vas directo al punto y no usas emojis."),
    "formal": ("Formal", "Formal y respetuoso. Hablas de usted y no usas emojis."),
}


def preguntas(perfil: dict) -> list[dict]:
    """[{"p": pregunta, "r": respuesta}]; antes del panel con pares era un solo texto."""
    faq = perfil.get("faq") or []
    return [{"p": "", "r": faq}] if isinstance(faq, str) else faq


def politicas(perfil: dict) -> dict:
    if "politicas" in perfil and not any(perfil.get(k) for k, *_ in POLITICAS):
        return {"otras_politicas": perfil["politicas"]}  # texto de antes de separarlas por tema
    return {k: perfil.get(k, "") for k, *_ in POLITICAS}


def generar(usuario: str, perfil: dict) -> str:
    """Las instrucciones del asistente a partir de los datos y el tono."""
    _, tono = TONOS.get(perfil.get("tono"), TONOS["amigable"])
    out = [f"Eres el asistente de Instagram de @{usuario}. Contestas los mensajes directos de sus clientes.",
           "", "TONO", tono]
    for key, titulo in [("negocio", "EL NEGOCIO"), ("horarios", "HORARIOS"), ("precios", "PRECIOS")]:
        if valor := perfil.get(key, "").strip():
            out += ["", titulo, valor]
    pares = [q for q in preguntas(perfil) if q["r"].strip()]
    if pares:
        out += ["", "PREGUNTAS FRECUENTES"]
        for q in pares:
            out += [f"- {q['p'].strip()}\n  {q['r'].strip()}" if q["p"].strip() else f"- {q['r'].strip()}"]
    temas = politicas(perfil)
    if lineas := [f"- {nombre}: {temas[k].strip()}" for k, nombre, _ in POLITICAS if temas.get(k, "").strip()]:
        out += ["", "POLÍTICAS", *lineas]
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

# Misma identidad que skandar.online (web/estilos.css): negro, grises de Apple,
# botones en píldora y el acento naranja. Sin fuentes externas (el CSP del bot
# no las deja): "Inter" si la tiene instalada, si no la del sistema.
ESTILO = """
:root{--negro:#0a0a0a;--grafito:#1d1d1f;--gris:#86868b;--claro:#f5f5f7;--blanco:#fff;--acento:#c2410c;--acento-claro:#f97316;--linea-oscura:#2c2c2e;--linea-clara:#d2d2d7}
*{box-sizing:border-box}
body{margin:0;background:var(--claro);color:var(--negro);font:17px/1.55 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit}
.contenedor{max-width:46rem;margin:0 auto;padding:0 1.25rem}
header{background:var(--negro);color:var(--claro);border-bottom:1px solid var(--linea-oscura)}
header .contenedor{display:flex;justify-content:space-between;align-items:center;height:3.25rem}
.marca{font-weight:600;font-size:.95rem;text-decoration:none;letter-spacing:-.01em}
.salir{background:none;border:0;color:var(--gris);font:inherit;font-size:.85rem;cursor:pointer;padding:0}
.salir:hover{color:var(--claro)}
.oscuro{background:var(--negro);color:var(--claro)}
.portada{padding:5rem 0 4rem}
.entrar{min-height:calc(100vh - 3.25rem);display:flex;align-items:center;text-align:center}
.antetitulo{font-size:.8rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--gris);margin:0 0 1rem}
h1,h2{font-weight:600;letter-spacing:-.035em;margin:0}
h1{font-size:clamp(2.5rem,8vw,4.5rem);line-height:1.04;overflow-wrap:anywhere}
h2{font-size:1.5rem;letter-spacing:-.02em;margin-bottom:.25rem}
.punto{color:var(--acento-claro)}
.bajada{font-size:clamp(1.1rem,2.4vw,1.35rem);line-height:1.4;color:var(--gris);margin:1.25rem auto 0;max-width:34rem}
.nota{color:var(--gris);font-size:.9rem;margin-top:2rem}
.enlace{color:var(--acento-claro);text-decoration:none;font-weight:500}
.enlace:hover{text-decoration:underline}
.boton{display:inline-block;padding:.8rem 1.5rem;border-radius:999px;font:inherit;font-weight:600;font-size:1rem;text-decoration:none;cursor:pointer;border:1px solid transparent;transition:transform .2s}
.boton:hover{transform:scale(1.03)}
.oscuro .boton{background:var(--claro);color:var(--negro)}
.oscuro .boton.secundario{background:transparent;color:var(--claro);border-color:#48484a}
main .boton{background:var(--negro);color:var(--claro)}
main .boton.secundario{background:transparent;color:var(--negro);border-color:var(--linea-clara)}
.acciones{display:flex;gap:.75rem;flex-wrap:wrap;align-items:center;margin-top:1.5rem}
.centro .acciones{justify-content:center;margin-top:2.25rem}
.estado{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;margin-top:2rem;padding:1rem 1.25rem;border:1px solid var(--linea-oscura);border-radius:1.25rem;background:var(--grafito)}
.acciones-estado{display:flex;gap:.6rem;flex-wrap:wrap}
.chat{background:var(--blanco);border-radius:1.5rem;padding:1.5rem}
.burbuja{max-width:82%;width:fit-content;padding:.6rem .95rem;border-radius:1.2rem;font-size:.95rem;line-height:1.45;margin-bottom:.55rem;white-space:pre-wrap;overflow-wrap:anywhere}
.burbuja.cliente{background:var(--negro);color:var(--claro);margin-left:auto;border-bottom-right-radius:.35rem}
.burbuja.agente{background:var(--claro);border:1px solid var(--linea-clara);border-bottom-left-radius:.35rem}
.vacio{color:#6e6e73;text-align:center;padding:2rem 0}
.escribir{display:flex;gap:.6rem;margin-top:1rem}
.escribir input{flex:1}
.luz{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;margin-right:.5rem;background:var(--acento-claro)}
.luz.on{background:#30d158}
main{padding:3rem 0 5rem}
.aviso{background:var(--blanco);border-left:3px solid var(--acento);border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:1.25rem}
.tarjeta{background:var(--blanco);border-radius:1.5rem;padding:2rem;margin-bottom:1.25rem}
.num{display:block;font-size:.85rem;font-weight:600;color:var(--acento);margin-bottom:1rem}
.ayuda{color:#6e6e73;font-size:.9rem;margin:.1rem 0 .5rem}
label{display:block;font-weight:600;font-size:.95rem;margin-top:1.25rem}
textarea,input[type=text]{width:100%;font:inherit;font-size:1rem;color:var(--negro);background:var(--claro);border:1px solid var(--linea-clara);border-radius:.85rem;padding:.7rem .9rem}
textarea:focus,input[type=text]:focus{outline:2px solid var(--acento-claro);outline-offset:1px;border-color:transparent}
textarea.prompt{font:14px/1.5 ui-monospace,Menlo,monospace;min-height:20rem}
.tarjeta .aviso{background:var(--claro);margin:1rem 0 0}
.par{border:1px solid var(--linea-clara);border-radius:1rem;padding:1rem;margin-top:1rem}
.par label{margin-top:0}
.par textarea{margin-top:.4rem}
.par .quitar{background:none;border:0;color:#6e6e73;font:inherit;font-size:.85rem;cursor:pointer;padding:.5rem 0 0}
.par .quitar:hover{color:var(--acento)}
.tonos{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:.75rem}
.tonos label{margin:0;font-weight:500;border:1px solid var(--linea-clara);border-radius:999px;padding:.45rem 1rem;cursor:pointer}
.tonos input{accent-color:var(--acento)}
.oculto{position:absolute;left:-9999px}
"""


def pagina(cuerpo: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex"><meta name="referrer" content="same-origin">'
        '<meta name="theme-color" content="#0a0a0a">'
        '<title>Tu asistente · Agentes IA by Skandar</title>'
        f"<style>{ESTILO}</style></head><body>{cuerpo}</body></html>"
    )


def cabecera(salir: bool = False) -> str:
    boton = ('<form method="post" action="/panel/salir"><button class="salir">Salir</button></form>'
             if salir else "")
    return (f'<header><div class="contenedor"><a class="marca" href="{config.SITE_URL}">Agentes IA by Skandar</a>'
            f"{boton}</div></header>")


def entrar() -> HTMLResponse:
    return pagina(
        cabecera()
        + '<section class="oscuro entrar"><div class="contenedor centro">'
        '<p class="antetitulo">Panel de clientes</p>'
        '<h1>Tu asistente<span class="punto">.</span></h1>'
        '<p class="bajada">Entra con tu cuenta de Instagram para configurar lo que responde tu asistente.</p>'
        '<div class="acciones"><a class="boton" href="/conectar/instagram">Entrar con Instagram</a></div>'
        f'<p class="nota">¿Aún no conectas tu Instagram? <a class="enlace" href="{config.SITE_URL}/conectar">Empieza aquí ›</a></p>'
        "</div></section>"
    )


def caja(nombre: str, etiqueta: str, ayuda: str, valor: str, filas: int = 2) -> str:
    return (f'<label for="{nombre}">{etiqueta}</label><p class="ayuda">{ayuda}</p>'
            f'<textarea id="{nombre}" name="{nombre}" rows="{filas}" maxlength="{MAX_CAMPO}">{escape(valor)}</textarea>')


async def panel_de(c: Cuenta, aviso: str = "", datos: dict | None = None, prompt: str | None = None,
                   pares_minimos: int = MIN_PREGUNTAS, nota: str = "") -> HTMLResponse:
    """La página del panel. `aviso` sale arriba; `nota`, junto a las instrucciones
    (ahí deja la página "Generar instrucciones", con #instrucciones)."""
    datos = datos if datos is not None else await perfil(c)
    prompt = prompt_actual(c) if prompt is None else prompt
    encendido = await activo(c)
    pares = preguntas(datos)
    pares += [{"p": "", "r": ""}] * (max(MIN_PREGUNTAS, pares_minimos) - len(pares))
    temas = politicas(datos)
    negocio = "".join(caja(k, etq, ayuda, datos.get(k, ""), filas) for k, etq, ayuda, filas in CAMPOS)
    faq = "".join(
        f'<div class="par"><label for="faq_p_{i}">Pregunta {i + 1}</label>'
        f'<input type="text" id="faq_p_{i}" name="faq_p_{i}" maxlength="300" value="{escape(q["p"])}"'
        f' placeholder="{EJEMPLOS[i % len(EJEMPLOS)][0]}">'
        f'<textarea name="faq_r_{i}" rows="2" maxlength="{MAX_CAMPO}" placeholder="{EJEMPLOS[i % len(EJEMPLOS)][1]}">'
        f'{escape(q["r"])}</textarea>'
        f'<button class="quitar" name="accion" value="quitar_{i}" formaction="/panel/guardar#preguntas">Quitar</button></div>'
        for i, q in enumerate(pares)
    )
    pol = "".join(caja(k, etq, ayuda, temas.get(k, "")) for k, etq, ayuda in POLITICAS)
    tono_actual = datos.get("tono", "amigable")
    tonos = "".join(
        f'<label><input type="radio" name="tono" value="{k}"{" checked" if k == tono_actual else ""}> {nombre}</label>'
        for k, (nombre, _) in TONOS.items()
    )
    return pagina(
        cabecera(salir=True)
        + '<section class="oscuro portada"><div class="contenedor">'
        '<p class="antetitulo">Tu asistente</p>'
        f'<h1>@{escape(await usuario(c))}<span class="punto">.</span></h1>'
        f'<div class="estado"><div><span class="luz{" on" if encendido else ""}"></span>'
        f'{"Está contestando tus mensajes." if encendido else "Está en pausa: no contesta mensajes."}</div>'
        f'<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="{"0" if encendido else "1"}">'
        f'<div class="acciones-estado"><a class="boton secundario" href="/panel/probar#ultimo">Probar mi asistente</a>'
        f'<button class="boton{" secundario" if encendido else ""}">{"Pausar" if encendido else "Encender"}</button></div></form></div>'
        "</div></section>"
        '<main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + '<form method="post" action="/panel/guardar">'
        # Enter en un campo no debe quitar una pregunta: el primer botón del formulario solo recarga.
        '<button class="oculto" name="accion" value="vista" tabindex="-1" aria-hidden="true">Actualizar</button>'
        f'<div class="tarjeta"><span class="num">01</span><h2>Tu negocio</h2>{negocio}</div>'
        f'<div class="tarjeta" id="preguntas"><span class="num">02</span><h2>Preguntas frecuentes</h2>'
        f'<p class="ayuda">Lo que más te preguntan, con la respuesta que darías tú.</p>'
        f'<input type="hidden" name="faq_n" value="{len(pares)}">{faq}'
        f'<div class="acciones"><button class="boton secundario" name="accion" value="agregar" formaction="/panel/guardar#preguntas">+ Agregar pregunta</button></div></div>'
        f'<div class="tarjeta"><span class="num">03</span><h2>Políticas</h2>'
        f'<p class="ayuda">Llena solo las que apliquen a tu negocio.</p>{pol}</div>'
        f'<div class="tarjeta"><span class="num">04</span><h2>Atención personal</h2>'
        f'{caja(HUMANO[0], HUMANO[1], HUMANO[2], datos.get(HUMANO[0], ""), HUMANO[3])}</div>'
        f'<div class="tarjeta" id="instrucciones"><span class="num">05</span><h2>Tono e instrucciones</h2>'
        f'<p class="ayuda">Elige cómo habla tu asistente y genera sus instrucciones con todo lo de arriba.</p>'
        f'<div class="tonos">{tonos}</div>'
        f'<div class="acciones"><button class="boton secundario" name="accion" value="generar" formaction="/panel/guardar#instrucciones">Generar instrucciones</button></div>'
        + (f'<div class="aviso">{escape(nota)}</div>' if nota else "") +
        f'<label>Instrucciones de tu asistente</label><p class="ayuda">Puedes editarlas antes de guardar.</p>'
        f'<textarea class="prompt" name="prompt" maxlength="{MAX_PROMPT}">{escape(prompt)}</textarea>'
        f'<div class="acciones"><button class="boton" name="accion" value="guardar">Guardar</button></div></div>'
        "</form></div></main>"
    )


def datos_de(f: dict[str, str]) -> dict:
    """El perfil tal como viene del formulario (las preguntas vacías se quedan, para no moverlas)."""
    datos = {k: f.get(k, "").strip()[:MAX_CAMPO] for k in [*(c[0] for c in CAMPOS), *(p[0] for p in POLITICAS), HUMANO[0]]}
    datos["tono"] = f.get("tono") if f.get("tono") in TONOS else "amigable"
    try:
        n = min(int(f.get("faq_n", "0")), MAX_PREGUNTAS)
    except ValueError:
        n = 0
    datos["faq"] = [{"p": f.get(f"faq_p_{i}", "").strip()[:300], "r": f.get(f"faq_r_{i}", "").strip()[:MAX_CAMPO]}
                    for i in range(n)]
    return datos


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
    datos = datos_de(f)
    accion = f.get("accion", "")
    prompt = f.get("prompt", "")[:MAX_PROMPT]
    # Agregar o quitar una pregunta solo redibuja la página: nada se guarda todavía.
    if accion == "agregar":
        return await panel_de(c, "", datos, prompt, pares_minimos=len(datos["faq"]) + 1)
    if accion.startswith("quitar_") and accion[7:].isdigit():
        del datos["faq"][int(accion[7:]):int(accion[7:]) + 1]
        return await panel_de(c, "", datos, prompt)
    if accion not in ("generar", "guardar"):
        return await panel_de(c, "", datos, prompt)
    datos["faq"] = [q for q in datos["faq"] if q["p"] or q["r"]]
    with usar(c):
        await c.store.set("perfil", json.dumps(datos, ensure_ascii=False))
        nombre = await usuario(c)
        if accion == "generar":
            return await panel_de(c, nota="Listo: revísalas y pulsa Guardar para que tu asistente las use.",
                                  datos=datos, prompt=generar(nombre, datos))
        prompt = prompt.strip() or generar(nombre, datos)
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
    return await panel_de(c, "Tu asistente ya está contestando." if encender else "Tu asistente quedó en pausa.")


# --- probar el asistente antes de encenderlo ---

MAX_PRUEBA_TURNOS = 20  # mensajes que se recuerdan en el chat de prueba
MAX_PRUEBAS_DIA = 40  # mensajes de prueba por cuenta al día (cada uno cuesta una llamada a Claude)
MAX_MENSAJE_PRUEBA = 500
NOTA_PRUEBA = (
    "\n\n<prueba>Esta conversación es una prueba del dueño del negocio desde su panel. Contesta"
    " exactamente como le contestarías a un cliente. Aquí no tienes herramientas: si te tocaría"
    " pasar con una persona, solo di lo que le dirías al cliente.</prueba>"
)


async def historial(c: Cuenta) -> list[list[str]]:
    return json.loads(await c.store.get("prueba") or "[]")


async def pruebas_hoy(c: Cuenta) -> tuple[str, int]:
    from .limites import today_start
    with usar(c):  # el día empieza a medianoche en la zona horaria de la cuenta
        clave = f"pruebas:{int(today_start())}"
    return clave, int(await c.store.get(clave) or 0)


async def responder_prueba(c: Cuenta, previos: list[list[str]], texto: str) -> str:
    """La respuesta del asistente con sus instrucciones guardadas, sin herramientas."""
    from . import agent, tools  # import tardío: agent importa el SDK
    with usar(c):
        partes = []
        if previos:
            partes.append(f"<conversacion_reciente>\n{agent.render(previos)}\n</conversacion_reciente>")
        partes.append(f"<ahora>{tools.fmt(tools.now())} ({c.timezone}), {tools.now():%Y-%m-%d}</ahora>")
        partes.append(f"<mensajes_nuevos>\n{texto}\n</mensajes_nuevos>\n\n"
                      "Escribe la respuesta que se enviará por DM (solo el texto del mensaje).")
        return await agent.run_claude("\n\n".join(partes), agent.system_prompt() + NOTA_PRUEBA, max_turns=1)


async def pagina_prueba(c: Cuenta, aviso: str = "") -> HTMLResponse:
    mensajes = await historial(c)
    burbujas = "".join(
        f'<div class="burbuja {"cliente" if rol == "user" else "agente"}">{escape(texto)}</div>'
        for rol, texto in mensajes
    ) or '<p class="vacio">Escribe como si fueras un cliente. Usa las instrucciones que guardaste.</p>'
    _, usadas = await pruebas_hoy(c)
    return pagina(
        cabecera(salir=True)
        + '<section class="oscuro portada"><div class="contenedor">'
        '<p class="antetitulo">Prueba tu asistente</p>'
        f'<h1>@{escape(await usuario(c))}<span class="punto">.</span></h1>'
        '<p class="bajada" style="margin-left:0">Así contestaría tus mensajes. Nada de esto llega a Instagram.</p>'
        "</div></section>"
        '<main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + f'<div class="chat">{burbujas}<span id="ultimo"></span>'
        '<form method="post" action="/panel/probar#ultimo" class="escribir">'
        f'<input type="text" name="mensaje" maxlength="{MAX_MENSAJE_PRUEBA}" placeholder="Escribe un mensaje" required autofocus>'
        '<button class="boton" name="accion" value="enviar">Enviar</button></form></div>'
        f'<p class="ayuda" style="margin-top:.75rem">Te quedan {max(MAX_PRUEBAS_DIA - usadas, 0)} mensajes de prueba hoy.</p>'
        '<div class="acciones"><a class="boton secundario" href="/panel">← Volver al panel</a>'
        '<form method="post" action="/panel/probar"><button class="boton secundario" name="accion" value="reiniciar">'
        "Empezar de nuevo</button></form></div>"
        "</div></main>"
    )


@router.get("/probar")
async def ver_prueba(request: Request):
    c = cuenta_de_sesion(request)
    return await pagina_prueba(c) if c else RedirectResponse("/panel", status_code=303)


@router.post("/probar")
async def probar(request: Request):
    de_aqui(request)
    if not (c := cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    f = await form(request)
    if f.get("accion") == "reiniciar":
        await c.store.set("prueba", "[]")
        return await pagina_prueba(c)
    texto = f.get("mensaje", "").strip()[:MAX_MENSAJE_PRUEBA]
    if not texto:
        return await pagina_prueba(c)
    clave, usadas = await pruebas_hoy(c)
    if usadas >= MAX_PRUEBAS_DIA:
        return await pagina_prueba(c, "Llegaste al límite de mensajes de prueba de hoy. Mañana puedes seguir probando.")
    previos = (await historial(c))[-MAX_PRUEBA_TURNOS:]
    await c.store.set(clave, str(usadas + 1))
    try:
        respuesta = await responder_prueba(c, previos, texto)
    except Exception:
        log.exception("Falló la prueba del asistente de %s", c.slug)
        return await pagina_prueba(c, "Tu asistente no pudo contestar esta vez. Intenta de nuevo en un momento.")
    previos += [["user", texto], ["assistant", respuesta]]
    await c.store.set("prueba", json.dumps(previos[-MAX_PRUEBA_TURNOS:], ensure_ascii=False))
    return await pagina_prueba(c)


@router.post("/salir")
async def salir(request: Request):
    de_aqui(request)
    response = RedirectResponse("/panel", status_code=303)
    response.delete_cookie(COOKIE, path="/panel")
    return response
