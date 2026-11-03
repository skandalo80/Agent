"""Comentarios ocultos: lo que el asistente ocultó por spam o por insultos, y el botón para volver a mostrarlo.

- La página /panel/interacciones/ocultos lista los ocultos, lo más reciente primero y por páginas. Del texto de
  quien comentó se ve solo el principio (RECORTE): es de un tercero y basta para reconocerlo.
- "Mostrar" lo vuelve visible en Instagram (o en la página de Facebook, si vino de ahí) y lo saca de la lista.
  No se le contesta después: solo deja de estar oculto.
- Un correo al día al dueño (owner_email), desde las HORA, solo si el día anterior se ocultó algo y si no lo
  apagó en Configura tus interacciones. Uno por día como mucho (unavez), aunque el proceso se reinicie.

Solo con contenido en el plan (Community Manager), igual que Configura tus interacciones.
"""
import logging
from datetime import timedelta
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import asistente, config, fechas, interacciones, marco, unavez
from .comentarios import OCULTOS
from .cuenta import Cuenta
from .pagina_fb import PREFIJO as DE_FACEBOOK

log = logging.getLogger("igbot.ocultos")
router = APIRouter(prefix="/panel")
BASE = "/panel/interacciones/ocultos"

POR_PAGINA = 20
RECORTE = 140  # caracteres del comentario que se ven en la lista
HORA = 9  # desde esta hora (del negocio) sale el correo con lo del día anterior
MOTIVOS = {"spam": "Spam", "ofensa": "Insulto o ataque"}
MARCAS = list(OCULTOS)


def recorte(texto: str) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= RECORTE else texto[:RECORTE].rstrip() + "…"


# --- la página ---

def _fila(comment_id: str, user_id: str, texto: str, matched: str, creado: float, hoja: int) -> str:
    donde = "Facebook" if user_id.startswith(DE_FACEBOOK) else "Instagram"
    return ('<li class="oc-fila"><div class="oc-texto">'
            f'<p>{escape(recorte(texto))}</p><small>{MOTIVOS[OCULTOS[matched]]} · {donde} · {fechas.hace(creado)}</small>'
            f'</div><form method="post" action="{BASE}"><input type="hidden" name="id" value="{escape(comment_id)}">'
            f'<input type="hidden" name="hoja" value="{hoja}"><button class="boton secundario">Mostrar</button></form></li>')


async def pagina(c: Cuenta, hoja: int = 1, aviso: str = "") -> HTMLResponse:
    total = sum((await c.store.cuantos_ocultos(MARCAS)).values())
    paginas = max(1, -(-total // POR_PAGINA))
    hoja = min(max(1, hoja), paginas)
    filas = await c.store.ocultos(MARCAS, POR_PAGINA, (hoja - 1) * POR_PAGINA)
    lista = ("".join(_fila(i, u, t, m, creado, hoja) for i, u, _, t, m, creado in filas) if filas else
             '<li class="oc-vacio">No hay comentarios ocultos.</li>')
    cuerpo = ('<div class="as-caja"><h3 class="as-pases-t">Comentarios ocultos</h3>'
              '<p class="ayuda">Los que ocultamos por spam o por insultos. Siguen en tu post, pero solo los ven quien'
              ' los escribió y sus amigos. Si alguno no debía ocultarse, tócale Mostrar.</p>'
              f'<ul class="oc-lista">{lista}</ul>{marco.paginas(hoja, paginas, lambda n: f"{BASE}?hoja={n}")}'
              '<p class="it-clave"><a href="/panel/interacciones?paso=comentarios">Cambiar qué se oculta</a></p></div>')
    return marco.pagina(
        f"<style>{asistente.ESTILO}{interacciones.ESTILO}{ESTILO}</style>"
        + await marco.menu_de(c, "contenido", sub="interacciones")
        + '<main class="as-main"><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + f"{cuerpo}</div></main>")


@router.get("/interacciones/ocultos")
async def ver(request: Request, c: marco.Sesion):
    if not interacciones.disponible(c):
        return RedirectResponse("/panel", status_code=303)
    q = request.query_params
    hoja = int(q["hoja"]) if q.get("hoja", "").isdigit() else 1
    aviso = {"1": "Listo: el comentario ya se ve en tu post.", "error": "No pudimos mostrarlo. Inténtalo más tarde."}
    return await pagina(c, hoja, aviso.get(q.get("ok", ""), ""))


@router.post("/interacciones/ocultos")
async def mostrar(request: Request, c: marco.Formulario):
    if not interacciones.disponible(c):
        return RedirectResponse("/panel", status_code=303)
    f = await request.form()
    comment_id, hoja = str(f.get("id", ""))[:64], str(f.get("hoja", "1"))
    hoja = hoja if hoja.isdigit() else "1"
    fila = await c.store.comentario(comment_id) if comment_id else None
    if not fila or fila[1] not in OCULTOS:  # solo los de esta cuenta que ocultamos nosotros
        return RedirectResponse(f"{BASE}?hoja={hoja}", status_code=303)
    canal = c.instagram.pagina if fila[0].startswith(DE_FACEBOOK) else c.instagram
    try:
        await canal.show_comment(comment_id)
    except Exception:
        log.exception("No se pudo volver a mostrar el comentario %s", comment_id)
        return RedirectResponse(f"{BASE}?hoja={hoja}&ok=error", status_code=303)
    await c.store.comment_result(comment_id, "", False, False)
    log.info("@%s volvió a mostrar el comentario %s", await marco.usuario(c), comment_id)
    return RedirectResponse(f"{BASE}?hoja={hoja}&ok=1", status_code=303)


# --- el correo del día ---

def texto_correo(cuantos: dict[str, int]) -> str:
    partes = [f"{n} {'de spam' if OCULTOS[k] == 'spam' else 'con insultos'}" for k, n in cuantos.items() if n]
    total = sum(cuantos.values())
    enlace = f"{config.PUBLIC_URL}{BASE}" if config.PUBLIC_URL else BASE
    return (f"Ayer ocultamos {marco.plural_(total, 'comentario', 'comentarios')} en tus publicaciones"
            f" ({', '.join(partes)}).\n\nPuedes revisarlos y volver a mostrar cualquiera aquí:\n{enlace}\n\n"
            "Si no quieres este correo, apágalo en Configura tus interacciones > Comentarios.")


async def tick(c: Cuenta, google) -> bool:
    """Lo llama el bucle de fondo de app.py: manda el correo del día anterior una vez. True si lo mandó."""
    if not (google and c.owner_email and interacciones.disponible(c)):
        return False
    ahora = fechas.ahora(c)
    if ahora.hour < HORA:
        return False
    ayer = fechas.hoy(c) - timedelta(days=1)
    desde = fechas.inicio_del_dia(c, ayer)
    hasta = fechas.inicio_del_dia(c, fechas.hoy(c))
    datos = await interacciones.guardado(c)
    if not interacciones.aviso_ocultos(datos):
        return False
    cuantos = await c.store.cuantos_ocultos(MARCAS, desde, hasta)
    if not sum(cuantos.values()) or not await unavez.gastar(c.store, f"aviso_ocultos:{ayer.isoformat()}", 2 * config.DIA):
        return False
    total = sum(cuantos.values())
    try:
        await google.send_email(config.MAIL_FROM, c.mail_from_name, c.owner_email, "",
                                f"Ocultamos {marco.plural_(total, 'comentario', 'comentarios')} ayer",
                                texto_correo(cuantos))
    except Exception:
        log.exception("No se pudo mandar el correo de comentarios ocultos")
        return False
    log.info("Correo de comentarios ocultos enviado (%d)", total)
    return True


ESTILO = """
.oc-lista{list-style:none;margin:.4rem 0 0;padding:0}
.oc-fila{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.8rem 0;border-top:1px solid var(--linea-oscura)}
.oc-texto{min-width:0}
.oc-texto p{margin:0;overflow-wrap:anywhere;color:var(--claro)}
.oc-texto small{display:block;margin-top:.2rem;color:var(--gris);font-size:.82rem}
.oc-vacio{padding:1rem 0;color:var(--gris)}
@media (max-width:640px){.oc-fila{flex-direction:column;align-items:stretch}.oc-fila .boton{width:100%}}
"""
