"""Inicio del panel (/panel/inicio): lo de hoy de un vistazo.

De arriba hacia abajo: el saludo con los botones de siempre; "Te falta" (los pasos iniciales que aún no hace,
y desaparece cuando los termina); los números de hoy; las conversaciones que te esperan; las próximas
publicaciones (solo con contenido en el plan) y cuánto lleva del plan este mes.

Todo sale de lo que ya está guardado (mensajes, chats, el resumen de Instagram de las 6:00, el calendario y los
cupos): no llama a la IA y a Instagram solo para los @usuarios que aún no conoce, igual que Conversaciones.
"""
import asyncio
import json
import time
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, conversaciones, marco, metricas
from .cuenta import Cuenta, usar

router = APIRouter(prefix="/panel/inicio")

# Cuántos chats y publicaciones se ven; lo demás, en Conversaciones y Calendario.
CHATS = 5
PUBLICACIONES = 3

# Lo que se dice bajo cada paso inicial de "Te falta" (por su dirección) y el texto de su botón.
PASOS = {
    "/panel/empresa": ("Para que tu asistente sepa qué vendes y cómo", "Llenar"),
    "/panel/asistente/configura": ("Su personalidad, cómo escribe y sus reglas", "Configurar"),
    "/panel/probar#ultimo": ("Escríbele como si fueras un cliente", "Probar"),
    "/panel/asistente": ("Para que empiece a contestar tus mensajes", "Encender"),
    "/panel/contenido/marca": ("Tus colores, letras y logo para tus publicaciones", "Definir"),
    "/panel/contenido/campanas": ("Publicaciones listas para revisar y programar", "Crear"),
}


def _dinero(monto: float, moneda: str) -> str:
    return f"${monto:,.0f}" + (f" {moneda}" if moneda else "")


def _tarjeta_numero(titulo: str, numero: str, abajo: str, sube: bool = False, href: str = "") -> str:
    """Una de las tarjetas de "Hoy". `sube`: un punto lima junto al texto de abajo (va mejor que antes)."""
    etiqueta = "a" if href else "section"
    destino = f' href="{href}"' if href else ""
    return (f'<{etiqueta} class="met-c ini-num"{destino}><span class="ini-et">{titulo}</span><b>{numero}</b>'
            f'<small class="{"sube" if sube else ""}">{abajo}</small></{etiqueta}>')


def te_falta(pasos: list[tuple[str, str, bool]]) -> str:
    faltan = [(i, nombre, href) for i, (nombre, href, hecho) in enumerate(pasos, 1) if not hecho]
    if not faltan:
        return ""
    listos = len(pasos) - len(faltan)
    pct = round(100 * listos / len(pasos))
    cajas = "".join(
        f'<a class="ini-paso" href="{href}"><span class="ini-n">{i}</span>'
        f'<span class="ini-txt"><b>{escape(nombre)}</b><small>{PASOS.get(href, ("", ""))[0]}</small></span>'
        f'<span class="ini-ir">{PASOS.get(href, ("", "Ir"))[1]}</span></a>' for i, nombre, href in faltan)
    return ('<section class="met-c ancho ini-falta">'
            + marco.cab_tarjeta("Te falta", boton=f'<span class="ini-gris">{listos} de {len(pasos)} listos</span>')
            + f'<div class="ini-pasos">{cajas}</div>'
            f'<span class="ini-barra"><span style="width:{pct}%"></span></span></section>')


async def hoy(c: Cuenta, inicio_dia: float, pendientes: int) -> str:
    store = c.store
    ayer = inicio_dia - config.DIA
    contesto = await store.all_bot_replies_since(inicio_dia)
    contesto_ayer = await store.all_bot_replies_since(ayer) - contesto
    dif = contesto - contesto_ayer
    tarjetas = [_tarjeta_numero("Mensajes que contestó tu asistente", f"{contesto:,}",
                                f"{'+' if dif > 0 else ''}{dif:,} vs. ayer", dif > 0)]
    tarjetas.append(_tarjeta_numero(
        "Conversaciones que te esperan", str(pendientes),
        "Contéstalas en Conversaciones" if pendientes else "Ninguna por ahora", href="/panel/asistente"))
    # Instagram: el resumen de las 6:00 trae hasta ayer.
    m = await metricas.numeros(c, 1)
    nuevos = m["serie"][-1].get("nuevos") if m["serie"] and m["serie"][-1] else None
    if nuevos is None:
        tarjetas.append(_tarjeta_numero("Seguidores nuevos ayer", "—", "Desde mañana a las 6:00"))
    else:
        total = "" if m["seguidores"] is None else f"{metricas.cifra(m['seguidores'])} en total"
        tarjetas.append(_tarjeta_numero("Seguidores nuevos ayer", f"+{metricas.cifra(nuevos)}", total, nuevos > 0))
    ventas = await store.ventas_desde(inicio_dia)
    if ventas is None:  # sin tienda ni pagos conectados: a cuántas personas atendió
        gente = await store.escribieron_desde(inicio_dia)
        tarjetas.append(_tarjeta_numero("Personas que te escribieron", f"{gente:,}", "Hoy"))
    else:
        n, monto, moneda = ventas
        tarjetas.append(_tarjeta_numero("Ventas desde los chats", _dinero(monto, moneda),
                                        f"{n} {'pedido' if n == 1 else 'pedidos'}", n > 0))
    return '<div class="ini-sec">Hoy</div><div class="met">' + "".join(tarjetas) + "</div>"


async def te_esperan(c: Cuenta, ahora: float) -> str:
    """Los chats del lado "Tú" que esperan tu respuesta: primero los que te pasó el asistente."""
    from .limites import LIMIT_NOTICE
    from .tools import RAZONES
    humano_desde = ahora - c.human_pause_hours * config.HORA - 60
    filas = []
    for filtro in ("nuevos", "sin"):
        pagina, _, _ = await c.store.chats_page(
            ahora, ahora - conversaciones.VENTANA, humano_desde, ahora - conversaciones.SIN_RESPUESTA,
            LIMIT_NOTICE, filtro, "", CHATS, 0)
        filas += [(filtro, f) for f in pagina]
    filas = filas[:CHATS]
    cab = marco.cab_tarjeta("Te esperan", "/panel/asistente", "Ver todas")
    if not filas:
        return (f'<section class="met-c ini-chats">{cab}<p class="ayuda">Nadie te espera. Tu asistente está'
                " atendiendo todas tus conversaciones.</p></section>")
    with usar(c):
        nombres = await asyncio.gather(*(c.instagram.username(f[0]) for _, f in filas))
    pasados = {f[0] for _, f in filas if f[6]}
    razones = ({u: d for u, d, _ in await c.store.events("humano", humano_desde, ahora + 1) if u in pasados}
               if pasados else {})
    zona = ZoneInfo(c.timezone)
    hoy_ = datetime.fromtimestamp(ahora, zona)
    lista = []
    for (filtro, (u, t, rol, texto, _, est, paso)), nombre in zip(filas, nombres):
        nombre = nombre if nombre.startswith("@") or u.startswith("fb:") else "Cliente de Instagram"
        if est == "revisar":
            motivo = "Límite del plan" if texto == LIMIT_NOTICE else "El asistente no respondió"
        elif paso:
            motivo = RAZONES.get(razones.get(u, {}).get("razon"), "Te lo pasó el asistente")
        else:
            motivo = "Sin contestar"
        if rol == "assistant":
            texto = ("Tú: " + texto[len(conversaciones.MANUAL):].strip()) if texto.startswith(conversaciones.MANUAL) \
                else "Asistente: " + texto
        lista.append(
            f'<a class="ini-fila" href="{escape(conversaciones.enlace(filtro, chat=u))}">'
            f'<span class="ini-av">{escape(nombre.lstrip("@")[:1].upper() or "?")}</span>'
            f'<span class="ini-txt"><b>{escape(nombre)}</b><small>{escape(texto[:140]) or "Sin mensajes todavía"}</small></span>'
            f'<span class="ini-der"><small>{conversaciones._hora(t, zona, hoy_) if t else ""}</small>'
            f'<em>{escape(motivo)}</em></span></a>')
    return f'<section class="met-c ini-chats">{cab}{"".join(lista)}</section>'


async def proximas(c: Cuenta, ahora: float) -> str:
    from . import contenido
    cab = marco.cab_tarjeta("Próximas publicaciones", "/panel/contenido", "Calendario")
    posts = (await c.store.posts(("programada",), desde=ahora))[:PUBLICACIONES]
    if not posts:
        return (f'<section class="met-c ini-posts">{cab}<p class="ayuda">No tienes publicaciones programadas.</p>'
                '<div class="acciones"><a class="boton secundario" href="/panel/contenido/campanas/nueva">'
                "Crear campaña</a></div></section>")
    zona = ZoneInfo(c.timezone)
    hoy_ = datetime.fromtimestamp(ahora, zona).date()
    filas = []
    for p in posts:
        pieza = await contenido.pieza(c.store, p["id"])
        media = await c.store.media(p["media_ids"][0]) if p["media_ids"] else None
        mini = (f'<img class="ini-mini" src="{contenido.url_imagen(c, media[0])}" alt="" loading="lazy">' if media
                else '<span class="ini-mini"></span>')
        d = datetime.fromtimestamp(p["publish_at"], zona)
        dias = (d.date() - hoy_).days
        dia = ("Hoy" if dias == 0 else "Mañana" if dias == 1 else
               f"{conversaciones.DIAS[d.weekday()].capitalize()} {d.day}" if dias < 7
               else f"{d.day} {conversaciones.MESES[d.month - 1]}")
        titulo = pieza.get("titulo") or (p["caption"] or "").split("\n")[0]
        medio = "Historia" if "historia" in (pieza.get("medio"), p.get("tipo")) else "Post"
        filas.append(
            f'<a class="ini-fila" href="/panel/contenido/post/{p["id"]}">{mini}'
            f'<span class="ini-txt"><b>{dia} · {d:%H:%M}</b><small>{escape(titulo[:120])}</small></span>'
            f'<span class="ini-der"><em class="gris">{medio}</em></span></a>')
    return f'<section class="met-c ini-posts">{cab}{"".join(filas)}</section>'


def _uso(nombre: str, usado: int, cupo: int) -> str:
    pct = min(100, round(100 * usado / cupo)) if cupo else 0
    return (f'<div class="ini-uso"><div><span>{nombre}</span><b>{usado:,} <small>de {cupo:,}</small></b></div>'
            f'<span class="ini-barra"><span style="width:{pct}%"></span></span></div>')


async def tu_plan(c: Cuenta, hoy_: date, zona: ZoneInfo) -> str:
    from . import imagenes
    inicio_mes = datetime(hoy_.year, hoy_.month, 1, tzinfo=zona).timestamp()
    siguiente = date(hoy_.year + hoy_.month // 12, hoy_.month % 12 + 1, 1)
    usos = []
    if c.mensajes_mes:
        usos.append(_uso("Mensajes", await c.store.all_bot_replies_since(inicio_mes), c.mensajes_mes))
    if c.imagenes_mes > 0:
        if c.posts_mes:
            usos.append(_uso("Posts", await c.store.posts_count(("publicada", "programada"), inicio_mes), c.posts_mes))
        incluidas, _ = await imagenes.usadas(c.store)
        usos.append(_uso("Imágenes", incluidas, c.imagenes_mes))
    if not usos:
        return ""
    plan = "Community Manager" if c.imagenes_mes > 0 else "Asistente virtual"
    renueva = f'<span class="ini-gris">Se renueva el 1 de {conversaciones.MESES_LARGOS[siguiente.month - 1]}</span>'
    return ('<div class="met"><section class="met-c ancho">' + marco.cab_tarjeta(f"Tu plan · {plan}", boton=renueva)
            + f'<div class="ini-usos">{"".join(usos)}</div></section></div>')


async def saludo(c: Cuenta) -> str:
    """"Hola, " y el nombre de su empresa o, si no lo ha puesto, su @usuario."""
    empresa = json.loads(await c.store.get("empresa") or "{}")
    usuario = await marco.usuario(c)
    nombre = (empresa.get("nombre") or "").strip() or (f"@{usuario}" if usuario else "")
    return f"Hola, {escape(nombre)}" if nombre else "Hola"


async def pagina(c: Cuenta):
    ahora = time.time()
    zona = ZoneInfo(c.timezone)
    hoy_ = datetime.fromtimestamp(ahora, zona)
    inicio_dia = datetime(hoy_.year, hoy_.month, hoy_.day, tzinfo=zona).timestamp()
    from .limites import LIMIT_NOTICE
    pendientes = await c.store.pendientes_tuyos(ahora, ahora - c.human_pause_hours * config.HORA - 60,
                                                ahora - conversaciones.SIN_RESPUESTA, LIMIT_NOTICE)
    botones = '<a class="boton secundario chico" href="/panel/probar#ultimo">Probar tu asistente</a>'
    if c.imagenes_mes > 0:
        botones += '<a class="boton chico" href="/panel/contenido/campanas/nueva">Crear campaña</a>'
    cabeza = (f'<div class="titulo-fila ini-cab"><span class="ini-hola">{await saludo(c)}</span>'
              f'<span class="ini-botones">{botones}</span></div>')
    abajo = await te_esperan(c, ahora)
    if c.imagenes_mes > 0:
        abajo = f'<div class="met ini-dos">{abajo}{await proximas(c, ahora)}</div>'
    else:
        abajo = f'<div class="met">{abajo.replace("met-c ini-chats", "met-c ancho ini-chats", 1)}</div>'
    cuerpo = (cabeza + te_falta(await marco.pasos_iniciales(c)) + await hoy(c, inicio_dia, pendientes)
              + abajo + await tu_plan(c, hoy_.date(), zona))
    return marco.pagina(await marco.menu_de(c, "inicio")
                        + f'<main><div class="contenedor ini">{cuerpo}</div></main>')


@router.get("")
async def ver(request: Request):
    if not (c := marco.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    return await pagina(c)
