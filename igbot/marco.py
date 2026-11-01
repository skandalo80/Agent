"""El marco del panel (console.skandar.online/panel): lo que comparten todas las secciones.

- La sesión: cookie firmada con SESSION_SECRET que dura SESSION_DAYS, con la versión de sesión de la cuenta
  (cerrar_sesiones la sube y saca a todos los dispositivos); de_aqui protege los POST (cabecera Origin).
- La página: la hoja de estilos (/panel/static/panel.css con su versión), el menú lateral, la cabecera, la
  página de entrar, la 404 y la de error, y los pedazos que repiten las secciones (encabezado, cab_tarjeta,
  pie_config, avance, paginas, hace).
- Lo que le falta configurar a la cuenta (Pasos iniciales, Te falta).
- Los trabajos largos (generar una campaña, sus imágenes) con su página de espera, y las conversaciones en vivo (SSE).
- Los archivos estáticos y salir.

Las secciones (panel.py = Tu asistente, empresa.py, campanas.py…) importan este módulo; él no importa a ninguna
al cargarse, así ninguna necesita importaciones tardías para esquivar ciclos. app.py le da buscar, alta y
conectar_pagina al arrancar.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse

from . import config
from .cuenta import Cuenta
from .estilos import FUENTES

log = logging.getLogger("igbot.marco")

router = APIRouter(prefix="/panel")
# Los pone app.py al arrancar: la cuenta (ya cargada) de un id de Instagram.
buscar: Callable[[str], Cuenta | None] = lambda ig_id: None
# Y la que da de alta una cuenta nueva al conectarse (None si no tiene panel).
alta: Callable[..., Awaitable[Cuenta | None]] | None = None
# Y la que empieza a escuchar la página de Facebook que acaba de conectar (Messenger y comentarios).
conectar_pagina: Callable[[Cuenta], Awaitable[None]] | None = None

COOKIE = "panel"
SESSION_DAYS = 30  # Instagram vuelve a pedir el permiso en cada inicio de sesión


# --- sesión ---

def _key(secreto: str = "") -> bytes:
    return hmac.new((secreto or config.SESSION_SECRET).encode(), b"panel-sesion", hashlib.sha256).digest()


# Versión de sesión de cada cuenta (clave kv "sesion_version"): va dentro de la firma de la cookie, así "Cerrar
# sesión en todos los dispositivos" solo tiene que subirla. La lee app.py al arrancar cada cuenta.
VERSIONES: dict[str, str] = {}


def version_de(ig_id: str) -> str:
    return VERSIONES.get(ig_id, "0")


async def cargar_version(c: Cuenta) -> None:
    if c.ig_account_id:
        VERSIONES[c.ig_account_id] = await c.store.get("sesion_version") or "0"


async def cerrar_sesiones(c: Cuenta) -> None:
    """Invalida todas las cookies de la cuenta (las de hoy y las viejas)."""
    nueva = str(int(version_de(c.ig_account_id)) + 1)
    await c.store.set("sesion_version", nueva)
    VERSIONES[c.ig_account_id] = nueva


def _firma(ig_id: str, vence: int, version: str | None = None) -> str:
    v = version_de(ig_id) if version is None else version
    return hmac.new(_key(), f"{ig_id}.{vence}.{v}".encode(), hashlib.sha256).hexdigest()


def _firma_vieja(ig_id: str, vence: int) -> str:
    """La cookie de antes: ig_id.vence.firma con IG_APP_SECRET. Vale mientras nadie cierre las sesiones de la cuenta,
    para que el cambio no saque a nadie del panel."""
    return hmac.new(_key(config.APP_SECRET), f"{ig_id}.{vence}".encode(), hashlib.sha256).hexdigest()


def cookie_sesion(ig_id: str, vence: int) -> str:
    v = version_de(ig_id)
    return f"{ig_id}.{vence}.{v}.{_firma(ig_id, vence, v)}"


def iniciar_sesion(response, ig_id: str) -> None:
    vence = int(time.time()) + SESSION_DAYS * config.DIA
    response.set_cookie(COOKIE, cookie_sesion(ig_id, vence), max_age=SESSION_DAYS * config.DIA,
                        path="/panel", secure=True, httponly=True, samesite="lax")


def sesion(request: Request) -> str | None:
    """El id de Instagram de quien entró, si la cookie es válida, no ha vencido y no se cerraron sus sesiones."""
    partes = request.cookies.get(COOKIE, "").split(".")
    try:
        if len(partes) == 4:
            ig_id, vence, v, firma = partes
            if (int(vence) > time.time() and v == version_de(ig_id)
                    and hmac.compare_digest(firma, _firma(ig_id, int(vence), v))):
                return ig_id
        elif len(partes) == 3:
            ig_id, vence, firma = partes
            if (int(vence) > time.time() and version_de(ig_id) == "0"
                    and hmac.compare_digest(firma, _firma_vieja(ig_id, int(vence)))):
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
    return not c.en_revision and await c.store.get("bot_activo") != "0"


async def perfil(c: Cuenta) -> dict:
    return json.loads(await c.store.get("perfil") or "{}")


async def usuario(c: Cuenta) -> str:
    return await c.store.get("account_username") or c.slug


async def foto_perfil(c: Cuenta) -> str:
    """La dirección del logo de Tu marca (para el menú lateral), o "" si no hay."""
    import json
    from pathlib import Path
    token = json.loads(await c.store.get("marca") or "{}").get("logo", "")
    path = Path(c.media_dir) / f"{token}.jpg"
    if not token or not path.is_file():
        return ""
    return f"/media/{c.media_slug}/{token}.jpg?v={int(path.stat().st_mtime)}"


# --- páginas ---

# Misma identidad que skandar.online (web/estilos.css): negro, grises de Apple,
# botones en píldora y el acento naranja. Sin fuentes externas (el CSP del bot
# no las deja): "Inter" si la tiene instalada, si no la del sistema.
CSS_DIR = Path(__file__).resolve().parent / "static" / "css"
# Las hojas de estilo viven en igbot/static/css/*.css y se sirven en /panel/static/panel.css (ver HOJA).
ESTILO = (CSS_DIR / "panel.css").read_text(encoding="utf-8")

# Los tamaños (letras, rellenos, huecos, esquinas) salen de igbot/ajustes.css: se cambian ahí y cambian
# en todo el panel. La web tiene los suyos en web/ajustes.css.
AJUSTES = (Path(__file__).resolve().parent / "ajustes.css").read_text(encoding="utf-8")

# Un solo estilo para todo el panel. Va al final de todo (también después del CSS propio de Tu asistente y
# Tu empresa), así manda sobre las reglas viejas de cada página: antetítulo, título, bajada, tarjetas,
# cabecera de tarjeta con Editar, rejilla del resumen y la barra de avance bajo los pasos.
COMUN = AJUSTES + (CSS_DIR / "comun.css").read_text(encoding="utf-8")


# Las tipografías de los posts (fonts/), para que los botones de letra se vean con la suya.
FONTS = Path(__file__).resolve().parent.parent / "fonts"
STATIC = Path(__file__).resolve().parent / "static"
FUENTES_CSS = "".join(
    f"@font-face{{font-family:'{familia}';src:url(/panel/static/fuentes/{archivo}-{peso_nombre}.ttf);"
    f"font-weight:{peso};font-display:swap}}"
    for familia, archivo in FUENTES.items()
    for peso, peso_nombre in ((400, "Regular"), (600, "SemiBold"), (700, "Bold"))
)

# La versión de panel.js va en su dirección: al cambiar el archivo, el navegador no usa la vieja de su caché.
VERSION_JS = hashlib.sha256((STATIC / "panel.js").read_bytes()).hexdigest()[:10]
VERSION_MENU = hashlib.sha256((STATIC / "menu.js").read_bytes()).hexdigest()[:10]
# Toda la hoja del panel en un archivo con su versión en la dirección: el navegador la guarda y cada página pesa
# 60 KB menos. Las fuentes (@font-face) van inline porque salen de estilos.FUENTES.
HOJA = ESTILO + (CSS_DIR / "conversaciones.css").read_text(encoding="utf-8") + (CSS_DIR / "skandi.css").read_text(encoding="utf-8") + COMUN
VERSION_CSS = hashlib.sha256(HOJA.encode()).hexdigest()[:10]
PRINCIPIO = (
    '<!doctype html><html lang="es"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">'
    '<meta name="robots" content="noindex"><meta name="referrer" content="same-origin">'
    '<meta name="theme-color" content="#2B2C30">'
    '<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon-32.png" sizes="32x32">'
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
    # Agregar a inicio: abre como app (manifest para Android y Chrome; las metas, para iPhone).
    '<link rel="manifest" href="/manifest.webmanifest">'
    '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="Skandar">'
    '<title>Tu asistente · Skandar</title>'
    f'<link rel="stylesheet" href="/panel/static/panel.css?v={VERSION_CSS}">'
    f"<style>{FUENTES_CSS}</style>"
    # Solo JavaScript propio (CSP script-src 'self'): la vista previa al momento en Contenido.
    # menu.js sin defer: pliega el menú antes de pintar la página (si no, se ve abrirse y cerrarse).
    f'<script src="/panel/static/menu.js?v={VERSION_MENU}"></script>'
    f'<script src="/panel/static/panel.js?v={VERSION_JS}" defer></script></head><body>'
)
FINAL = "</body></html>"


def pagina(cuerpo: str) -> HTMLResponse:
    return HTMLResponse(PRINCIPIO + cuerpo + FINAL)


ACTIVA = ' class="on"'
# El menú lateral, sin grupos: cada sección con su ícono. (clave, nombre, dirección, grupo al que pertenece)
MENU = [("inicio", "Inicio", "/panel/inicio", "inicio"),
        ("resultados", "Resultados", "/panel/resultados", "resultados"),
        ("chats", "Conversaciones", "/panel/asistente", "asistente"),
        ("probar", "Probar tu asistente", "/panel/probar#ultimo", "asistente"),
        ("campanas", "Campañas", "/panel/contenido/campanas", "contenido"),
        ("calendario", "Calendario", "/panel/contenido", "contenido")]
# Abajo, bajo la etiqueta "Configuración" (arriba de Ayuda): lo que se configura una vez. (clave, nombre, dirección)
# Tu marca y Tus interacciones solo con contenido en el plan.
CONFIGURACION = [("empresa", "Tu empresa", "/panel/empresa"),
                 ("configura", "Tu asistente", "/panel/asistente/configura"),
                 ("marca", "Tu marca", "/panel/contenido/marca"),
                 ("interacciones", "Tus interacciones", "/panel/interacciones")]
CON_CONTENIDO = {"marca", "interacciones"}
# La sección que se marca si la página no dice cuál (`sub`) dentro de su grupo (`menu`).
POR_GRUPO = {"inicio": "inicio", "resultados": "resultados", "asistente": "chats", "contenido": "calendario"}


# Íconos del menú lateral: de línea, todos del mismo trazo y tamaño (dibujos de Lucide, licencia ISC).
def _icono(trazos: str, tam: int = 20) -> str:
    return (f'<svg viewBox="0 0 24 24" width="{tam}" height="{tam}" fill="none" stroke="currentColor" stroke-width="1.75"'
            f' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{trazos}</svg>')


ICONOS = {
    "inicio": _icono('<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999'
                     'a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    "resultados": _icono('<path d="M3 3v16a2 2 0 0 0 2 2h16M18 17V9M13 17V5M8 17v-3"/>'),
    "chats": _icono('<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/>'
                    '<path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>'),
    "configura": _icono('<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/>'
                        '<path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>'),
    "probar": _icono('<path d="M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.64l-19 6.5a.5.5 0 0 0-.03.93'
                     'l7.93 3.18a2 2 0 0 1 1.11 1.11zM21.85 2.15 10.91 13.09"/>'),
    "campanas": _icono('<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>'),
    "calendario": _icono('<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M8 2v4M16 2v4M3 10h18'
                         'M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>'),
    "interacciones": _icono('<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>'),
    "marca": _icono('<path d="M12 22a10 10 0 1 1 10-10c0 3-2.5 5-5.5 5H15a1.7 1.7 0 0 0-1.3 2.8c.3.3.4.7.4 1.1'
                    ' 0 .9-.7 1.6-1.6 1.6z"/><circle cx="13.5" cy="6.5" r=".6" fill="currentColor"/>'
                    '<circle cx="17.5" cy="10.5" r=".6" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".6" fill="currentColor"/>'
                    '<circle cx="6.5" cy="12.5" r=".6" fill="currentColor"/>'),
    "empresa": _icono('<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18zM6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2'
                      'M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2M10 6h4M10 10h4M10 14h4M10 18h4"/>'),
    "ayuda": _icono('<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>'),
    "salir": _icono('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>'),
    "plegar": _icono('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18M16 15l-3-3 3-3"/>'),
    "hecho": _icono('<path d="M20 6 9 17l-5-5"/>', 14),
}


async def pasos_iniciales(c: Cuenta) -> list[tuple[str, str, bool]]:
    """Lo primero que conviene dejar listo, en orden: (nombre, dirección, ¿hecho?).
    Tu marca y la primera campaña solo con contenido en el plan."""
    pasos = [("Llena tu Perfil de empresa", "/panel/empresa", await empresa_lista(c)),
             ("Configura tu asistente", "/panel/asistente/configura", await asistente_listo(c)),
             ("Pruébalo como cliente", "/panel/probar#ultimo", await c.store.get("probo") == "1"),
             ("Enciende tu asistente", "/panel/asistente", await activo(c))]
    if c.imagenes_mes > 0:
        from .campanas import hay_campanas  # campanas importa este módulo
        pasos += [("Define tu marca", "/panel/contenido/marca", await marca_lista(c)),
                  ("Crea tu primera campaña", "/panel/contenido/campanas", await hay_campanas(c))]
    return pasos


def tarjeta_pasos(pasos: list[tuple[str, str, bool]]) -> str:
    """Abajo del menú, mientras falte alguno: un anillo con el avance y, al abrirla, la lista."""
    listos = sum(1 for *_, hecho in pasos if hecho)
    if not pasos or listos == len(pasos):
        return ""
    # El anillo: un círculo de perímetro 100 (r = 100 / 2π), lleno hasta el porcentaje.
    pct = round(100 * listos / len(pasos))
    anillo = ('<svg class="anillo" viewBox="0 0 36 36" width="34" height="34" aria-hidden="true">'
              '<circle cx="18" cy="18" r="15.915" class="fondo"/>'
              f'<circle cx="18" cy="18" r="15.915" class="lleno" stroke-dasharray="{pct} 100"/></svg>')
    lista = "".join(
        f'<a href="{href}" class="{"hecho" if hecho else ""}"><span class="marca-paso">'
        f'{ICONOS["hecho"] if hecho else ""}</span>{nombre}</a>' for nombre, href, hecho in pasos)
    return ('<details class="perfil pasos-ini">'
            f'<summary class="lat-item" title="Pasos iniciales · {listos} de {len(pasos)}">'
            f'<span class="anillo-caja">{anillo}<b>{pct}%</b></span>'
            f'<span class="txt">Pasos iniciales<small>{listos} de {len(pasos)} listos</small></span></summary>'
            f'<div class="perfil-menu"><div class="quien">Deja listo lo básico para empezar</div>{lista}</div></details>')


async def menu_de(c: Cuenta, menu: str, sub: str = "", luces: dict[str, str] | None = None) -> str:
    """El menú lateral de una página con sesión: con su @usuario, su logo y los pasos iniciales.
    Conversaciones lleva un punto lima si hay chats del lado "Tú" esperando tu respuesta."""
    from . import conversaciones  # conversaciones importa este módulo
    from .limites import LIMIT_NOTICE
    ahora = time.time()
    if await c.store.pendientes_tuyos(ahora, ahora - c.human_pause_hours * config.HORA - 60,
                                      ahora - conversaciones.SIN_RESPUESTA, LIMIT_NOTICE):
        luces = {**(luces or {}), "chats": "pendiente"}
    return cabecera(salir=True, menu=menu, cuenta=c, sub=sub, perfil=await usuario(c), foto=await foto_perfil(c),
                    luces=luces, pasos=await pasos_iniciales(c))


def cabecera(salir: bool = False, menu: str = "", cuenta: Cuenta | None = None, sub: str = "",
             perfil: str = "", luces: dict[str, str] | None = None, foto: str = "",
             pasos: list[tuple[str, str, bool]] | None = None) -> str:
    """Con `menu` (la sección en la que está): el menú lateral, plegable a solo íconos y con el
    botón de hamburguesa en el celular. Contenido sale solo si el plan de `cuenta` lo tiene.
    Con `perfil` (su @usuario), abajo va su menú de perfil en vez de solo Salir.
    `luces`: un semáforo junto a una parte del submenú o del menú de perfil (p. ej. {"conexion": "ok"}).
    `sub` también marca la parte activa de Configuración o del menú de perfil (Conexión vive ahí).
    Abajo, como en otras plataformas: los pasos iniciales (`pasos`, mientras falte alguno), Ayuda y tu cuenta.
    Sin menú (la entrada y las páginas de espera): la barra de arriba de siempre."""
    from . import skandi  # skandi importa este módulo
    boton = ('<form method="post" action="/panel/salir"><button class="salir">Salir</button></form>'
             if salir else "")
    # Con sesión, el logo lleva al inicio del panel; sin sesión, a la página principal.
    inicio_marca = "/panel" if salir else config.SITE_URL
    if not menu:
        return (f'<header><div class="contenedor"><a class="marca" href="{inicio_marca}"><img class="logo-largo" src="/logo-skandar.svg" alt="Skandar" width="132" height="36"></a>'
                f"{boton}</div></header>")
    en_config = any(k == sub for k, *_ in CONFIGURACION)
    activa = sub if any(k == sub for k, *_ in MENU) else "" if en_config else POR_GRUPO.get(menu, "")
    secciones = []
    for k, nombre, href, grupo in MENU:
        if grupo == "contenido" and cuenta is not None and cuenta.imagenes_mes <= 0:
            continue
        luz = f' <span class="semaforo sem-{(luces or {})[k]}"></span>' if k in (luces or {}) else ""
        # "pendiente": un punto en la esquina del ícono, que también se ve con el menú plegado.
        punto = '<span class="punto-menu" title="Tienes chats por contestar"></span>' if luz and "sem-pendiente" in luz else ""
        luz = "" if punto else luz
        secciones.append(f'<a class="lat-item{" on" if k == activa else ""}" href="{href}" title="{nombre}">'
                         f'{ICONOS[k]}{punto}<span class="txt">{nombre}{luz}</span></a>')
    ajustes = "".join(
        f'<a class="lat-item{" on" if k == sub else ""}" href="{href}" title="{nombre}">{ICONOS[k]}'
        f'<span class="txt">{nombre}</span></a>' for k, nombre, href in CONFIGURACION
        if k not in CON_CONTENIDO or cuenta is None or cuenta.imagenes_mes > 0)
    # Sin JavaScript también abre en el celular: la hamburguesa apunta a #lateral (:target).
    return (
        '<header class="movil"><a class="hamburguesa" href="#lateral" aria-label="Abrir menú">'
        '<span></span><span></span><span></span></a>'
        '<a class="marca" href="/panel"><img class="logo-largo" src="/logo-skandar.svg" alt="Skandar" width="132" height="36"></a></header>'
        '<aside class="lateral" id="lateral"><div class="lat-cabeza">'
        # El logo completo con el menú abierto; plegado, solo el ícono Sk.
        '<a class="marca" href="/panel"><img class="logo-largo" src="/logo-skandar.svg" alt="Skandar" width="132" height="36"><img class="logo-corto" src="/logo-sk.svg" alt="Skandar" width="48" height="36"></a>'
        f'<button class="plegar" type="button" data-plegar aria-label="Plegar o fijar el menú" title="Plegar o fijar">'
        f'{ICONOS["plegar"]}</button><a class="cerrar" href="#" aria-label="Cerrar menú">×</a></div>'
        f'<nav class="lat-nav">{"".join(secciones)}</nav>'
        + '<div class="lat-abajo">' + tarjeta_pasos(pasos or [])
        + (f'<div class="lat-etiqueta"><span class="txt">Configuración</span></div>{ajustes}' if cuenta is not None else "")
        # Ayuda abre a Skandi (la ventanita de abajo).
        + (f'<a class="lat-item" href="#skandi" data-abrir-skandi title="Ayuda">{ICONOS["ayuda"]}'
           '<span class="txt">Ayuda</span></a>' if cuenta is not None else "") + '</div>'
        + menu_perfil(perfil, cuenta, foto, sub, luces or {}) + '</aside>'
        '<a class="velo" href="#" aria-label="Cerrar menú"></a>'
        # Skandi (la ayuda), en todas las páginas con menú.
        + (skandi.widget(cuenta) if cuenta is not None else "")
    )


def menu_perfil(perfil: str, cuenta: Cuenta | None, foto: str = "", sub: str = "",
                luces: dict[str, str] | None = None) -> str:
    salir = ('<form method="post" action="/panel/salir"{}><button class="{}" title="Salir">{}</button></form>')
    if not perfil:
        return salir.format(' class="lat-pie"', "lat-item", f'{ICONOS["salir"]}<span class="txt">Salir</span>')
    usuario_ = escape(perfil)
    # El logo de Tu marca si ya subió uno; si no, la inicial.
    avatar = (f'<img class="lat-avatar" src="{escape(foto)}" alt="">' if foto else
              f'<span class="lat-avatar">{usuario_[:1].upper()}</span>')
    luz = f' <span class="semaforo sem-{luces["conexion"]}"></span>' if "conexion" in (luces or {}) else ""
    # <details>: abre y cierra sin JavaScript; panel.js lo cierra al hacer clic fuera.
    return ('<div class="lat-pie"><details class="perfil">'
            f'<summary class="lat-item" title="@{usuario_}">{avatar}'
            f'<span class="txt">@{usuario_}<small>Tu cuenta</small></span></summary>'
            # Quién eres y la cuenta (conexión, Instagram y salir); tu empresa y tu marca están en Configuración.
            f'<div class="perfil-menu"><div class="quien">Conectado como @{usuario_}</div>'
            # Solo la cuenta principal (Skandar): los chats de clientes y visitantes con Skandi.
            + (f'<a href="/panel/skandi"{ACTIVA if sub == "skandi" else ""}>Chats de Skandi</a>'
               f'<a href="/panel/salud"{ACTIVA if sub == "salud" else ""}>Salud del servicio</a><div class="sep"></div>'
               if cuenta is not None and not cuenta.slug else "")
            + f'<a href="/panel/conexion"{ACTIVA if sub == "conexion" else ""}>Conexión{luz}</a>'
            f'<a href="https://www.instagram.com/{usuario_}/" target="_blank" rel="noopener">Ver mi Instagram</a>'
            + salir.format("", "", "Salir") + "</div></details></div>")


ICONO_GOOGLE = ('<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7'
                ' 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4'
                ' 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00"'
                ' d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3'
                ' 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7'
                ' 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6'
                ' 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/>'
                '</svg>')
ICONO_FACEBOOK = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#1877F2" d="M24 12a12 12 0 1 0-13.9'
                  ' 11.9v-8.4H7.1V12h3V9.4c0-3 1.8-4.7 4.5-4.7 1.3 0 2.7.2 2.7.2v3h-1.5c-1.5 0-2 .9-2 1.9V12h3.4l-.5'
                  ' 3.5h-2.9v8.4A12 12 0 0 0 24 12z"/></svg>')
ICONO_APPLE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M16.4 12.6c0-2.6 2.1-3.8'
               ' 2.2-3.9-1.2-1.8-3.1-2-3.7-2-1.6-.2-3.1.9-3.9.9-.8 0-2-.9-3.4-.9-1.7 0-3.3 1-4.2 2.6-1.8 3.1-.5'
               ' 7.7 1.3 10.2.9 1.2 1.9 2.6 3.2 2.6 1.3-.1 1.8-.8 3.3-.8 1.6 0 2 .8 3.4.8 1.4 0 2.3-1.3 3.1-2.5 1-1.4'
               ' 1.4-2.8 1.4-2.9 0 0-2.7-1-2.7-4.1zM13.9 5c.7-.9 1.2-2.1 1.1-3.3-1 0-2.3.7-3 1.6-.7.8-1.2 2-1.1'
               ' 3.2 1.1.1 2.3-.6 3-1.5z"/></svg>')
ICONO_CORREO = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"'
                ' stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/>'
                '<path d="m22 7-10 6L2 7"/></svg>')


def entrar(request: Request | None = None, aviso: str = "") -> HTMLResponse:
    """La pantalla de entrar: correo (enlace de un solo uso), Google, Apple (si hay llaves) y Facebook. Instagram ya no: en el iPhone
    con la app instalada se queda en "Loading". Quien entra por primera vez con correo o Google enlaza su
    Instagram en el paso siguiente (acceso.py); con Facebook llega ya enlazado."""
    from . import acceso, agenda_google, facebook
    ultimo = request.cookies.get(acceso.COOKIE_ULTIMO, "") if request else ""

    def marca(metodo: str) -> str:
        return '<span class="ultimo-usado">Último usado</span>' if metodo == ultimo else ""

    otros = ""
    if agenda_google.enabled():
        otros += (f'<a class="boton-entrar" href="/panel/correo/google">{ICONO_GOOGLE}Entrar con Google'
                  f'{marca("google")}</a>')
    if acceso.apple_activo():
        otros += (f'<a class="boton-entrar" href="/panel/correo/apple">{ICONO_APPLE}Entrar con Apple'
                  f'{marca("apple")}</a>')
    if facebook.enabled():
        otros += (f'<a class="boton-entrar" href="/panel/facebook/entrar">{ICONO_FACEBOOK}Entrar con Facebook'
                  f'{marca("facebook")}</a>')
    return pagina(
        cabecera()
        + '<section class="oscuro entrar"><div class="contenedor"><div class="caja-entrar">'
        '<h1>Entra a Skandar<span class="punto">.</span></h1>'
        '<p class="bajada">Tus redes sociales, trabajando.</p>'
        + (f'<p class="aviso-entrar">{escape(aviso)}</p>' if aviso else "")
        + '<form class="entrar-correo" method="post" action="/panel/correo">'
        '<label for="correo-entrar">Tu correo</label>'
        '<input id="correo-entrar" type="email" name="correo" required maxlength="254" autocomplete="email"'
        ' placeholder="tu@correo.com">'
        '<p class="ayuda-entrar">Te mandamos un enlace de un solo uso para entrar.</p>'
        f'<button class="boton boton-ancho" type="submit">{ICONO_CORREO}Entrar con mi correo{marca("correo")}'
        "</button></form>"
        + (f'<div class="o-entra"><span>o entra con</span></div><div class="otros-entrar">{otros}</div>'
           if otros else "")
        + f'<p class="nota">¿Primera vez? Entra con cualquiera de estas opciones y enlazamos tu Instagram.'
        f' <a class="enlace" href="{config.SITE_URL}">Conoce Skandar ›</a></p>'
        "</div></div></section>"
    )


def hace(t: float) -> str:
    s = time.time() - t
    if s < 60:
        return "hace un momento"
    if s < config.HORA:
        return f"hace {int(s // 60)} min"
    if s < config.DIA:
        return f"hace {int(s // 3600)} h"
    return "ayer" if s < 2 * 86400 else f"hace {int(s // 86400)} días"


def avance(pct: int) -> str:
    """La barra de avance que va abajo de la barra de pasos (Tu asistente, Tu marca, Tu empresa, Crear campaña)."""
    return f'<div class="avance-pasos" aria-hidden="true"><i style="width:{pct}%"></i></div>'


def pie_config(atras: str, siguiente: str, nota: str = "") -> str:
    """El pie de cada paso de Configuración (Tu empresa, Tu asistente, Tu marca), igual en las tres: `atras` (el
    ← Anterior, o nada en el paso 1) y `nota` a la izquierda; Aplicar y `siguiente` (el botón naranja) a la derecha.
    Aplicar manda accion=aplicar: guarda y se queda en el mismo paso."""
    return (f'<div class="pie-config">{atras}<span class="pie-nota">{nota}</span><span class="pie-der">'
            '<button class="boton aplicar" name="accion" value="aplicar">Aplicar</button>'
            f"{siguiente}</span></div>")


def cab_tarjeta(titulo: str, href: str = "", texto: str = "Editar", boton: str = "") -> str:
    """Cabecera de una tarjeta de resumen: su título y el botón Editar, igual en todo el panel."""
    accion = boton or (f'<a class="editar" href="{href}">{texto}</a>' if href else "")
    return f'<div class="cab-tarjeta"><h3>{titulo}</h3>{accion}</div>'


def encabezado(ante: str = "", botones: str = "", extra: str = "") -> str:
    """La franja de arriba de cada sección: sus botones a la derecha y `extra` debajo (el estado del
    asistente, las pestañas, los pasos). Sin antetítulo (`ante` ya no se muestra); sin nada, no sale."""
    if not botones and not extra:
        return ""
    fila = f'<div class="titulo-fila sin-ante">{botones}</div>' if botones else ""
    return ('<section class="oscuro portada compacta delgada"><div class="contenedor">'
            f'{fila}{extra}</div></section>')


def paginas(actual: int, total: int, enlace) -> str:
    """‹ 1 2 3 … 9 ›: la primera, la última y las de junto a la actual; lo demás, puntitos.
    `enlace(n)` da la dirección de la página n."""
    if total <= 1:
        return ""
    numeros = sorted({1, total, *range(max(1, actual - 1), min(total, actual + 1) + 1)})
    partes, antes = [], 0
    for n in numeros:
        if n - antes > 1:
            partes.append('<span class="puntos">…</span>')
        partes.append(f'<span class="on" aria-current="page">{n}</span>' if n == actual else
                      f'<a href="{escape(enlace(n))}">{n}</a>')
        antes = n

    def flecha(n: int, signo: str, nombre: str) -> str:
        return (f'<a href="{escape(enlace(n))}" aria-label="{nombre}">{signo}</a>'
                if 1 <= n <= total else f'<span class="apagada" aria-hidden="true">{signo}</span>')
    return (f'<nav class="paginas" aria-label="Páginas">{flecha(actual - 1, "‹", "Anterior")}'
            f'{"".join(partes)}{flecha(actual + 1, "›", "Siguiente")}</nav>')


def plural_(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


# --- lo que le falta configurar a la cuenta (Pasos iniciales, Te falta) ---

async def asistente_listo(c: Cuenta) -> bool:
    """Ya guardó sus instrucciones (o llenó el formulario antes de que hubiera bienvenida).
    La principal usa las del servidor: siempre está lista."""
    return not c.slug or await c.store.get("asistente_listo") == "1" or bool(await perfil(c))


async def marca_lista(c: Cuenta) -> bool:
    """Tu marca tiene al menos un estilo visual y sus colores (marca.lista); sin contenido en el plan, no hace falta."""
    from .marca import lista  # import tardío: marca importa contenido, que importa este módulo
    return c.imagenes_mes <= 0 or lista(json.loads(await c.store.get("marca") or "{}"))


async def empresa_lista(c: Cuenta) -> bool:
    """Su Perfil de empresa tiene lo esencial (igbot/empresa.py). La principal no lo necesita."""
    if not c.slug:
        return True
    from . import empresa  # empresa importa este módulo
    return await empresa.empresa_lista(c)


async def pendiente(c: Cuenta, aqui: str) -> str:
    """Un aviso arriba si le falta configurar otra parte (no la de la página donde está)."""
    if aqui != "empresa" and not await empresa_lista(c):
        return ('<div class="aviso">Te falta llenar tu <b>Perfil de empresa</b>: con eso tu asistente responde con'
                ' tus datos y tus posts hablan de tu negocio. <a href="/panel/empresa">Llenarlo</a></div>')
    if aqui != "asistente" and not await asistente_listo(c):
        return ('<div class="aviso">Te falta configurar <b>Tu asistente</b>: sin eso no contesta tus mensajes.'
                ' <a href="/panel/asistente">Configurarlo</a></div>')
    if aqui != "marca" and not await marca_lista(c):
        return ('<div class="aviso">Te falta configurar <b>Tu marca</b> para que tus posts se vean como tú.'
                ' <a href="/panel/contenido/marca">Configurarla</a></div>')
    return ""


async def no_encontrada(request: Request) -> HTMLResponse:
    """La página 404 del panel: con el menú lateral si hay sesión."""
    c = cuenta_de_sesion(request)
    menu = (await menu_de(c, "ninguna")
            if c else cabecera())
    destino = ("/panel", "Ir a mi panel") if c else ("/panel", "Entrar a mi panel")
    respuesta = pagina(
        menu + '<section class="oscuro portada compacta delgada"><div class="contenedor">'
        '<h1>Esta página no existe<span class="punto">.</span></h1>'
        '<p class="bajada" style="margin-left:0">Puede que el enlace esté mal escrito o que la página ya no exista.</p>'
        '</div></section><main><div class="contenedor"><div class="tarjeta"><h2>¿A dónde quieres ir?</h2>'
        f'<div class="acciones"><a class="boton" href="{destino[0]}">{destino[1]}</a>'
        f'<a class="boton secundario" href="{config.SITE_URL}">Ir a skandar.online</a></div></div>'
        "</div></main>")
    respuesta.status_code = 404
    return respuesta


# --- trabajos largos (generar campaña, imágenes, otra idea) ---
# Corren en segundo plano, aparte de la página: si el dueño cierra la pestaña o se le bloquea el celular,
# siguen. La página de espera se recarga sola (sin JavaScript) y muestra el avance; al terminar, lleva al
# resultado. Uno a la vez por cuenta y tipo: un doble clic o recargar no lo repite ni gasta cupo doble.
# Viven en memoria: si igbot se reinicia a la mitad, lo que ya salió se queda guardado y el resto se pide otra vez.
ESPERA_TRABAJO = 2.0  # segundos que la respuesta espera: lo rápido llega ya terminado, sin página de espera
RECARGA_TRABAJO = 3  # cada cuánto se recarga la página de espera
GUARDAR_TRABAJO = config.HORA  # los terminados se olvidan después de esto
_trabajos: dict[str, "Trabajo"] = {}


class Trabajo:
    """Un trabajo largo: `correr` es un generador async que va dando líneas de avance ("Pieza 2 de 9…") y,
    si quiere, la dirección a la que lleva al terminar (una línea que empieza con "/")."""

    def __init__(self, c: Cuenta, clave: str, titulo: str, nota: str, destino: str, menu: tuple[str, str],
                 boton: str, correr, segundos: float = 30, pasos: tuple[str, ...] = ()):
        self.id = secrets.token_urlsafe(9)
        self.slug, self.clave, self.titulo, self.nota = c.slug, clave, titulo, nota
        self.destino, self.menu, self.boton = destino, menu, boton
        self.segundos, self.pasos, self.inicio = max(segundos, 3), pasos, time.time()
        self.avance: list[str] = []
        self.error = ""
        self.hecho = False
        self.fin = 0.0
        self.tarea = asyncio.create_task(self._correr(c, correr))

    async def _correr(self, c: Cuenta, correr) -> None:
        from . import imagenes
        try:
            async for linea in correr():
                if linea.startswith("/"):
                    self.destino = linea
                else:
                    self.avance.append(linea)
        except imagenes.ErrorImagen as e:
            self.error = str(e)
        except Exception:
            log.exception("Trabajo «%s» de %s", self.titulo, c.slug or "la principal")
            self.error = "No se pudo terminar esta vez. Lo que no salió no usó tu cupo; intenta de nuevo en un momento."
        finally:
            self.hecho, self.fin = True, time.time()


def curva(t: Trabajo) -> dict:
    """Cómo avanza la barra de un trabajo: rápido al principio y cada vez más lento, sin llegar al 100 hasta que
    termina. El tiempo se estira y se encoge con una onda propia de cada trabajo (sale de su id), así que nunca
    avanza igual dos veces. panel.js hace la misma cuenta para moverla sin recargar."""
    h = int(hashlib.sha256(t.id.encode()).hexdigest()[:12], 16)
    parte = lambda i: (h >> (10 * i)) % 1000 / 1000  # noqa: E731
    return {"tau": round(t.segundos * (0.85 + 0.3 * parte(0)) / 2.3, 3), "a": round(0.45 + 0.4 * parte(1), 3),
            "w": round(2 * math.pi / (6 + 8 * parte(2)), 4), "f": round(2 * math.pi * parte(3), 3)}


def porcentaje(k: dict, segundos: float) -> float:
    tiempo = segundos + k["a"] * (math.sin(k["w"] * segundos + k["f"]) - math.sin(k["f"])) / k["w"]
    return 96 * (1 - math.exp(-tiempo / k["tau"]))


def paso_de(pasos: tuple[str, ...], pct: float) -> str:
    return pasos[min(len(pasos) - 1, int(pct / 100 * len(pasos) * 1.08))] if pasos else ""


def en_curso(c: Cuenta, clave: str) -> Trabajo | None:
    return next((t for t in _trabajos.values() if t.slug == c.slug and t.clave == clave and not t.hecho), None)


async def trabajo_largo(c: Cuenta, clave: str, titulo: str, nota: str, destino: str, correr,
                        menu: tuple[str, str] = ("contenido", ""), boton: str = "Ver el resultado",
                        segundos: float = 30, pasos: tuple[str, ...] = ()) -> HTMLResponse:
    """Lanza `correr` en segundo plano (o se suma al mismo trabajo si ya corre) y contesta con su página: el
    resultado si terminó en ESPERA_TRABAJO, o la de espera que se recarga sola."""
    ahora = time.time()
    for k in [k for k, t in _trabajos.items() if t.hecho and ahora - t.fin > GUARDAR_TRABAJO]:
        del _trabajos[k]
    if not (t := en_curso(c, clave)):
        t = Trabajo(c, clave, titulo, nota, destino, menu, boton, correr, segundos, pasos)
        _trabajos[t.id] = t
    await asyncio.wait({t.tarea}, timeout=ESPERA_TRABAJO)
    return await pagina_trabajo(c, t)


async def pagina_trabajo(c: Cuenta, t: Trabajo) -> HTMLResponse:
    if not t.hecho:
        recarga = f'<meta http-equiv="refresh" content="{RECARGA_TRABAJO};url=/panel/trabajo/{t.id}">'
        k, pasado = curva(t), time.time() - t.inicio
        pct = porcentaje(k, pasado)
        # Lo que va haciendo: sus pasos según el avance o, si no tiene, lo último que contó el trabajo.
        paso = paso_de(t.pasos, pct) or (t.avance[-1] if t.avance else "")
        datos = " ".join(f'data-{n}="{v}"' for n, v in k.items())
        cuerpo = (recarga + '<div class="tarjeta" id="generando"><div class="trabajo-cab">'
                  f'<h2>{escape(t.titulo)}</h2><b class="pct-trabajo">{int(pct)}%</b></div>'
                  f'<div class="avance-trabajo" {datos} data-t="{pasado:.2f}"'
                  f' data-pasos="{escape(json.dumps(t.pasos, ensure_ascii=False))}"><i style="width:{pct:.1f}%"></i></div>'
                  f'<p class="paso-trabajo">{escape(paso)}</p>'
                  f'<p class="ayuda">{escape(t.nota)} Puedes cerrar esta página: seguimos trabajando y lo verás al'
                  " volver.</p></div>")
    elif t.error:
        cuerpo = (f'<div class="aviso">{escape(t.error)}</div><div class="tarjeta"><h2>{escape(t.titulo)}</h2>'
                  '<p class="ayuda">No se terminó.</p>'
                  f'<div class="acciones"><a class="boton" href="{escape(t.destino)}">{escape(t.boton)}</a></div></div>')
    else:
        cuerpo = (f'<meta http-equiv="refresh" content="0;url={escape(t.destino)}">'
                  f'<div class="tarjeta"><h2>{escape(t.titulo)}</h2><p class="ayuda">Listo.</p>'
                  f'<div class="acciones"><a class="boton" href="{escape(t.destino)}">{escape(t.boton)}</a></div></div>')
    return pagina(await menu_de(c, t.menu[0], sub=t.menu[1]) + f'<main><div class="contenedor">{cuerpo}</div></main>')


@router.get("/trabajo/{tid}")
async def ver_trabajo(request: Request, tid: str):
    if not (c := cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if not (t := _trabajos.get(tid)) or t.slug != c.slug:  # ya se olvidó, o igbot se reinició
        return RedirectResponse("/panel/contenido/campanas", status_code=303)
    return await pagina_trabajo(c, t)


async def error_inesperado(request: Request) -> HTMLResponse:
    """La página de un error inesperado (ver errores.py): sin detalles técnicos y con salida."""
    c = cuenta_de_sesion(request)
    menu = await menu_de(c, "ninguna") if c else cabecera()
    volver = request.url.path if request.method == "GET" else (request.headers.get("referer") or "/panel")
    if not volver.startswith(("/panel", config.PUBLIC_URL + "/panel")):
        volver = "/panel"
    respuesta = pagina(
        menu + '<section class="oscuro portada compacta delgada"><div class="contenedor">'
        '<h1>Algo salió mal<span class="punto">.</span></h1>'
        '<p class="bajada" style="margin-left:0">No pudimos terminar esto. Ya nos llegó el aviso para revisarlo.'
        ' Si estabas guardando algo, revisa si quedó antes de intentarlo otra vez.</p>'
        '</div></section><main><div class="contenedor"><div class="tarjeta"><h2>¿Qué quieres hacer?</h2>'
        f'<div class="acciones"><a class="boton" href="{escape(volver)}">Intentar de nuevo</a>'
        '<a class="boton secundario" href="/panel">Ir a mi panel</a></div></div>'
        "</div></main>")
    respuesta.status_code = 500
    return respuesta


# Cuánto dura cada conexión en vivo antes de cerrarse sola (EventSource reconecta en 2 s con su
# Last-Event-ID y no se pierde nada). Corta para que reiniciar igbot no espere a los paneles abiertos.
VIVO_SEGUNDOS = 20
MAX_VIVOS = 40  # conexiones en vivo a la vez, entre todas las cuentas (la instancia es chica)
_vivos = 0


@router.get("/vivo")
async def vivo(request: Request):
    """Conversaciones en vivo (Server-Sent Events): cada vez que un chat de la cuenta cambia, manda la
    lista y el chat abierto ya armados, con los mismos filtros de la página. Solo HTML del servidor."""
    global _vivos
    from . import conversaciones  # conversaciones importa este módulo
    if not (c := cuenta_de_sesion(request)):
        return Response(status_code=204)  # sin sesión: EventSource deja de intentar
    if _vivos >= MAX_VIVOS:
        return Response("retry: 30000\n\n", media_type="text/event-stream")
    q = request.query_params
    hoja = int(q["p"]) if q.get("p", "").isdigit() else 1
    n = int(q["n"]) if q.get("n", "").isdigit() else 0
    visto = request.headers.get("last-event-id") or q.get("v", "")
    visto = int(visto) if visto.isdigit() else -1

    async def flujo():
        global _vivos
        _vivos += 1
        nonlocal visto
        try:
            yield "retry: 2000\n\n"
            fin = time.monotonic() + VIVO_SEGUNDOS
            while (resto := fin - time.monotonic()) > 0:
                await c.store.esperar_cambio(visto, resto)
                if c.store.cambios == visto:
                    break
                await asyncio.sleep(0.3)  # un DM y la respuesta del bot seguidos van juntos
                visto = c.store.cambios
                p = await conversaciones.partes(c, q.get("ver", ""), q.get("q", ""), hoja, q.get("chat", "")[:64], n)
                yield f"id: {visto}\ndata: {json.dumps({'lista': p['lista'], 'hilo': p['hilo'], 'punto': p['pendientes'] > 0})}\n\n"
        finally:
            _vivos -= 1
    return StreamingResponse(flujo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})




@router.get("/static/menu.js")
def script_menu():
    return FileResponse(STATIC / "menu.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/static/panel.css")
def hoja():
    return Response(HOJA, media_type="text/css", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/static/panel.js")
def script():
    return FileResponse(STATIC / "panel.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/static/fuentes/{archivo}")
def fuente(archivo: str):
    if not re.fullmatch(rf"({'|'.join(FUENTES.values())})-(Regular|SemiBold|Bold)\.ttf", archivo):
        raise HTTPException(status_code=404)
    return FileResponse(FONTS / archivo, media_type="font/ttf", headers={"Cache-Control": "public, max-age=86400"})


ARCHIVO_TARJETA = re.compile(r"^[a-z0-9-]+\.(webp|png|jpg)$")


@router.get("/static/tarjetas/{seccion}/{archivo}")
def tarjeta(seccion: str, archivo: str):
    """Las imágenes de las tarjetas de opción (Tu marca y Crear campaña)."""
    path = STATIC / "tarjetas" / seccion / archivo
    if seccion not in ("marca", "campana") or not ARCHIVO_TARJETA.match(archivo) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@router.post("/salir")
async def salir(request: Request):
    de_aqui(request)
    response = RedirectResponse("/panel", status_code=303)
    response.delete_cookie(COOKIE, path="/panel")
    return response


@router.post("/salir-todos")
async def salir_todos(request: Request):
    """Cerrar sesión en todos los dispositivos: cambia la versión de sesión de la cuenta y la cookie de aquí."""
    de_aqui(request)
    if c := cuenta_de_sesion(request):
        await cerrar_sesiones(c)
    response = RedirectResponse("/panel", status_code=303)
    response.delete_cookie(COOKIE, path="/panel")
    return response
