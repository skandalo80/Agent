"""Conectar con Facebook: la página de Facebook del cliente y, con ella, su Instagram.

Dos caminos, con el mismo login de Facebook:
1. Ya entró con Instagram (lo normal): desde Conexión pulsa "Conectar tu página" y elige su página. Desde
   ahí su asistente contesta Messenger y los comentarios de la página, y sus campañas salen también en ella
   (ver pagina_fb.py). Su Instagram sigue con el login de Instagram.
2. Entra al panel con "Entrar con Facebook" (GET /panel/facebook/entrar): elige su página y, si tiene un
   Instagram profesional enlazado, se conectan los dos de una vez. Si la cuenta es nueva se da de alta con
   IG_VIA=facebook: su Instagram también va con el token de la página (ver instagram.py).

El token de usuario se cambia por uno de 60 días y con él se piden sus páginas; el token de cada página
que sale así no vence. Sin FB_APP_ID en .env no sale nada de esto.
"""
import contextvars
import logging
import secrets
import time
from html import escape
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import conectar, config, marco, pagina_fb
from .cuenta import Cuenta, usar
from .pagina_fb import FB_GRAPH

log = logging.getLogger("igbot.facebook")

router = APIRouter(prefix="/panel/facebook")

# Messenger, comentarios y publicar en la página, más lo mismo de su Instagram enlazado (los permisos de
# Instagram con el login de Facebook se llaman distinto que con el de Instagram).
# business_management: sin él no salen las páginas que están dentro de un portafolio comercial.
# FB_SCOPES en .env los reemplaza, por si Meta le cambia el nombre a alguno.
SCOPES = config.FB_SCOPES or ",".join([
    "pages_show_list", "business_management", "pages_manage_metadata", "pages_messaging", "pages_read_engagement",
    "pages_manage_engagement", "pages_read_user_content", "pages_manage_posts",
    "instagram_basic", "instagram_manage_messages", "instagram_manage_comments",
    "instagram_content_publish", "instagram_manage_insights",
])
STATE_COOKIE = "facebook_state"
ELEGIR_COOKIE = "facebook_elegir"
TTL = 900
_por_elegir: dict[str, tuple[float, str, list[dict]]] = {}  # código -> (cuándo, ig de la sesión, páginas)


def enabled() -> bool:
    return bool(config.FB_APP_ID and config.FB_APP_SECRET and config.PUBLIC_URL)


def redirect_uri() -> str:
    # Debe estar en "URI de redireccionamiento de OAuth válidos" del login de Facebook en el panel de Meta.
    return f"{config.PUBLIC_URL}/panel/facebook/callback"


def _dialogo(solo_ig: bool = False) -> RedirectResponse:
    state = conectar.nuevo_state()
    config_id = config.FB_CONFIG_ID_IG if solo_ig and config.FB_CONFIG_ID_IG else config.FB_CONFIG_ID
    url = f"https://www.facebook.com/{config.API_VERSION}/dialog/oauth?" + urlencode({
        "client_id": config.FB_APP_ID, "redirect_uri": redirect_uri(), "state": state,
        "response_type": "code",
        # Login de Facebook para empresas: la "configuración" de Meta dice qué permisos pide; sin ella, la lista.
        **({"config_id": config_id} if config_id else {"scope": SCOPES})})
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, state, max_age=TTL, path="/panel/facebook", secure=True, httponly=True,
                 samesite="lax")
    return r


@router.get("/entrar")
def entrar(solo: str = ""):
    """Desde la página de entrar: iniciar sesión con Facebook. ?solo=ig: la configuración "Solo Instagram"."""
    return _dialogo(solo == "ig") if enabled() else RedirectResponse("/panel", status_code=303)


@router.post("/conectar")
async def conectar_desde_panel(c: marco.Formulario):
    """Desde Conexión, ya con sesión: conectar su página."""
    return _dialogo() if enabled() else RedirectResponse("/panel/facebook", status_code=303)


def _check(r: httpx.Response) -> None:
    if r.is_error:
        log.error("Facebook respondió %s en %s: %s", r.status_code, r.url.path, r.text[:500])
    r.raise_for_status()


async def paginas_del_codigo(code: str) -> list[dict]:
    """Código de un solo uso -> token de usuario -> token de 60 días -> sus páginas, cada una con su token
    y su Instagram enlazado (si tiene)."""
    base = f"{FB_GRAPH}/{config.API_VERSION}"
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{base}/oauth/access_token", params={
            "client_id": config.FB_APP_ID, "client_secret": config.FB_APP_SECRET,
            "redirect_uri": redirect_uri(), "code": code})
        _check(r)
        token = r.json()["access_token"]
        # Con la configuración de "System-user access token" (la que deja elegir portafolio y cuenta de
        # Instagram, como Manychat) el token ya es de larga duración y no se cambia: si falla, se usa tal cual.
        r = await http.get(f"{base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": config.FB_APP_ID,
            "client_secret": config.FB_APP_SECRET, "fb_exchange_token": token})
        if r.is_error:
            log.info("Token de Facebook sin cambiar a 60 días (%s): se usa el que llegó", r.status_code)
        else:
            token = r.json().get("access_token", token)
        _token_actual.set(token)
        r = await http.get(f"{base}/me/accounts", params={
            "access_token": token, "limit": 100,
            "fields": "id,name,access_token,instagram_business_account{id,username,followers_count,media_count}"})
        _check(r)
        return [p for p in r.json().get("data", []) if p.get("id") and p.get("access_token")]


# El token del login en curso (lo deja paginas_del_codigo en el contexto de esa misma petición).
_token_actual: contextvars.ContextVar[str] = contextvars.ContextVar("token_facebook", default="")


async def instagram_sin_pagina() -> dict:
    """Con "Solo Instagram" (token de usuario del sistema de su portafolio, sin página): el Instagram que eligió
    en Meta, con su token. Vacío si no hay."""
    if not (token := _token_actual.get()):
        return {}
    base = f"{FB_GRAPH}/{config.API_VERSION}"
    async with httpx.AsyncClient(timeout=15) as http:
        try:
            r = await http.get(f"{base}/debug_token", params={
                "input_token": token, "access_token": f"{config.FB_APP_ID}|{config.FB_APP_SECRET}"})
            datos = (r.json().get("data") or {}) if r.status_code == 200 else {}
            if datos.get("type") != "SYSTEM_USER" or not (uid := str(datos.get("user_id") or "")):
                return {}
            r = await http.get(f"{base}/{uid}/assigned_instagram_accounts", params={"access_token": token})
            _check(r)
            ids = [str(x["id"]) for x in r.json().get("data", []) if x.get("id")]
            if not ids:
                return {}
            if len(ids) > 1:
                log.warning("Eligió %d Instagram en Meta; se conecta el primero (%s)", len(ids), ids[0])
            r = await http.get(f"{base}/{ids[0]}", params={
                "fields": "id,username,followers_count,media_count", "access_token": token})
            if r.is_error:
                log.warning("No se pudo leer el Instagram %s: %s", ids[0], r.text[:300])
            ig = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            log.exception("No se pudo leer el Instagram de Meta sin página")
            return {}
    return {**ig, "id": ids[0], "token": token}


async def diagnostico_sin_paginas() -> None:
    """Sin páginas: deja en el registro qué Instagram ve Meta en sus portafolios (prueba de "Solo Instagram").
    Leer con: sudo journalctl -u igbot --since "10 min ago" | grep "META SIN PAGINA"."""
    if not (token := _token_actual.get()):
        return
    base = f"{FB_GRAPH}/{config.API_VERSION}"
    consultas = {
        "permisos": ("/me/permissions", {}),
        "portafolios": ("/me/businesses", {"fields": "id,name,owned_instagram_accounts{id,username},"
                                                     "instagram_business_accounts{id,username}"}),
        "me_instagram": ("/me", {"fields": "id,name,accounts{id,name}"}),
        "debug_token": ("/debug_token", {"input_token": token}),
        # Con token de usuario del sistema (configuración "Solo Instagram") los activos se ven por aquí.
        "me_negocio": ("/me", {"fields": "id,name,client_business_id"}),
        "me_cuentas": ("/me/accounts", {"fields": "id,name,instagram_business_account{id,username}"}),
        "me_ig_cuentas": ("/me/instagram_accounts", {"fields": "id,username"}),
        "me_activos": ("/me/assigned_business_assets", {}),
    }
    negocio = uid = ""
    async with httpx.AsyncClient(timeout=15) as http:
        for nombre, (ruta, params) in consultas.items():
            try:
                quien = f"{config.FB_APP_ID}|{config.FB_APP_SECRET}" if nombre == "debug_token" else token
                r = await http.get(base + ruta, params={**params, "access_token": quien})
                log.warning("META SIN PAGINA %s %s: %s", nombre, r.status_code, r.text[:1500])
                if nombre == "me_negocio" and r.status_code == 200:
                    negocio = str(r.json().get("client_business_id") or "")
                if nombre == "debug_token" and r.status_code == 200:
                    uid = str((r.json().get("data") or {}).get("user_id") or "")
            except (httpx.HTTPError, ValueError) as e:
                log.warning("META SIN PAGINA %s: %s", nombre, e)
        rutas = [f"/{uid}/assigned_pages", f"/{uid}/assigned_business_asset_groups",
                 f"/{uid}/assigned_instagram_accounts", "/me/instagram_business_accounts",
                 f"/{negocio}?fields=id,name", f"/{negocio}/owned_instagram_assets",
                 f"/{negocio}/client_instagram_assets?fields=id,username,instagram_account",
                 f"/{negocio}/owned_pages", f"/{negocio}/client_pages",
                 f"/{negocio}/system_user_access_tokens?fetch_only=true"] if uid and negocio else []
        log.warning("META SIN PAGINA barrido usuario=%s negocio=%s rutas=%d", uid, negocio, len(rutas))
        for ruta in rutas:
            ruta, _, q = ruta.partition("?")
            params = dict(x.split("=", 1) for x in q.split("&") if x)
            try:
                r = await http.get(base + ruta, params={**params, "access_token": token,
                                                        **({"system_user_id": uid} if "system_user_access" in ruta else {})})
                texto = r.text if "system_user_access" not in ruta else ("con datos" if '"access_token"' in r.text else r.text)
                log.warning("META SIN PAGINA %s %s: %s", ruta, r.status_code, texto[:1500])
            except httpx.HTTPError as e:
                log.warning("META SIN PAGINA %s: %s", ruta, e)


def _ig(p: dict) -> dict:
    return p.get("instagram_business_account") or {}


def _volver(aviso: str) -> RedirectResponse:
    r = RedirectResponse(f"/panel/facebook?aviso={aviso}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/panel/facebook")
    return r


def _sin_sesion(titulo: str, texto: str) -> HTMLResponse:
    return marco.pagina(
        marco.cabecera() + '<section class="oscuro entrar"><div class="contenedor centro">'
        f'<h1>{escape(titulo)}<span class="punto">.</span></h1><p class="bajada">{texto}</p>'
        '<div class="acciones"><a class="boton" href="/conectar/instagram">Entrar con Instagram</a>'
        '<a class="boton secundario" href="/panel/facebook/entrar">Probar de nuevo con Facebook</a></div>'
        "</div></section>")


@router.get("/callback")
async def callback(request: Request):
    p = request.query_params
    sesion = marco.sesion(request) or ""
    if p.get("error") or not p.get("code"):
        log.info("Login de Facebook cancelado: %s", p.get("error_reason") or p.get("error"))
        return _volver("cancelada") if sesion else RedirectResponse("/panel", status_code=303)
    if not (enabled() and conectar.state_valido(p.get("state", ""), request.cookies.get(STATE_COOKIE))):
        log.warning("Vuelta de Facebook con state inválido")
        return _volver("fallo") if sesion else RedirectResponse("/panel", status_code=303)
    try:
        paginas = await paginas_del_codigo(p["code"].removesuffix("#_"))
    except Exception:
        log.exception("No se pudo cambiar el código de Facebook por sus páginas")
        return _volver("fallo") if sesion else _sin_sesion(
            "No pudimos conectar", "Facebook no nos dejó entrar esta vez. Intenta de nuevo en un momento.")
    if not paginas:
        if ig := await instagram_sin_pagina():
            r = await terminar_sin_pagina(ig, sesion)
            r.delete_cookie(STATE_COOKIE, path="/panel/facebook")
            return r
        await diagnostico_sin_paginas()
        return _volver("sin_paginas") if sesion else _sin_sesion(
            "No encontramos tu página", "Tu Facebook no tiene páginas, o no nos diste permiso de usarlas."
            " Crea una página para tu negocio o entra con tu Instagram.")
    # Con sesión, la que tiene su mismo Instagram; si no, si solo tiene una, esa.
    elegida = next((x for x in paginas if sesion and _ig(x).get("id") == sesion), None)
    if not elegida and len(paginas) == 1:
        elegida = paginas[0]
    if elegida:
        r = await terminar(elegida, sesion)
    else:
        codigo = secrets.token_urlsafe(18)
        ahora = time.time()
        for k in [k for k, (t, _, _) in _por_elegir.items() if ahora - t > TTL]:
            del _por_elegir[k]
        _por_elegir[codigo] = (ahora, sesion, paginas)
        r = elegir_pagina(paginas, codigo)
        r.set_cookie(ELEGIR_COOKIE, codigo, max_age=TTL, path="/panel/facebook", secure=True, httponly=True,
                     samesite="lax")
    r.delete_cookie(STATE_COOKIE, path="/panel/facebook")
    return r


def elegir_pagina(paginas: list[dict], codigo: str):
    filas = "".join(
        '<form method="post" action="/panel/facebook/elegir" class="fila">'
        f'<input type="hidden" name="pagina" value="{escape(x["id"])}">'
        f'<div class="datos"><b>{escape(x.get("name", ""))}</b>'
        f'<span>{"Instagram enlazado: @" + escape(_ig(x)["username"]) if _ig(x).get("username") else "Sin Instagram enlazado"}</span>'
        '</div><button class="boton secundario chico">Elegir</button></form>' for x in paginas)
    return marco.pagina(
        marco.cabecera() + '<main><div class="contenedor"><div class="tarjeta"><h2>¿Qué página conectamos?</h2>'
        '<p class="ayuda">Tu asistente va a contestar los mensajes y comentarios de esa página, y tus campañas'
        " se van a publicar también ahí.</p>" + filas + "</div></div></main>")


@router.post("/elegir")
async def elegir(request: Request):
    marco.de_aqui(request)
    codigo = request.cookies.get(ELEGIR_COOKIE, "")
    t, sesion, paginas = _por_elegir.pop(codigo, (0, "", []))
    pagina_id = (await marco.form(request)).get("pagina", "")
    elegida = next((x for x in paginas if x["id"] == pagina_id), None)
    if not elegida or time.time() - t > TTL:
        return RedirectResponse("/panel", status_code=303)
    r = await terminar(elegida, sesion)
    r.delete_cookie(ELEGIR_COOKIE, path="/panel/facebook")
    return r


async def adjuntar(c: Cuenta, p: dict) -> None:
    """Guarda la página en la cuenta y empieza a escuchar sus avisos."""
    await pagina_fb.guardar(c.store, p["id"], p.get("name", ""), p["access_token"])
    if c.ig_via == "facebook":  # su Instagram va con el token de la página: el nuevo reemplaza al anterior
        await c.store.set("ig_token", p["access_token"])
    if marco.conectar_pagina:
        with usar(c):
            await marco.conectar_pagina(c)
    log.info("@%s conectó su página de Facebook %s (%s)", await marco.usuario(c), p.get("name"), p["id"])


async def terminar(p: dict, sesion: str):
    ig = _ig(p)
    if sesion:  # ya tiene cuenta: solo se suma la página
        if not (c := marco.buscar(sesion)):
            return RedirectResponse("/panel", status_code=303)
        if c.ig_via == "facebook" and ig.get("id") != c.ig_account_id:
            return _volver("otra_pagina")  # su Instagram depende de su página: tiene que ser la enlazada
        await adjuntar(c, p)
        return _volver("conectada")
    if not ig.get("id"):
        return _sin_sesion(
            "Tu página no tiene Instagram", f"La página <b>{escape(p.get('name', ''))}</b> no tiene una cuenta"
            " de Instagram profesional enlazada. Enlázala desde la configuración de tu página o entra con tu"
            " Instagram; después puedes conectar tu página desde tu panel.")
    ig_id, usuario = str(ig["id"]), ig.get("username", "")
    if not (c := marco.buscar(ig_id)):
        nueva = await conectar.store.save_conexion(ig_id, usuario, p["access_token"], time.time() + 3650 * config.DIA)
        grande = (int(ig.get("followers_count") or 0) > config.CUENTA_GRANDE_SEGUIDORES
                  or int(ig.get("media_count") or 0) > config.CUENTA_GRANDE_POSTS)
        try:
            c = await marco.alta(ig_id, usuario, p["access_token"], grande, via="facebook") if marco.alta else None
        except Exception:
            log.exception("No se pudo dar de alta a @%s desde Facebook", usuario)
            c = None
        if not c:
            return _sin_sesion("No pudimos conectar", "Algo falló al crear tu cuenta. Escríbenos y lo vemos.")
        if nueva:
            await conectar.avisar(
                f"Instagram conectado por Facebook: @{usuario}",
                f"@{usuario} entró con Facebook (página {p.get('name', '')}). Su asistente quedó en pausa hasta"
                " que lo encienda desde su panel." + (" Es una cuenta grande: quedó en revisión." if grande else ""))
    await adjuntar(c, p)
    r = RedirectResponse(f"{config.PUBLIC_URL}/panel", status_code=303)
    marco.iniciar_sesion(r, ig_id)
    from . import acceso
    acceso.ultimo(r, "facebook")
    return r


async def terminar_sin_pagina(ig: dict, sesion: str):
    """Conectar con Meta sin página: crea su cuenta (o entra a la que ya tiene) con el token del portafolio."""
    ig_id, usuario, token = str(ig["id"]), ig.get("username", ""), ig["token"]
    if sesion and sesion != ig_id:
        return _volver("otro_instagram")
    c = marco.buscar(ig_id)
    if not c or c.ig_via == "sistema":  # nueva, o ya conectada así: el token nuevo reemplaza al anterior
        nueva = await conectar.store.save_conexion(ig_id, usuario, token, time.time() + 3650 * config.DIA)
        grande = (int(ig.get("followers_count") or 0) > config.CUENTA_GRANDE_SEGUIDORES
                  or int(ig.get("media_count") or 0) > config.CUENTA_GRANDE_POSTS)
        try:
            c = await marco.alta(ig_id, usuario, token, grande, via="sistema") if marco.alta else None
        except Exception:
            log.exception("No se pudo dar de alta a @%s con Meta sin página", usuario)
            c = None
        if not c:
            return _sin_sesion("No pudimos conectar", "Algo falló al crear tu cuenta. Escríbenos y lo vemos.")
        if nueva:
            await conectar.avisar(
                f"Instagram conectado con Meta: @{usuario}",
                f"@{usuario} entró con Conectar con Meta, sin página de Facebook. Su asistente quedó en pausa hasta"
                " que lo encienda desde su panel." + (" Es una cuenta grande: quedó en revisión." if grande else ""))
    log.info("@%s entró con Meta sin página (%s)", usuario, ig_id)
    r = RedirectResponse(f"{config.PUBLIC_URL}/panel", status_code=303)
    marco.iniciar_sesion(r, ig_id)
    from . import acceso
    acceso.ultimo(r, "facebook")
    return r


AVISOS = {
    "otro_instagram": "Elegiste en Meta otro Instagram que el de esta cuenta. Sal y entra con ese, o elige este.",
    "conectada": "Listo: conectamos tu página. Tu asistente ya contesta Messenger y los comentarios de tu página.",
    "cancelada": "No se conectó tu página. Puedes intentarlo de nuevo cuando quieras.",
    "fallo": "No pudimos conectar tu página esta vez. Intenta de nuevo en un momento.",
    "sin_paginas": "Tu Facebook no tiene páginas, o no nos diste permiso de usarlas.",
    "otra_pagina": "Tu Instagram está conectado por otra página: elige la página enlazada a tu Instagram.",
    "desconectada": "Desconectamos tu página de Facebook.",
}


@router.get("")
async def ver(request: Request, c: marco.Sesion):
    return await pagina(c, request.query_params.get("aviso", ""))


async def pagina(c: Cuenta, aviso: str = ""):
    store = c.store
    if not enabled():
        cuerpo = ('<div class="tarjeta"><h2>Tu página de Facebook</h2><p class="ayuda">Muy pronto vas a poder'
                  " conectar tu página desde aquí.</p></div>")
    elif await pagina_fb.conectada(store):
        publicar = await store.get("fb_publicar") != "0"
        cuerpo = ('<div class="tarjeta"><h2>Tu página de Facebook está conectada</h2>'
                  f'<p class="ayuda"><b>{escape(await store.get("fb_page_name") or "")}</b></p>'
                  '<ul class="lista-shopify"><li>Tu asistente contesta los mensajes de Messenger de tu página, con'
                  " lo mismo que configuraste para Instagram.</li><li>Los comentarios de tu página siguen las mismas"
                  " palabras clave que tus posts de Instagram.</li>"
                  f"<li>Tus campañas {'se publican' if publicar else 'no se publican'} también en tu página.</li></ul>"
                  '<form method="post" action="/panel/facebook/publicar" class="acciones">'
                  f'<input type="hidden" name="publicar" value="{"0" if publicar else "1"}">'
                  f'<button class="boton secundario chico">{"No publicar mis campañas en Facebook" if publicar else "Publicar mis campañas también en Facebook"}</button></form>'
                  + ('<p class="ayuda">Tu Instagram está conectado a través de esta página.</p>' if c.ig_via == "facebook"
                     else '<form method="post" action="/panel/facebook/desconectar" class="acciones">'
                          '<button class="boton secundario chico">Desconectar tu página</button></form>')
                  + "</div>")
    else:
        cuerpo = ('<div class="tarjeta"><h2>Conecta tu página de Facebook</h2>'
                  '<p class="ayuda">Tu asistente contesta también los mensajes de Messenger y los comentarios de tu'
                  " página, y tus campañas se publican en Instagram y en Facebook a la vez.</p>"
                  '<form method="post" action="/panel/facebook/conectar">'
                  '<div class="acciones"><button class="boton">Conectar tu página</button></div>'
                  '<p class="ayuda">Te llevamos a Facebook para que elijas tu página y des el permiso; al aceptar'
                  " vuelves aquí.</p></form></div>")
    aviso_html = f'<div class="aviso">{escape(AVISOS[aviso])}</div>' if aviso in AVISOS else ""
    return marco.pagina(
        await marco.menu_de(c, "cuenta", sub="conexion")
        + f'<main class="contenido">{marco.encabezado("Tu página de Facebook")}{aviso_html}{cuerpo}</main>')


@router.post("/publicar")
async def cambiar_publicar(request: Request, c: marco.Formulario):
    await c.store.set("fb_publicar", "0" if (await marco.form(request)).get("publicar") == "0" else "1")
    return RedirectResponse("/panel/facebook", status_code=303)


@router.post("/desconectar")
async def desconectar(c: marco.Formulario):
    if c.ig_via != "facebook":  # si su Instagram va por la página, quitarla lo apagaría todo
        await pagina_fb.quitar(c.store)
        c.fb_page_id = ""
    return RedirectResponse("/panel/facebook?aviso=desconectada", status_code=303)
