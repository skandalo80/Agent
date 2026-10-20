"""Panel del cliente: bot.skandar.online/panel.

El cliente entra con su Instagram (el mismo botón de /conectar, sin
contraseñas) y desde ahí:
- llena los datos de su negocio (a qué se dedica, horarios, precios, preguntas
  frecuentes, políticas, qué hacer si piden una persona),
- elige un tono (amigable, profesional o formal) y con eso se arman las
  instrucciones de su asistente, que puede revisar y editar antes de guardar,
- enciende o pausa su asistente;
- ve sus números del día y sus conversaciones recientes: toma un chat (pausa al
  asistente ahí para contestar él), reactiva los que están en pausa o los abre en Instagram.

La cuenta principal también se edita aquí (para enseñarlo en una demo): lo que
guarda va a data/system.md y manda sobre prompts/system.md hasta que lo restaure.

/panel lleva a Resultados cuando el asistente ya está encendido y con mensajes
de clientes; si no, a Tu asistente (/panel/asistente) para configurarlo.

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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from . import config
from .cuenta import Cuenta, usar

log = logging.getLogger("igbot.panel")

router = APIRouter(prefix="/panel")
# Los pone app.py al arrancar: la cuenta (ya cargada) de un id de Instagram.
buscar: Callable[[str], Cuenta | None] = lambda ig_id: None
# Y la que da de alta una cuenta nueva al conectarse (None si no tiene panel).
alta: Callable[[str, str, str], Awaitable[Cuenta | None]] | None = None

COOKIE = "panel"
SESSION_DAYS = 30  # Instagram vuelve a pedir el permiso en cada inicio de sesión
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
    path = c.ruta_prompt()
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
.contenedor{max-width:64rem;margin:0 auto;padding:0 1.25rem}
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
.escribiendo{display:flex;gap:.3rem;align-items:center;height:2.4rem}
.escribiendo span{width:.45rem;height:.45rem;border-radius:50%;background:var(--gris);animation:puntos 1.2s infinite ease-in-out}
.escribiendo span:nth-child(2){animation-delay:.15s}
.escribiendo span:nth-child(3){animation-delay:.3s}
@keyframes puntos{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
.compacta{padding:2.25rem 0 2rem;display:flex}
/* La franja negra mide lo mismo en todas las secciones: el resumen (.hoy) va siempre pegado abajo. */
@media(min-width:561px){.compacta{min-height:28.5rem}.compacta .hoy .kpi{min-height:8.6rem}}
.compacta>.contenedor{display:flex;flex-direction:column;width:100%}
.compacta .hoy{margin-top:auto}
.compacta .estado,.compacta .pestanas{margin-bottom:1.25rem}
.titulo-fila{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.oscuro .boton.ajuste{padding:.55rem 1.1rem;font-size:.9rem}
.oscuro .boton.ajuste.on{background:var(--claro);color:var(--negro)}
.compacta h1{font-size:clamp(1.9rem,5vw,2.75rem)}
.compacta .bajada{font-size:1.05rem;margin-top:.5rem}
.compacta+main{padding-top:1.5rem}
.ancla{display:block;scroll-margin-top:30vh}
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
textarea,input[type=text],input[type=datetime-local],input[type=time],input[type=email]{width:100%;font:inherit;font-size:1rem;color:var(--negro);background:var(--claro);border:1px solid var(--linea-clara);border-radius:.85rem;padding:.7rem .9rem}
textarea:focus,input[type=text]:focus,input[type=datetime-local]:focus,input[type=time]:focus,input[type=email]:focus{outline:2px solid var(--acento-claro);outline-offset:1px;border-color:transparent}
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
header form{display:flex;margin:0}
.menu a,.marca{white-space:nowrap}
.marca{margin-right:1rem}
@media(max-width:560px){header .marca{font-size:0}header .marca::after{content:"Skandar";font-size:.95rem}.menu{gap:.75rem;margin-right:.75rem;font-size:.8rem}.kpis{grid-template-columns:1fr 1fr}.kpi{padding:1rem}.kpi b{font-size:1.6rem}header:has(.menu) .marca{display:none}header:has(.menu) .menu{margin-left:0}}
.menu{display:flex;gap:1.25rem;font-size:.85rem;margin-left:auto;margin-right:1.5rem}
.menu a{color:var(--gris);text-decoration:none}
.menu a.on,.menu a:hover{color:var(--claro)}
.pestanas{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:1.5rem}
.pestanas a{padding:.5rem 1rem;border-radius:999px;color:#a1a1a6;text-decoration:none;font-size:.95rem;font-weight:500;border:1px solid transparent}
.pestanas a.on{background:var(--grafito);color:var(--claro);border-color:var(--linea-oscura)}
.pestanas a.crear{background:var(--claro);color:var(--negro);font-weight:600}
.contador{color:var(--gris);font-size:.85rem;margin:1rem 0 0}
.opciones{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));gap:.75rem;margin-top:.75rem}
.opcion{position:relative;margin:0;font-weight:400;border:1px solid var(--linea-clara);border-radius:1.1rem;padding:1rem 1.1rem;cursor:pointer;background:var(--blanco)}
.opcion b{display:block;font-weight:600}
.opcion span{display:block;color:#6e6e73;font-size:.9rem;line-height:1.4;margin-top:.2rem}
.opcion small{display:inline-block;margin-top:.5rem;font-size:.75rem;font-weight:600;color:var(--acento)}
.opcion:has(input:checked){border-color:var(--acento);box-shadow:inset 0 0 0 1px var(--acento);background:#fff4ed}
.opcion input,.chips input,.foto input{position:absolute;opacity:0;pointer-events:none}
.chips{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.6rem}
.chips label{position:relative;margin:0;font-weight:500;font-size:.95rem;border:1px solid var(--linea-clara);border-radius:999px;padding:.45rem 1rem;cursor:pointer;background:var(--blanco)}
.chips label:has(input:checked){background:var(--negro);color:var(--claro);border-color:var(--negro)}
.chips label:has(input:focus-visible),.opcion:has(input:focus-visible),.foto:has(input:focus-visible){outline:2px solid var(--acento-claro);outline-offset:2px}
.pieza{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:1rem;display:block;background:var(--claro)}
.editor{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:1.25rem;align-items:start}
.editor .fija{position:sticky;top:1rem}
@media(max-width:760px){.editor{grid-template-columns:1fr}.editor .fija{position:static}}
.fila{display:flex;gap:1rem;align-items:center;padding:1rem 0;border-top:1px solid var(--linea-clara)}
.fila:first-of-type{border-top:0}
.fila img{width:4.5rem;aspect-ratio:4/5;object-fit:cover;border-radius:.6rem;flex:none}
.fila .datos{flex:1;min-width:0}
.fila .datos b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fila .datos span{display:block;color:#6e6e73;font-size:.9rem}
@media(max-width:560px){.fila{flex-wrap:wrap;gap:.6rem 1rem}.fila .datos{flex-basis:calc(100% - 5.5rem)}.fila img+.datos+.chip,.fila .pieza+.datos+.chip{margin-left:5.5rem}}
.chip{display:inline-block;font-size:.75rem;font-weight:600;padding:.2rem .65rem;border-radius:999px;background:var(--claro);color:#3a3a3c;border:1px solid var(--linea-clara);white-space:nowrap}
.chip.verde{background:#e8f5ec;color:#1d6b37;border-color:transparent}
.chip.rojo{background:#fdecea;color:#a1260d;border-color:transparent}
.color{display:flex;gap:1rem;align-items:flex-start;margin-top:1.25rem}
.color>div{flex:1;min-width:0}
.color label{margin-top:0}
.color .ayuda{margin:.1rem 0 .4rem}
.muestra{width:2.9rem;height:2.9rem;border-radius:.75rem;flex:none;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}
input[type=color].muestra{padding:0;border:0;background:none;cursor:pointer;-webkit-appearance:none;appearance:none}
input[type=color].muestra::-webkit-color-swatch-wrapper{padding:0}
input[type=color].muestra::-webkit-color-swatch{border:0;border-radius:.75rem}
input[type=color].muestra::-moz-color-swatch{border:0;border-radius:.75rem}
.hex{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85rem;font-weight:400;color:#6e6e73;margin-left:.35rem}
label.pantone{font-size:.8rem;font-weight:500;color:#6e6e73;margin-top:.2rem}
label.pantone span{font-weight:400}
input.chico{max-width:9rem;padding:.4rem .7rem;font-size:.85rem}
.opcion img.ejemplo{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:.7rem;display:block;margin-bottom:.6rem}
label.check{display:flex;gap:.5rem;align-items:center;font-weight:500;cursor:pointer}
label.check input{width:auto;margin:0}
.si-precio{display:none}
form:has(#con_precio:checked) .si-precio{display:block}
form:has(input[name=modo][value=libre]:checked) .si-plantilla{display:none}
.si-como{display:none}form:has(input[name=como][value=plantilla]:checked) .si-como.plantilla,form:has(input[name=como][value=libre]:checked) .si-como.libre{display:block}
.desplegable{margin-top:1.25rem;padding:1.25rem 1.4rem;border:1px solid var(--linea-clara);border-radius:1.25rem;background:var(--claro)}
.desplegable h3{font-size:1rem;margin:0 0 .25rem}
.mini-colores{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.6rem}.mini-color{display:flex;align-items:center;gap:.5rem;margin:0;font-weight:500}
.mini-color input{width:2.4rem;height:2.4rem;border:1px solid var(--linea-clara);padding:0;border-radius:.6rem;background:none}
.opcion small.tuyo{display:block;color:var(--negro);font-style:italic;font-weight:400;font-size:.85rem}
.si-libre{display:none}form:has(input[name=modo][value=libre]:checked) .si-libre{display:block}
.minis{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-top:.75rem}
.minis figure{margin:0}.minis figcaption{color:#6e6e73;font-size:.8rem;margin-top:.35rem;line-height:1.3}
.pieza.cargando{opacity:.45;transition:opacity .2s}
.js .sin-js{display:none}
.propuestas{display:grid;grid-template-columns:repeat(auto-fill,minmax(15.5rem,1fr));gap:1rem}
.propuesta{background:var(--blanco);border-radius:1.25rem;overflow:hidden;display:flex;flex-direction:column}
.propuesta .pieza{border-radius:0}
.propuesta .cuerpo{padding:1rem 1.1rem 1.2rem;display:flex;flex-direction:column;gap:.45rem;flex:1}
.propuesta .cuerpo p{margin:0;font-size:.92rem;line-height:1.45}
.propuesta .arriba{display:flex;justify-content:space-between;gap:.5rem}
.propuesta .acciones{margin-top:auto;padding-top:.4rem}
.propuesta .acciones .boton{flex:1;text-align:center}
.por-que{color:#6e6e73}
.chip.acento{background:#fff4ed;color:var(--acento);border-color:transparent}
.titulo-seccion{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;margin-bottom:1rem}
.titulo-seccion h2{margin:0}
.acciones-fila{display:flex;gap:.5rem;flex:none}
.antiguos .fila img{opacity:.85}
.antiguos .datos b{font-weight:500}
.dos{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
.dos .ayuda{margin:0 0 .3rem}
.fotos{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:.75rem}
.fotos figure{margin:0;width:6rem;text-align:center}
.fotos img{width:6rem;height:6rem;object-fit:cover;border-radius:.75rem;display:block}
.foto{position:relative;margin:0;cursor:pointer;border-radius:.85rem;border:2px solid transparent}
.foto:has(input:checked){border-color:var(--acento)}
.fotos .quitar{background:none;border:0;color:#6e6e73;font:inherit;font-size:.8rem;cursor:pointer;padding:.3rem 0 0}
input[type=file]{font:inherit;font-size:.9rem;margin-top:.75rem;max-width:100%}
.boton.chico{padding:.5rem 1rem;font-size:.9rem}
.generando{display:flex;gap:1rem;align-items:center}
.extra{border:1px solid var(--acento);background:#fff4ed}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(10.5rem,1fr));gap:.75rem;margin-bottom:1.25rem}
.kpi{background:var(--blanco);border-radius:1.25rem;padding:1.25rem 1.4rem}
.kpi span{display:block;color:#6e6e73;font-size:.85rem}
.kpi b{display:block;font-size:2rem;font-weight:600;letter-spacing:-.03em;line-height:1.15;margin:.2rem 0}
.kpi small{color:#6e6e73;font-size:.85rem}
.hoy{margin:1rem 0 0;grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr))}
.hoy .kpi{background:var(--grafito);color:var(--claro);border:1px solid var(--linea-oscura)}
.hoy .kpi b{font-size:1.6rem}.hoy .kpi span,.hoy .kpi small{color:#a1a1a6}.hoy .kpi small{display:block;line-height:1.35}
.barra{height:.35rem;border-radius:999px;background:var(--linea-oscura);margin-top:.5rem;overflow:hidden}
.barra i{display:block;height:100%;background:var(--acento-claro)}
.avatar{width:2.6rem;height:2.6rem;border-radius:50%;background:var(--claro);display:grid;place-items:center;font-weight:600;flex:none}
.sub{margin-top:1.5rem;padding-top:1.5rem;border-top:1px solid var(--linea-clara)}
.horas{display:flex;gap:.75rem;align-items:center;margin-top:.6rem}.horas input{width:9rem}
label.check input[type=email]{flex:1;max-width:22rem;margin:0}
.si-fijo{display:none;margin-top:.75rem}form:has(input[name=fuera][value=fijo]:checked) .si-fijo{display:block}
.semaforo{width:.65rem;height:.65rem;border-radius:50%;flex:none;margin:0 .4rem;background:#30d158}
.semaforo.sem-aviso{background:#ff9f0a}.semaforo.sem-mal{background:#ff453a}
.caminos{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem}
.camino{background:var(--blanco);border-radius:1.5rem;padding:2rem;display:flex;flex-direction:column;border:2px solid transparent}
.camino.sugerido{border-color:var(--acento)}
.camino h2{margin:.25rem 0 .5rem}.camino ul{margin:1rem 0 1.5rem;padding:0;list-style:none;color:#3a3a3c}
.camino li{padding:.45rem 0;border-top:1px solid var(--linea-clara);display:flex;gap:.6rem}
.camino li:first-child{border-top:0}.camino li b{color:var(--acento);font-weight:600;min-width:1.2rem}
.camino .acciones{margin-top:auto}
.tiempo{align-self:flex-start;font-size:.8rem;color:#6e6e73;background:var(--claro);border-radius:999px;padding:.2rem .7rem}
.sola{max-width:40rem;margin:0 auto}
.bajada-bienvenida{color:#a1a1a6;font-size:1.15rem;max-width:40rem;margin:.75rem 0 0}
.despues{margin-top:1.25rem;text-align:center;color:#6e6e73;font-size:.9rem}.despues a{color:inherit}
@media(max-width:700px){.caminos{grid-template-columns:1fr}}
.plegable summary{display:flex;justify-content:space-between;align-items:center;cursor:pointer;list-style:none}
.plegable summary::-webkit-details-marker{display:none}
.plegable summary .num{margin:0}
.plegable summary::after{content:"";width:.55rem;height:.55rem;border-right:2px solid #6e6e73;border-bottom:2px solid #6e6e73;transform:rotate(45deg);margin:0 .3rem .3rem 1rem}
.plegable[open] summary::after{transform:rotate(-135deg);margin-bottom:-.2rem}
.plegable summary .resumen{margin-left:auto;color:#6e6e73;font-size:.85rem}
.plegable[open] summary{margin-bottom:1rem}
.filtros{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0 .25rem}
.filtros a{font-weight:500;font-size:.95rem;border:1px solid var(--linea-clara);border-radius:999px;padding:.45rem 1rem;color:inherit;text-decoration:none}
.filtros a.on{background:var(--negro);color:var(--claro);border-color:var(--negro)}
@media(max-width:560px){.hoy{grid-template-columns:1fr 1fr}.fila:has(.avatar) .datos{flex-basis:calc(100% - 3.6rem)}.fila .avatar+.datos+.chip{margin-left:3.6rem}}
.grafica{width:100%;height:9rem;display:block;margin-top:1.25rem}
.ejex{display:flex;color:#6e6e73;font-size:.75rem;margin-top:.35rem}
.ejex span{flex:1;text-align:center;white-space:nowrap;min-width:0;overflow:visible}
.grafica .b{fill:var(--acento-claro)}
.grafica .b.fuera{fill:#c7c7cc}
.grafica .eje{stroke:var(--linea-clara)}
.leyenda{display:flex;gap:1.25rem;flex-wrap:wrap;color:#6e6e73;font-size:.85rem;margin-top:.75rem}
.leyenda i{display:inline-block;width:.7rem;height:.7rem;border-radius:.2rem;margin-right:.4rem;background:var(--acento-claro)}
.leyenda i.fuera{background:#c7c7cc}
.tarjeta .kpis{margin:1rem 0 0}
.tarjeta .kpi{background:var(--claro)}
"""


# Las tipografías de los posts (fonts/), para que los botones de letra se vean con la suya.
FONTS = Path(__file__).resolve().parent.parent / "fonts"
STATIC = Path(__file__).resolve().parent / "static"
FUENTES_CSS = "".join(
    f"@font-face{{font-family:'{familia}';src:url(/panel/static/fuentes/{archivo}-{peso_nombre}.ttf);"
    f"font-weight:{peso};font-display:swap}}"
    for familia, archivo in (("Montserrat", "Montserrat"), ("Poppins", "Poppins"),
                             ("Playfair Display", "PlayfairDisplay"), ("Lora", "Lora"), ("Inter", "Inter"))
    for peso, peso_nombre in ((400, "Regular"), (600, "SemiBold"), (700, "Bold"))
)

PRINCIPIO = (
    '<!doctype html><html lang="es"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="robots" content="noindex"><meta name="referrer" content="same-origin">'
    '<meta name="theme-color" content="#0a0a0a">'
    '<title>Tu asistente · Agentes IA by Skandar</title>'
    f"<style>{ESTILO}{FUENTES_CSS}</style>"
    # Solo JavaScript propio (CSP script-src 'self'): la vista previa al momento en Contenido.
    '<script src="/panel/static/panel.js" defer></script></head><body>'
)
FINAL = "</body></html>"


def pagina(cuerpo: str) -> HTMLResponse:
    return HTMLResponse(PRINCIPIO + cuerpo + FINAL)


ACTIVA = ' class="on"'
MENU = {"resultados": ("Resultados", "/panel/resultados"), "asistente": ("Tu asistente", "/panel/asistente"),
        "contenido": ("Contenido", "/panel/contenido")}


def cabecera(salir: bool = False, menu: str = "", cuenta: Cuenta | None = None) -> str:
    """`menu`: la sección en la que está. Contenido sale solo si el plan de `cuenta` lo tiene."""
    boton = ('<form method="post" action="/panel/salir"><button class="salir">Salir</button></form>'
             if salir else "")
    enlaces = "".join(f'<a href="{href}"{ACTIVA if k == menu else ""}>{nombre}</a>'
                      for k, (nombre, href) in MENU.items()
                      if k != "contenido" or cuenta is None or cuenta.imagenes_mes > 0)
    # Con sesión, el logo lleva al inicio del panel; sin sesión, a la página principal.
    inicio_marca = "/panel" if salir else config.SITE_URL
    return (f'<header><div class="contenedor"><a class="marca" href="{inicio_marca}">Agentes IA by Skandar</a>'
            + (f'<nav class="menu">{enlaces}</nav>' if menu else "")
            + f"{boton}</div></header>")


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
                   pares_minimos: int = MIN_PREGUNTAS, nota: str = "", ver: str = "",
                   confirmar: bool = False) -> HTMLResponse:
    """La página del panel. `aviso` sale arriba; `nota`, junto a las instrucciones
    (ahí deja la página "Generar instrucciones", con #instrucciones)."""
    datos = datos if datos is not None else await perfil(c)
    prompt = prompt_actual(c) if prompt is None else prompt
    encendido = await activo(c)
    formulario = _formulario(c, datos, prompt, pares_minimos, nota, confirmar)
    return pagina(
        cabecera(salir=True, menu="asistente", cuenta=c)
        + '<section class="oscuro portada compacta"><div class="contenedor">'
        '<p class="antetitulo">Tu asistente</p>'
        f'<h1>@{escape(await usuario(c))}<span class="punto">.</span></h1>'
        f'<div class="estado"><div><span class="luz{" on" if encendido else ""}"></span>'
        f'{"Está contestando tus mensajes." if encendido else "Está en pausa: no contesta mensajes."}</div>'
        f'<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="{"0" if encendido else "1"}">'
        f'<div class="acciones-estado"><a class="boton secundario" href="/panel/probar#ultimo">Probar mi asistente</a>'
        f'<button class="boton{" secundario" if encendido else ""}">{"Pausar" if encendido else "Encender"}</button></div></form></div>'
        + await hoy(c) +
        "</div></section>"
        '<main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + await pendiente(c, "asistente")
        + await recientes(c, ver)
        + await tarjetas_ajustes(c)
        + ("" if c.slug else principal(c))
        + formulario
        + "</div></main>"
    )


async def tarjetas_ajustes(c: Cuenta) -> str:
    from . import ajustes  # ajustes usa este módulo
    return ajustes.tarjeta(c) + await ajustes.conexion(c)


def hace(t: float) -> str:
    s = time.time() - t
    if s < 60:
        return "hace un momento"
    if s < 3600:
        return f"hace {int(s // 60)} min"
    if s < 86400:
        return f"hace {int(s // 3600)} h"
    return "ayer" if s < 2 * 86400 else f"hace {int(s // 86400)} días"


def kpi(etiqueta: str, valor: str, nota: str, extra: str = "") -> str:
    return f'<div class="kpi"><span>{etiqueta}</span><b>{valor}</b><small>{nota}</small>{extra}</div>'


def restantes(etiqueta: str, usados: int, tope: int, que: str) -> str:
    """Cuánto le queda del plan este mes, con su barra. Pasarse no corta nada: se ve como extra."""
    barra = f'<div class="barra"><i style="width:{min(100, usados / tope * 100):.0f}%"></i></div>'
    if usados > tope:
        return kpi(etiqueta, "0", f"{usados - tope:,} {que} extra este mes", barra)
    return kpi(etiqueta, f"{tope - usados:,}", f"de {tope:,} este mes", barra)


async def hoy(c: Cuenta) -> str:
    """Los números del día, debajo del estado. El gasto en dólares solo lo ve la cuenta principal:
    a un cliente le diría cuánto cuesta su asistente; él ve los mensajes que le quedan de su plan."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from . import limites, resultados
    zona = ZoneInfo(c.timezone)
    ahora = datetime.now(zona)
    inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    filas = await c.store.message_times(inicio.timestamp(), ahora.timestamp())
    contestados = [u for u, rol, _, a_mano in filas if rol == "assistant" and not a_mano]
    respuesta = resultados.conversaciones(filas, c, inicio, 1)["respuesta"]
    en_pausa = len(await c.store.paused_list(time.time()))

    chats = len(set(contestados))
    cajas = [
        kpi("Contestados hoy", str(len(contestados)), f"en {chats} chat{'s' if chats != 1 else ''}"),
        kpi("Tiempo de respuesta", resultados.duracion(respuesta), "mediana de hoy"),
        kpi("Chats en pausa", str(en_pausa), "los atiendes tú" if en_pausa else "ninguno"),
    ]
    if not c.slug:
        with usar(c):
            gasto = await limites.spent_today(c.store)
        tope = c.daily_budget_usd
        barra = (f'<div class="barra"><i style="width:{min(100, gasto / tope * 100):.0f}%"></i></div>'
                 if tope > 0 else "")
        cajas.append(kpi("Gasto de hoy", f"US${gasto:.2f}",
                         f"de US${tope:.2f} al día" if tope > 0 else "sin límite diario", barra))
    else:
        mes = inicio.replace(day=1).timestamp()
        if c.mensajes_mes > 0:
            cajas.append(restantes("Mensajes restantes", await c.store.all_bot_replies_since(mes), c.mensajes_mes,
                                   "respuestas"))
        else:
            cajas.append(kpi("Mensajes este mes", str(await c.store.client_messages_since(mes)), "de tus clientes"))
    return f'<div class="hoy kpis">{"".join(cajas)}</div>'


MAX_CHATS = 15
FILTROS = {"": "Todas", "pausa": "En pausa", "sin": "Sin contestar"}


async def recientes(c: Cuenta, ver: str) -> str:
    """Los últimos chats: quién los atiende y los botones para tomarlos, reactivarlos o abrirlos
    en Instagram. Los que están en pausa salen siempre, aunque sean viejos."""
    import asyncio

    from .store import MANUAL
    ahora = time.time()
    pausados = {u: (hasta, ultimo) for u, hasta, ultimo in await c.store.paused_list(ahora)}
    chats = {u: (t, rol, texto, ultimo) for u, t, rol, texto, ultimo in await c.store.recent_chats(MAX_CHATS)}
    for u, (hasta, ultimo) in pausados.items():
        chats.setdefault(u, (0.0, "user", ultimo, ultimo))

    def estado(u: str, rol: str, texto: str) -> str:
        if u in pausados:
            return "pausa"
        if rol == "user":
            return "sin"
        return "tu" if texto.startswith(MANUAL) else "asistente"
    filas = [(u, *datos, estado(u, datos[1], datos[2])) for u, datos in chats.items()]
    cuantos = {"": len(filas), "pausa": sum(f[-1] == "pausa" for f in filas), "sin": sum(f[-1] == "sin" for f in filas)}
    ver = ver if ver in FILTROS else ""
    if ver:
        filas = [f for f in filas if f[-1] == ver]
    with usar(c):
        nombres = await asyncio.gather(*(c.instagram.username(f[0]) for f in filas))
    etiquetas = {"pausa": ("En pausa · tú", "acento"), "sin": ("Sin contestar", "rojo"),
                 "tu": ("Contestaste tú", ""), "asistente": ("Asistente", "verde")}
    html = []
    for (u, t, _, _, ultimo, est), nombre in zip(filas, nombres):
        nombre = nombre if nombre.startswith("@") else "Cliente de Instagram"
        detalle = f"«{escape(ultimo[:90])}»" if ultimo else "Sin mensajes todavía"
        if est == "pausa":
            horas = max(1, round((pausados[u][0] - ahora) / 3600))
            detalle += f" · vuelve a contestar solo en {horas} h"
        elif t:
            detalle += f" · {hace(t)}"
        texto, color = etiquetas[est]
        botones = (f'<button class="boton secundario chico" name="accion" value="extender">+{c.human_pause_hours:g} h</button>'
                   '<button class="boton chico" name="accion" value="reactivar">Reactivar</button>'
                   if est == "pausa" else
                   '<button class="boton secundario chico" name="accion" value="tomar">Tomar este chat</button>')
        abrir = (f'<a class="boton secundario chico" href="https://ig.me/m/{escape(nombre[1:])}" target="_blank"'
                 ' rel="noopener">Abrir</a>' if nombre.startswith("@") else "")
        html.append(
            f'<div class="fila"><span class="avatar">{escape(nombre[1:2].upper() or "?")}</span>'
            f'<div class="datos"><b>{escape(nombre)}</b><span>{detalle}</span></div>'
            f'<span class="chip {color}">{texto}</span>'
            '<form method="post" action="/panel/pausas" class="acciones-fila">'
            f'<input type="hidden" name="usuario" value="{escape(u)}">{botones}{abrir}</form></div>')
    filtros = "".join(
        f'<a href="/panel/asistente{"?ver=" + k if k else ""}#chats" class="{"on" if k == ver else ""}">'
        f'{nombre}{f" ({cuantos[k]})" if k else ""}</a>' for k, nombre in FILTROS.items())
    vacio = {"": "Aún no te escribe nadie. Aquí vas a ver tus chats en cuanto lleguen.",
             "pausa": "Ninguno: tu asistente está contestando en todos tus chats.",
             "sin": "Ninguno: todos tus chats tienen respuesta."}[ver]
    # Plegable (<details>): panel.js recuerda en el navegador si la cerró.
    resumen = f"{cuantos['pausa']} en pausa" if cuantos["pausa"] else f"{cuantos['']} chats"
    return ('<details class="tarjeta plegable" id="chats" open><summary><span class="num">Conversaciones recientes</span>'
            f'<span class="resumen">{resumen}</span></summary>'
            f'<p class="ayuda">Lo último que te escribieron. "Tomar este chat" pausa al asistente ahí'
            f" {c.human_pause_hours:g} h para que contestes tú desde Instagram.</p>"
            f'<div class="filtros">{filtros}</div>'
            + ("".join(html) or f'<p class="ayuda vacio">{vacio}</p>') + "</details>")


# La cuenta principal (la vendedora, la del .env) usa prompts/system.md, que vive en el
# repo. Lo que se guarda aquí va aparte (Cuenta.prompt_editado) para no chocar con git pull;
# "Volver a las del servidor" lo borra y vuelve a mandar el del repo.
def principal(c: Cuenta) -> str:
    if c.prompt_editado().is_file():
        return ('<form method="post" action="/panel/guardar" class="aviso acciones-fila" style="align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap">'
                "<span>Tu asistente usa las instrucciones que guardaste aquí, no las del servidor"
                " (prompts/system.md). Los cambios a ese archivo no se aplican mientras tanto.</span>"
                '<button class="boton secundario chico" name="accion" value="restaurar">'
                "Volver a las del servidor</button></form>")
    return ('<div class="aviso">Cuenta principal: abajo, en Tono e instrucciones, están las del servidor'
            " (prompts/system.md). Si las cambias y guardas, tu asistente usa las tuyas hasta que las restaures.</div>")


def _formulario(c: Cuenta, datos: dict, prompt: str, pares_minimos: int, nota: str, confirmar: bool = False) -> str:
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
    return (
        '<form method="post" action="/panel/guardar">'
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
        + (f'<div class="aviso">{escape(nota)}</div>' if nota else "")
        + ('<div class="aviso"><b>Ya tienes instrucciones escritas a mano.</b> Generar las reemplaza por unas nuevas'
           " armadas solo con el formulario y se pierde lo que escribiste. Tus instrucciones siguen abajo sin cambios."
           '<div class="acciones"><button class="boton" name="accion" value="generar_si"'
           ' formaction="/panel/guardar#instrucciones">Sí, reemplazarlas</button>'
           '<a class="boton secundario" href="/panel/asistente#instrucciones">Cancelar</a></div></div>'
           if confirmar else "") +
        f'<label>Instrucciones de tu asistente</label><p class="ayuda">Puedes editarlas antes de guardar.</p>'
        f'<textarea class="prompt" name="prompt" maxlength="{MAX_PROMPT}">{escape(prompt)}</textarea>'
        f'<div class="acciones"><button class="boton" name="accion" value="guardar">Guardar</button></div></div>'
        "</form>"
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


# --- bienvenida: la primera vez que entra una cuenta nueva ---

async def asistente_listo(c: Cuenta) -> bool:
    """Ya guardó sus instrucciones (o llenó el formulario antes de que hubiera bienvenida).
    La principal usa las del servidor: siempre está lista."""
    return not c.slug or await c.store.get("asistente_listo") == "1" or bool(await perfil(c))


async def marca_lista(c: Cuenta) -> bool:
    return c.imagenes_mes <= 0 or bool(await c.store.get("marca"))


async def pendiente(c: Cuenta, aqui: str) -> str:
    """Un aviso arriba si le falta configurar la otra parte (no la de la página donde está)."""
    if aqui != "asistente" and not await asistente_listo(c):
        return ('<div class="aviso">Te falta configurar <b>Tu asistente</b>: sin eso no contesta tus mensajes.'
                ' <a href="/panel/asistente">Configurarlo</a></div>')
    if aqui != "marca" and not await marca_lista(c):
        return ('<div class="aviso">Te falta configurar <b>Tu marca</b> para que tus posts se vean como tú.'
                ' <a href="/panel/contenido/marca">Configurarla</a></div>')
    return ""


PASOS_ASISTENTE = ["Cuéntanos de tu negocio: qué ofreces, horarios y precios", "Las preguntas que más te hacen",
                   "Elige su tono y revisa sus instrucciones", "Pruébalo y enciéndelo"]
PASOS_MARCA = ["Cómo se arman tus posts", "Colores y tipografía", "El estilo de tus fotos", "Fotos de tu negocio"]


def camino(etiqueta: str, titulo: str, desc: str, pasos: list[str], tiempo: str, boton: str, href: str,
           sugerido: bool) -> str:
    lis = "".join(f"<li><b>{i}</b>{p}</li>" for i, p in enumerate(pasos, 1))
    return (f'<div class="camino{" sugerido" if sugerido else ""}"><span class="num">{etiqueta}</span>'
            f'<span class="tiempo">{tiempo}</span><h2>{titulo}</h2><p class="ayuda">{desc}</p><ul>{lis}</ul>'
            f'<div class="acciones"><a class="boton{"" if sugerido else " secundario"}" href="{href}">{boton}</a>'
            "</div></div>")


async def bienvenida(c: Cuenta) -> HTMLResponse:
    """Con contenido (Community y Pro): elige por dónde empezar. Básico: solo su asistente."""
    asistente_desc = "El que contesta tus mensajes directos por ti, de día y de noche."
    if c.imagenes_mes > 0:
        bajada = "Vamos a dejar todo listo. ¿Por dónde quieres empezar? Puedes hacer el otro después; te lo recordamos en el panel."
        cuerpo = ('<div class="caminos">'
                  + camino("Recomendado para empezar", "Tu asistente", asistente_desc, PASOS_ASISTENTE, "≈ 10 min",
                           "Configurar mi asistente", "/panel/asistente", True)
                  + camino("Contenido", "Tu marca", "Tus colores, tu letra y tus fotos, para que tus posts se vean"
                           " de la misma casa.", PASOS_MARCA, "≈ 5 min", "Configurar mi marca",
                           "/panel/contenido/marca", False)
                  + "</div>")
    else:
        bajada = "En 10 minutos dejas tu asistente contestando tus mensajes. Son 4 pasos y puedes guardar y seguir después."
        cuerpo = ('<div class="sola">' + camino("Tu plan Básico", "Tu asistente", asistente_desc, PASOS_ASISTENTE,
                                                "≈ 10 min", "Empezar", "/panel/asistente", True) + "</div>")
    await c.store.set("bienvenida", "1")
    return pagina(
        cabecera(salir=True)
        + '<section class="oscuro portada compacta"><div class="contenedor">'
        f'<p class="antetitulo">Bienvenido</p><h1>Hola, @{escape(await usuario(c))}<span class="punto">.</span></h1>'
        f'<p class="bajada-bienvenida">{bajada}</p></div></section>'
        f'<main><div class="contenedor">{cuerpo}'
        '<p class="despues"><a href="/panel/asistente">Prefiero verlo todo primero</a></p></div></main>'
    )


async def inicio(c: Cuenta) -> str:
    """A dónde lleva /panel: la primera vez de una cuenta nueva, a la bienvenida; a Resultados
    si el asistente ya trabaja (encendido y con mensajes de clientes); si no, a Tu asistente."""
    if c.slug and await c.store.get("bienvenida") != "1" and not (await asistente_listo(c) and await marca_lista(c)):
        return "/panel/bienvenida"
    if await activo(c) and await c.store.has_client_messages():
        return "/panel/resultados"
    return "/panel/asistente"


@router.get("")
async def ver(request: Request):
    c = cuenta_de_sesion(request)
    return RedirectResponse(await inicio(c), status_code=303) if c else entrar()


@router.get("/bienvenida")
async def ver_bienvenida(request: Request):
    c = cuenta_de_sesion(request)
    return await bienvenida(c) if c else entrar()


@router.get("/asistente")
async def ver_asistente(request: Request):
    c = cuenta_de_sesion(request)
    return await panel_de(c, ver=request.query_params.get("ver", "")) if c else entrar()


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
    if accion == "restaurar" and not c.slug:
        c.prompt_editado().unlink(missing_ok=True)
        log.info("@%s volvió a las instrucciones del servidor", await usuario(c))
        return await panel_de(c, "Listo: tu asistente vuelve a usar las instrucciones del servidor.")
    if accion not in ("generar", "generar_si", "guardar"):
        return await panel_de(c, "", datos, prompt)
    datos["faq"] = [q for q in datos["faq"] if q["p"] or q["r"]]
    with usar(c):
        nombre = await usuario(c)
        # Si el cuadro trae instrucciones escritas a mano (no las que salen del formulario
        # guardado), generar las borraría: primero se pide confirmar.
        if accion == "generar" and prompt.strip() and prompt.strip() != generar(nombre, await perfil(c)).strip():
            return await panel_de(c, datos=datos, prompt=prompt, confirmar=True)
        await c.store.set("perfil", json.dumps(datos, ensure_ascii=False))
        if accion in ("generar", "generar_si"):
            return await panel_de(c, nota="Listo: revísalas y pulsa Guardar para que tu asistente las use.",
                                  datos=datos, prompt=generar(nombre, datos))
        prompt = prompt.strip() or generar(nombre, datos)
        destino = Path(c.prompt_path) if c.slug else c.prompt_editado()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(prompt + "\n", encoding="utf-8")
    await c.store.set("asistente_listo", "1")
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


@router.post("/pausas")
async def cambiar_pausa(request: Request):
    """Reactiva un chat en pausa o le da más tiempo a la persona que lo atiende."""
    de_aqui(request)
    if not (c := cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    f = await form(request)
    usuario_chat, accion = f.get("usuario", ""), f.get("accion")
    if accion == "tomar":
        if not await c.store.has_chat(usuario_chat):
            return await panel_de(c, "No encontramos ese chat.")
        await c.store.pause(usuario_chat, time.time() + c.human_pause_hours * 3600)
        with usar(c):
            nombre = await c.instagram.username(usuario_chat)
        log.info("@%s tomó el chat de %s desde el panel", await usuario(c), usuario_chat)
        return await panel_de(c, f"Listo: el asistente no le contesta a {nombre} en {c.human_pause_hours:g} h."
                              " Contéstale tú desde Instagram.")
    if usuario_chat not in {u for u, *_ in await c.store.paused_list(time.time())}:
        return await panel_de(c, "Ese chat ya no estaba en pausa.")
    with usar(c):
        nombre = await c.instagram.username(usuario_chat)
    if accion == "reactivar":
        await c.store.unpause(usuario_chat)
        aviso = f"Listo: tu asistente vuelve a contestarle a {nombre}."
    else:
        await c.store.pause(usuario_chat, time.time() + c.human_pause_hours * 3600)
        aviso = f"Listo: {nombre} sigue en pausa {c.human_pause_hours:g} h más."
    log.info("@%s cambió la pausa de %s desde el panel (%s)", await usuario(c), usuario_chat, accion)
    return await panel_de(c, aviso)


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


# Adonde llega la página (#ultimo) al mandar un mensaje: va justo después de
# "escribiendo…", en la primera parte, para que el navegador baje desde el inicio.
ANCLA = '<span class="ancla" id="ultimo"></span>'


def burbuja(rol: str, texto: str) -> str:
    return f'<div class="burbuja {"cliente" if rol == "user" else "agente"}">{escape(texto)}</div>'


async def prueba_inicio(c: Cuenta, mensajes: list[list[str]], aviso: str = "") -> str:
    """La página del chat de prueba hasta el último mensaje (sin cerrar el chat)."""
    burbujas = "".join(burbuja(rol, texto) for rol, texto in mensajes) or (
        '<p class="vacio">Escribe como si fueras un cliente. Usa las instrucciones que guardaste.</p>')
    return (
        PRINCIPIO
        + cabecera(salir=True, menu="asistente", cuenta=c)
        + '<section class="oscuro portada compacta"><div class="contenedor">'
        '<p class="antetitulo">Prueba tu asistente</p>'
        f'<h1>@{escape(await usuario(c))}<span class="punto">.</span></h1>'
        '<p class="bajada" style="margin-left:0">Así contestaría tus mensajes. Nada de esto llega a Instagram.</p>'
        "</div></section>"
        '<main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + f'<div class="chat">{burbujas}'
    )


async def prueba_fin(c: Cuenta, ancla: bool = True) -> str:
    _, usadas = await pruebas_hoy(c)
    return (
        (ANCLA if ancla else "")
        + '<form method="post" action="/panel/probar#ultimo" class="escribir">'
        f'<input type="text" name="mensaje" maxlength="{MAX_MENSAJE_PRUEBA}" placeholder="Escribe un mensaje" required autofocus>'
        '<button class="boton" name="accion" value="enviar">Enviar</button></form></div>'
        f'<p class="ayuda" style="margin-top:.75rem">Te quedan {max(MAX_PRUEBAS_DIA - usadas, 0)} mensajes de prueba hoy.</p>'
        '<div class="acciones"><a class="boton secundario" href="/panel/asistente">← Volver a tu asistente</a>'
        '<form method="post" action="/panel/probar"><button class="boton secundario" name="accion" value="reiniciar">'
        "Empezar de nuevo</button></form></div>"
        "</div></main>" + FINAL
    )


async def pagina_prueba(c: Cuenta, aviso: str = "") -> HTMLResponse:
    return HTMLResponse(await prueba_inicio(c, await historial(c), aviso) + await prueba_fin(c))


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

    async def pagina_en_partes():
        # Sin JavaScript: primero sale su mensaje con "escribiendo…" (el navegador ya
        # muestra la página nueva) y, cuando Claude contesta, el resto. Caddy no
        # guarda respuestas sin Content-Length, las pasa tal cual van llegando.
        yield (await prueba_inicio(c, previos + [["user", texto]])
               + '<div class="burbuja agente escribiendo" id="escribiendo"><span></span><span></span><span></span></div>'
               + ANCLA)
        try:
            respuesta = await responder_prueba(c, previos, texto)
        except Exception:
            log.exception("Falló la prueba del asistente de %s", c.slug)
            yield ('<style>#escribiendo{display:none}</style><div class="aviso">'
                   "Tu asistente no pudo contestar esta vez. Intenta de nuevo en un momento.</div>"
                   + await prueba_fin(c, ancla=False))
            return
        nuevos = previos + [["user", texto], ["assistant", respuesta]]
        await c.store.set("prueba", json.dumps(nuevos[-MAX_PRUEBA_TURNOS:], ensure_ascii=False))
        yield ("<style>#escribiendo{display:none}</style>" + burbuja("assistant", respuesta)
               + await prueba_fin(c, ancla=False))

    return StreamingResponse(pagina_en_partes(), media_type="text/html; charset=utf-8")


ARCHIVO_FUENTE = re.compile(r"^(Inter|Montserrat|Poppins|PlayfairDisplay|Lora)-(Regular|SemiBold|Bold)\.ttf$")


@router.get("/static/panel.js")
def script():
    return FileResponse(STATIC / "panel.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/static/fuentes/{archivo}")
def fuente(archivo: str):
    if not ARCHIVO_FUENTE.match(archivo):
        raise HTTPException(status_code=404)
    return FileResponse(FONTS / archivo, media_type="font/ttf", headers={"Cache-Control": "public, max-age=86400"})


@router.post("/salir")
async def salir(request: Request):
    de_aqui(request)
    response = RedirectResponse("/panel", status_code=303)
    response.delete_cookie(COOKIE, path="/panel")
    return response
