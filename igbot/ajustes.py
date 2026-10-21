"""Tu asistente > Ajustes y Conexión, en el panel.

Ajustes: cuántas horas se calla el asistente cuando contesta una persona, el
horario en que atiende una persona (y qué decir fuera de él) y por dónde avisar
al dueño cuando alguien pide hablar con una persona. Se guardan en la base de la
cuenta (kv "ajustes") y mandan sobre lo del .env; se aplican al arrancar y al
guardar, sin reiniciar.

Conexión: un semáforo para saber que todo funciona (acceso de Instagram, que
lleguen mensajes, el permiso de estadísticas, Google y el gasto del día).
"""
import json
import logging
import re
import time
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import config, panel
from .cuenta import Cuenta, usar

log = logging.getLogger(__name__)
router = APIRouter(prefix="/panel")

PAUSAS = (4, 12, 24, 48)
DIAS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FUERA = 300
TOKEN_DIAS = 60  # lo que dura el acceso de Instagram desde que se renueva


async def cargar(c: Cuenta) -> None:
    """Aplica a la cuenta los ajustes guardados desde el panel (si los hay)."""
    aplicar(c, json.loads(await c.store.get("ajustes") or "{}"))


def aplicar(c: Cuenta, datos: dict) -> None:
    for k in ("human_pause_hours", "business_days", "business_hours", "owner_email",
              "aviso_dm", "aviso_correo", "fuera_horario"):
        if k in datos:
            setattr(c, k, datos[k])


def del_form(f: dict, dias: list[str], c: Cuenta) -> dict:
    pausa = int(f.get("pausa")) if f.get("pausa", "").isdigit() and int(f["pausa"]) in PAUSAS else c.human_pause_hours
    desde, hasta = f.get("desde", ""), f.get("hasta", "")
    horario = f"{desde}-{hasta}" if HORA.match(desde) and HORA.match(hasta) and desde < hasta else c.business_hours
    correo = f.get("correo", "").strip()
    return {
        "human_pause_hours": float(pausa),
        "business_days": sorted({int(d) for d in dias if d in "1234567" and len(d) == 1}) or c.business_days,
        "business_hours": horario,
        "owner_email": correo if CORREO.match(correo) else c.owner_email,
        "aviso_dm": f.get("aviso_dm") == "1",
        "aviso_correo": f.get("aviso_correo") == "1",
        "fuera_horario": f.get("fuera_texto", "").strip()[:MAX_FUERA] if f.get("fuera") == "fijo" else "",
    }


def prompt_horario(c: Cuenta) -> str:
    """Lo que el asistente sabe del horario de las personas (va al final de sus instrucciones)."""
    from .resultados import dias_de_horario
    fuera = (f'responde exactamente esto: "{c.fuera_horario}"' if c.fuera_horario
             else "dile con amabilidad cuándo le contestan (el siguiente día y hora de atención) y sigue"
                  " ayudando en lo que puedas")
    return (f"<atencion_personal>Una persona del equipo atiende {dias_de_horario(c)} (hora del negocio)."
            f" Si piden hablar con una persona fuera de ese horario, {fuera}.</atencion_personal>")


# --- tarjetas del panel ---

def tarjeta(c: Cuenta) -> str:
    pausas = "".join(
        f'<label><input type="radio" name="pausa" value="{h}"{" checked" if h == c.human_pause_hours else ""}> {h} h</label>'
        for h in PAUSAS)
    dias = "".join(
        f'<label><input type="checkbox" name="dias" value="{i}"{" checked" if i in c.business_days else ""}> {d}</label>'
        for i, d in enumerate(DIAS, 1))
    desde, hasta = (t.strip() for t in c.business_hours.split("-"))
    fijo = bool(c.fuera_horario)
    dm = ('<label class="check"><input type="checkbox" name="aviso_dm" value="1"'
          f'{" checked" if c.aviso_dm else ""}> DM a mi Instagram personal</label>' if c.admin_ig_ids else "")
    return (
        # Va dentro de "Configura tu asistente", después del formulario de instrucciones (es otro form).
        '<details class="tarjeta plegable seccion" id="ajustes" open><summary><span class="titulo-sec">'
        '<span class="num">06</span><h2>Horario y avisos</h2></span></summary>'
        '<form method="post" action="/panel/ajustes#ajustes">'
        "<label>Cuando contestas tú un chat, el asistente se calla por</label>"
        f'<div class="chips">{pausas}</div>'
        '<p class="ayuda">Pasado ese tiempo vuelve a contestar solo. Siempre puedes reactivarlo antes desde'
        " Conversaciones recientes.</p>"
        '<div class="sub"><label>Horario de atención</label><p class="ayuda">Tu asistente contesta las 24 h;'
        " esto es para decir cuándo atiende una persona.</p>"
        f'<div class="chips">{dias}</div>'
        f'<div class="horas"><input type="time" name="desde" value="{escape(desde)}" aria-label="Desde">'
        f' a <input type="time" name="hasta" value="{escape(hasta)}" aria-label="Hasta"></div>'
        '<label style="margin-top:1rem">Fuera de ese horario, si piden hablar con una persona</label>'
        '<div class="opciones">'
        f'<label class="opcion"><input type="radio" name="fuera" value="avisar"{"" if fijo else " checked"}>'
        "<b>Avisar cuándo le contestan</b><span>«Te escribimos mañana a partir de las 9:00». El asistente sigue"
        " resolviendo lo que pueda.</span></label>"
        f'<label class="opcion"><input type="radio" name="fuera" value="fijo"{" checked" if fijo else ""}>'
        "<b>Mensaje fijo</b><span>Escribes tú el mensaje que reciben.</span></label></div>"
        f'<textarea class="si-fijo" name="fuera_texto" rows="2" maxlength="{MAX_FUERA}"'
        ' placeholder="Ej. Gracias por escribir. Te contestamos el lunes a primera hora.">'
        f"{escape(c.fuera_horario)}</textarea></div>"
        '<div class="sub"><label>Cuando alguien pide hablar con una persona, avísame por</label>'
        f"{dm}"
        f'<label class="check"><input type="checkbox" name="aviso_correo" value="1"{" checked" if c.aviso_correo else ""}>'
        f' Correo a <input type="email" name="correo" value="{escape(c.owner_email)}" aria-label="Correo"></label></div>'
        '<div class="acciones"><button class="boton">Guardar ajustes</button></div></form></details>'
    )


_revision: dict[str, tuple[float, bool | None]] = {}  # cuenta -> (cuándo, si tiene el permiso de estadísticas)
REVISION_TTL = 6 * 3600


async def permiso_estadisticas(c: Cuenta) -> bool | None:
    """Si el token deja leer estadísticas (None: no hay posts para probar). Se revisa cada 6 h."""
    guardado = _revision.get(c.slug)
    if guardado and time.monotonic() - guardado[0] < REVISION_TTL:
        return guardado[1]
    ok = None
    try:
        posts = await c.instagram.recent_media(1)
    except Exception:  # sin red o sin Instagram no se sabe; no se marca como falta
        posts = []
    if posts:
        try:
            await c.instagram.media_insights(posts[0]["id"])
            ok = True
        except Exception:
            ok = False
    _revision[c.slug] = (time.monotonic(), ok)
    return ok


async def conexion(c: Cuenta) -> tuple[str, str]:
    """(estado: ok, aviso o mal; la tarjeta). El estado colorea el botón Conexión del panel."""
    from . import limites
    ahora = time.time()
    filas = []

    def fila(estado: str, titulo: str, detalle: str, accion: str = "") -> None:
        filas.append((estado, f'<div class="fila"><span class="semaforo sem-{estado}"></span><div class="datos">'
                              f"<b>{titulo}</b><span>{detalle}</span></div>{accion}</div>"))
    reconectar = '<a class="boton secundario chico" href="/conectar/instagram">Volver a conectar</a>'
    renovado = float(await c.store.get("ig_token_refreshed_at") or 0)
    if renovado:
        dias = int((ahora - renovado) // 86400)
        quedan = TOKEN_DIAS - dias
        fila("ok" if quedan > 7 else "mal", "Instagram conectado",
             f"@{escape(await panel.usuario(c))} · el acceso se renovó hace {dias} día{'s' if dias != 1 else ''},"
             f" vence en {quedan}", "" if quedan > 7 else reconectar)
    else:
        fila("ok", "Instagram conectado", f"@{escape(await panel.usuario(c))}")
    ultimo = await c.store.last_client_message()
    if ultimo:
        fila("ok" if ahora - ultimo < 7 * 86400 else "aviso", "Recibiendo mensajes",
             f"El último llegó {panel.hace(ultimo)}")
    else:
        fila("aviso", "Recibiendo mensajes", "Aún no llega ninguno: escríbele a tu cuenta desde otro Instagram para probar.")
    with usar(c):
        estadisticas = await permiso_estadisticas(c)
    if estadisticas is False:
        fila("aviso", "Permisos de Instagram", "Mensajes, comentarios y publicar: sí · Estadísticas: falta", reconectar)
    else:
        fila("ok", "Permisos de Instagram", "Mensajes, comentarios, publicar" + (" y estadísticas" if estadisticas else ""))
    if not c.slug and c.google_tools and config.GOOGLE_REFRESH_TOKEN:
        fila("ok", "Google (agenda y correo)", f"Conectado como {escape(config.MAIL_FROM)}")
    if not c.slug:
        with usar(c):
            gasto = await limites.spent_today(c.store)
        tope = c.daily_budget_usd
        lleno = tope > 0 and gasto >= tope
        fila("mal" if lleno else "ok", "Límite de gasto",
             f"Hoy vas en US${gasto:.2f}" + (f" de US${tope:.2f}" if tope > 0 else " (sin límite)")
             + (": el asistente no contesta hasta mañana" if lleno else ""))
    peor = "mal" if any(e == "mal" for e, _ in filas) else "aviso" if any(e == "aviso" for e, _ in filas) else "ok"
    titulo = {"ok": "Todo funcionando", "aviso": "Hay algo que revisar", "mal": "Algo no está funcionando"}[peor]
    return peor, ('<div class="tarjeta" id="conexion"><span class="num">Conexión</span>'
                  f"<h2>{titulo}</h2>"
                  '<p class="ayuda">Así sabes que tu asistente está recibiendo y contestando todo.</p>'
                  + "".join(h for _, h in filas) + "</div>")


@router.post("/ajustes")
async def guardar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    form = await request.form()
    f = {k: str(v) for k, v in form.items()}
    datos = del_form(f, [str(d) for d in form.getlist("dias")], c)
    await c.store.set("ajustes", json.dumps(datos, ensure_ascii=False))
    aplicar(c, datos)
    log.info("@%s cambió sus ajustes desde el panel", await panel.usuario(c))
    return await panel.panel_de(c, "Guardado: tu asistente ya trabaja con estos ajustes.", abrir=True)
