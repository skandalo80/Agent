"""Tu asistente > Configura, para los clientes: pasos cortos en vez de un formulario largo.

1 Personalidad (6 tarjetas), 2 Cómo escribe (trato, largo y emojis), 3 Reglas y límites
(interruptores y dos cajas), 4 Tu equipo (qué decir si piden una persona, y de ajustes.py la
pausa, el horario en que atiende una persona y los avisos) y un resumen con Editar, desde donde
se prueba (/panel/probar) y se enciende. Lo que sabe del negocio (datos, catálogo, preguntas y políticas) viene del
Perfil de empresa (empresa.py); si aún no lo llena, de lo que guardó antes en Configura.

Cada paso guarda solo lo suyo en kv "perfil" y vuelve a escribir el prompt (panel.generar).
Lo que el dueño escribe a mano pasa por panel.revisar_instrucciones; elegir de una lista no.
Las reglas base (prompts/base.md) siguen al final de todo prompt, como antes.
Sin JavaScript: radios y casillas con CSS (:has) y un POST por paso.
La cuenta principal se configura igual; su prompt va aparte del repo (Cuenta.prompt_destino) y
con "Volver a las del servidor" (POST /panel/guardar) regresa a prompts/system.md.
"""
import json
import logging
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import panel
from .cuenta import Cuenta

log = logging.getLogger("igbot.asistente")
router = APIRouter(prefix="/panel")

# clave: (nombre, rasgos, uso ideal, pregunta y respuesta de ejemplo, cómo habla (va al prompt),
#         (trato, largo, emojis) con que empieza, colores de la tarjeta mientras no tenga ilustración)
PERSONALIDADES = {
    "cercano": ("Cercano y amable", ("Cálido", "De tú", "Conversacional"),
                "Tiendas, restaurantes, belleza y servicios del día a día.",
                ("¿Tienen envío a Monterrey?", ("Claro, enviamos a todo México. A Monterrey llega en 2 o 3 días."
                 " ¿Te ayudo a elegir algo? 🙂")),
                "Cercano y amable: cálido y conversacional, como alguien del equipo que atiende con gusto.",
                ("tu", "equilibrada", "aveces"), ("#7c2d12", "#f97316")),
    "profesional": ("Profesional y confiable", ("Claro", "Preciso", "Sobrio"),
                    "Consultorios, despachos, inmobiliarias y empresas que venden a empresas.",
                    ("¿Qué necesito para una cita?", ("Con gusto te ayudo. Solo necesitamos tu nombre y un horario."
                     " ¿Qué día te acomoda?")),
                    "Profesional y confiable: claro, preciso y sobrio. Transmites seguridad y vas al punto.",
                    ("tu", "equilibrada", "nunca"), ("#1e3a5f", "#3b82f6")),
    "divertido": ("Divertido y desenfadado", ("Ligero", "Creativo", "Emojis"),
                  "Moda, marcas juveniles, comida y entretenimiento.",
                  ("¿Tardan mucho los tenis?", "Nada, salen volando en 24 h 🚀 ¿Ya tienes tu talla?"),
                  ("Divertido y desenfadado: ligero y creativo, con humor suave cuando cabe y sin bromas que"
                  " puedan ofender."),
                  ("tu", "breve", "seguido"), ("#713f12", "#facc15")),
    "empatico": ("Empático y paciente", ("Escucha", "Calma", "Respetuoso"),
                 "Salud, bienestar, clínicas y atención después de la venta.",
                 ("Mi pedido llegó dañado.", ("Lamento mucho lo que pasó. Vamos a resolverlo: ¿me compartes una foto"
                  " del producto?")),
                 ("Empático y paciente: escuchas, reconoces cómo se siente la persona y la acompañas con calma,"
                 " paso a paso."),
                 ("tu", "equilibrada", "nunca"), ("#831843", "#f472b6")),
    "energico": ("Enérgico y motivador", ("Dinámico", "Positivo", "A la acción"),
                 "Gimnasios, coaches, deportes y cursos.",
                 ("¿Tienen clase para principiantes?", ("Sí, y es ideal para arrancar. Hay lugar el lunes a las 7:00."
                  " ¿Te aparto uno? 💪")),
                 ("Enérgico y motivador: dinámico y positivo. Animas a dar el siguiente paso sin presionar ni"
                 " prometer de más."),
                 ("tu", "breve", "aveces"), ("#14532d", "#22c55e")),
    "premium": ("Elegante y premium", ("Sobrio", "Cuidado", "Exclusivo"),
                "Joyería, hoteles, arquitectura, tecnología y marcas de lujo.",
                ("¿Hacen piezas a la medida?", "Sí. Cada pieza se diseña contigo. ¿Te comparto cómo es el proceso?"),
                "Elegante y premium: sobrio y cuidado en cada palabra, con un trato atento y exclusivo, sin exagerar.",
                ("usted", "equilibrada", "nunca"), ("#27272a", "#a1a1aa")),
}
TRATOS = {"tu": ("De tú", "Tuteas a las personas."),
          "usted": ("De usted", "Hablas de usted a las personas.")}
LARGOS = {"breve": ("Muy breves", "Contestas en una o dos frases."),
          "equilibrada": ("Equilibradas", "Contestas en dos o tres frases, claras y al punto."),
          "detalle": ("Con detalle", ("Puedes dar respuestas más completas cuando ayuden, sin pasar de un párrafo"
                                     " corto."))}
EMOJIS = {"nunca": ("Nunca", "No usas emojis."),
          "aveces": ("De vez en cuando", "Puedes usar algún emoji de vez en cuando."),
          "seguido": ("Seguido", "Usas emojis con frecuencia, sin exagerar.")}
# El tono de antes (amigable, profesional o formal): sigue funcionando hasta que elija personalidad.
TONO_VIEJO = {"amigable": ("cercano", "tu", "breve", "aveces"),
              "profesional": ("profesional", "tu", "equilibrada", "nunca"),
              "formal": ("profesional", "usted", "equilibrada", "nunca")}
# (clave, título, para qué sirve, encendida al empezar, lo que va al prompt encendida, y apagada)
REGLAS = [
    ("preguntar", "Pregunta antes de recomendar", "Hace una o dos preguntas para entender qué busca la persona.",
     True, "Antes de recomendar algo, haces una o dos preguntas para entender qué busca la persona.", ""),
    ("siguiente", "Invita al siguiente paso", "Sugiere comprar, agendar o visitar cuando la persona muestra interés.",
     True, "Cuando la persona muestra interés, la invitas al siguiente paso: comprar, agendar o visitar.", ""),
    ("links", "Comparte el link del producto", "Si preguntan por algo que tiene link, lo manda.",
     True, "Si preguntan por un producto o servicio que tiene link en tu información, compártelo.", ""),
    ("quejas", "Pasa las quejas a tu equipo", "Reclamos, reembolsos y casos delicados los atiende una persona.",
     True, "Las quejas, reclamos, reembolsos y casos delicados no los resuelves tú: usa pasar_a_humano.", ""),
    ("descuentos", "Habla de descuentos", "Solo los que tengas escritos; nunca inventa promociones.",
     False, "Puedes mencionar los descuentos y promociones que aparecen en tu información, y ninguno más.",
     "No ofreces descuentos ni promociones aunque te los pidan; si insisten, ofrece pasar con una persona."),
]
MAX_TEXTO = 1000  # cada caja de Reglas y límites

# (clave, título, lo que dice debajo en la lista de pasos)
PASOS = [
    ("personalidad", "Personalidad", "Cómo se siente al hablar"),
    ("escribe", "Cómo escribe", "Trato, largo y emojis"),
    ("reglas", "Reglas y límites", "Qué hace y qué evita"),
    ("equipo", "Tu equipo", "Cuándo entra una persona"),
]
ORDEN = [k for k, *_ in PASOS]
ESCRITOS = {"reglas", "equipo"}  # traen texto del dueño: pasan por la revisión


def estilo(perfil: dict) -> dict:
    """Personalidad, trato, largo y emojis, con lo de antes (tono) y los de la personalidad de respaldo."""
    if perfil.get("personalidad") in PERSONALIDADES:
        k = perfil["personalidad"]
        trato, largo, emojis = PERSONALIDADES[k][5]
    else:
        k, trato, largo, emojis = TONO_VIEJO.get(perfil.get("tono"), TONO_VIEJO["amigable"])
    return {"personalidad": k,
            "trato": perfil["trato"] if perfil.get("trato") in TRATOS else trato,
            "largo": perfil["largo"] if perfil.get("largo") in LARGOS else largo,
            "emojis": perfil["emojis"] if perfil.get("emojis") in EMOJIS else emojis}


def reglas_de(perfil: dict) -> dict[str, bool]:
    guardadas = perfil.get("reglas") or {}
    return {k: bool(guardadas.get(k, encendida)) for k, _, _, encendida, *_ in REGLAS}


def tono_prompt(perfil: dict) -> list[str]:
    """Las líneas de TONO y CÓMO ATIENDES del prompt (panel.generar las pone después de la presentación)."""
    e = estilo(perfil)
    out = ["TONO", PERSONALIDADES[e["personalidad"]][4],
           f"- {TRATOS[e['trato']][1]}", f"- {LARGOS[e['largo']][1]}", f"- {EMOJIS[e['emojis']][1]}"]
    activas = reglas_de(perfil)
    reglas = [si if activas[k] else no for k, _, _, _, si, no in REGLAS if (si if activas[k] else no)]
    if reglas:
        out += ["", "CÓMO ATIENDES", *(f"- {r}" for r in reglas)]
    # Lo que escribe el dueño va escapado y entre etiquetas: son datos, no instrucciones nuevas.
    if no_hacer := perfil.get("no_hacer", "").strip():
        out += ["", "LO QUE NO DEBES DECIR NI HACER (además de las reglas base)",
                f"<lo_que_no_debes_hacer>\n{escape(no_hacer)}\n</lo_que_no_debes_hacer>"]
    if pasar := perfil.get("pasar_equipo", "").strip():
        out += ["", "CUÁNDO PASAR LA CONVERSACIÓN AL EQUIPO (usa pasar_a_humano)",
                f"<cuando_pasar_al_equipo>\n{escape(pasar)}\n</cuando_pasar_al_equipo>"]
    return out


# --- páginas ---

def _hechos(perfil: dict) -> set[str]:
    return set(perfil.get("pasos") or [])


def _href(paso: str) -> str:
    if paso in ("probar", "empresa"):
        return "/panel/probar#ultimo" if paso == "probar" else "/panel/empresa"
    return f"/panel/asistente/configura?paso={paso}"


def _lista(actual: str, perfil: dict, hechos: set[str]) -> str:
    e = estilo(perfil)
    detalle = {"personalidad": PERSONALIDADES[e["personalidad"]][0],
               "escribe": f"{TRATOS[e['trato']][0]} · {LARGOS[e['largo']][0].lower()} · emojis"
                          f" {EMOJIS[e['emojis']][0].lower()}",
               "reglas": f"{sum(reglas_de(perfil).values())} de {len(REGLAS)} activas"}
    items = []
    for i, (k, titulo, sub) in enumerate(PASOS):
        hecho = k in hechos and k != actual
        sub = detalle.get(k, sub) if hecho else sub
        items.append(f'<a class="as-paso{" on" if k == actual else ""}{" ok" if hecho else ""}" href="{_href(k)}"'
                     + (' aria-current="step"' if k == actual else "")
                     + f'><span class="as-n">{"✓" if hecho else i + 1}</span><span><b>{titulo}</b>'
                     f"<small>{escape(sub)}</small></span></a>")
    listos = len(hechos & set(ORDEN))
    return ('<nav class="as-pasos" aria-label="Pasos"><p class="as-t">Tus pasos</p>' + "".join(items)
            + f'<a class="as-paso{" on" if actual == "resumen" else ""}" href="{_href("resumen")}">'
            '<span class="as-n">★</span><span><b>Resumen</b><small>Revisa y enciéndelo</small></span></a>'
            f'<div class="as-barra"><i style="width:{listos / len(ORDEN) * 100:.0f}%"></i></div>'
            f'<small class="as-nota">{listos} de {len(ORDEN)} listos. Puedes volver a cualquier paso cuando'
            " quieras.</small></nav>")


def _cabeza(paso: str, titulo: str, bajada: str) -> str:
    if paso in ORDEN:
        antes = f"Paso {ORDEN.index(paso) + 1} de {len(ORDEN)}"
    else:
        antes = "Resumen"
    return f'<p class="as-num">{antes}</p><h2 class="as-h">{titulo}</h2><p class="as-baj">{bajada}</p>'


def _pie(paso: str, siguiente: str = "Siguiente") -> str:
    i = ORDEN.index(paso)
    atras = f'<a class="boton secundario" href="{_href(ORDEN[i - 1])}">Atrás</a>' if i else "<span></span>"
    return (f'<div class="as-pie">{atras}<button class="boton" name="accion" value="siguiente">'
            f"{siguiente}</button></div>")


def _radios(nombre: str, opciones: dict, actual: str, pregunta: str, ayuda: str) -> str:
    botones = "".join(
        f'<label><input type="radio" id="{nombre}-{k}" name="{nombre}" value="{k}"{" checked" if k == actual else ""}>'
        f"{etiqueta}</label>" for k, (etiqueta, _) in opciones.items())
    return (f'<fieldset class="as-caja"><legend>{pregunta}</legend><p class="ayuda">{ayuda}</p>'
            f'<div class="as-seg">{botones}</div></fieldset>')


def _tarjeta(k: str, elegida: bool) -> str:
    nombre, rasgos, uso, (pregunta, respuesta), _, _, (c1, c2) = PERSONALIDADES[k]
    img = panel.STATIC / "personalidad" / f"{k}.png"
    arte = (f'<img src="/panel/static/personalidad/{k}.png?v={int(img.stat().st_mtime)}" alt="">' if img.is_file()
            else f'<span class="as-arte" style="background:linear-gradient(135deg,{c1},{c2})"></span>')
    chips = "".join(f"<span>{r}</span>" for r in rasgos)
    return (f'<label class="as-pers"><input type="radio" name="personalidad" value="{k}"{" checked" if elegida else ""}>'
            f'<span class="as-img">{arte}<span class="as-ck" aria-hidden="true">✓</span></span>'
            f'<span class="as-in"><b class="as-nom">{nombre}</b><span class="as-rasgos">{chips}</span>'
            f'<span class="as-lbl">Uso ideal</span><span class="as-uso">{uso}</span>'
            f'<span class="as-dlg"><span class="u">Cliente: {pregunta}</span>'
            f'<span class="a">Asistente: {respuesta}</span></span></span></label>')


def _personalidad(perfil: dict) -> str:
    actual = perfil.get("personalidad") if perfil.get("personalidad") in PERSONALIDADES else ""
    return (_cabeza("personalidad", "Elige su personalidad",
                    "Elige la que más se parezca a tu marca. En el siguiente paso ajustas los detalles.")
            + '<fieldset class="as-tarjetas"><legend class="oculto">Personalidad</legend>'
            + "".join(_tarjeta(k, k == actual) for k in PERSONALIDADES) + "</fieldset>" + _pie("personalidad"))


def _escribe(perfil: dict) -> str:
    e = estilo(perfil)
    # "Así sonaría" cambia con lo que elijas sin JavaScript: el CSS enseña la parte que toca.
    muestra = ('<div class="as-muestra" aria-live="polite"><b>Así sonaría</b>'
               '<span class="u">Cliente: ¿Tienen disponible este modelo?</span><span class="a">Asistente: Sí, lo'
               ' tenemos disponible<span class="v-detalle"> en tres colores y <span class="v-tu">te</span>'
               '<span class="v-usted">le</span> llega en dos o tres días</span>.<span class="v-pregunta">'
               ' <span class="v-tu">¿Quieres que te comparta el precio?</span><span class="v-usted">¿Desea que le'
               ' comparta el precio?</span></span><span class="v-emoji"> 🙂</span><span class="v-emoji2"> ✨</span>'
               "</span></div>")
    return (_cabeza("escribe", "Cómo escribe",
                    f"Ya viene ajustado a {PERSONALIDADES[e['personalidad']][0].lower()}. Cambia solo lo que quieras.")
            + _radios("trato", TRATOS, e["trato"], "¿Cómo trata a tus clientes?", "Así se dirige a cada persona.")
            + _radios("largo", LARGOS, e["largo"], "¿Qué tan largas son sus respuestas?",
                      "En Instagram funcionan mejor los mensajes cortos.")
            + _radios("emojis", EMOJIS, e["emojis"], "¿Usa emojis?",
                      "Un emoji bien puesto hace la conversación más cálida.")
            + muestra + _pie("escribe"))


def _reglas(perfil: dict) -> str:
    activas = reglas_de(perfil)
    filas = ('<div class="as-fila"><span><b>Nunca inventa datos <em>Siempre activa</em></b>'
             "<small>Si no sabe un precio o un dato, lo dice y ofrece pasar con una persona.</small></span>"
             '<input type="checkbox" class="as-sw" checked disabled aria-label="Nunca inventa datos"></div>')
    filas += "".join(
        f'<label class="as-fila"><span><b>{titulo}</b><small>{ayuda}</small></span>'
        f'<input type="checkbox" class="as-sw" name="r_{k}" value="1"{" checked" if activas[k] else ""}></label>'
        for k, titulo, ayuda, *_ in REGLAS)
    extra = perfil.get("instrucciones", "")
    return (_cabeza("reglas", "Reglas y límites", "Qué puede hacer por su cuenta y qué no.")
            + f'<div class="as-caja">{filas}'
            '<label for="no_hacer">¿Qué no debe decir o hacer?</label>'
            f'<textarea id="no_hacer" name="no_hacer" rows="3" maxlength="{MAX_TEXTO}"'
            ' placeholder="Ej. No prometer fechas de entrega exactas, no hablar de la competencia.">'
            f'{escape(perfil.get("no_hacer", ""))}</textarea>'
            '<label for="pasar_equipo">¿Cuándo debe pasar la conversación a tu equipo?</label>'
            f'<textarea id="pasar_equipo" name="pasar_equipo" rows="3" maxlength="{MAX_TEXTO}"'
            ' placeholder="Ej. Si piden más de 10 piezas o una cotización especial.">'
            f'{escape(perfil.get("pasar_equipo", ""))}</textarea>'
            # Las instrucciones libres de antes no se pierden: aquí siguen, plegadas.
            f'<details class="as-mas"{" open" if extra else ""}><summary>Algo más que deba saber (opcional)</summary>'
            '<label for="instrucciones">Otras indicaciones</label>'
            '<p class="ayuda">Ej.: saluda por su nombre y siempre invita a visitar la tienda.</p>'
            f'<textarea id="instrucciones" name="instrucciones" rows="4" maxlength="{panel.MAX_INSTRUCCIONES}">'
            f"{escape(extra)}</textarea></details></div>" + _pie("reglas"))


def _equipo(c: Cuenta, perfil: dict) -> str:
    from . import ajustes  # ajustes importa panel
    _, _, ayuda, filas = panel.HUMANO
    return (_cabeza("equipo", "Tu equipo", "Cuándo entra una persona y cómo te avisamos.")
            + '<div class="as-caja">' + panel.caja("humano", "Cuando alguien pide hablar con una persona, ¿qué le dice?",
                                                   ayuda, perfil.get("humano", ""), filas)
            + "</div>" + f'<div class="as-caja">{ajustes.campos(c)}</div>'
            + _pie("equipo", "Siguiente: ver resumen"))


def _servidor(c: Cuenta) -> str:
    """La principal: si usa lo de este panel o las instrucciones del vendedor del repo (prompts/system.md)."""
    if c.slug:
        return ""
    if c.prompt_editado().is_file():
        return ('<form method="post" action="/panel/guardar" class="as-caja as-srv"><span>Tu asistente usa lo que'
                " configuraste aquí, no las instrucciones del servidor (prompts/system.md).</span>"
                '<button class="boton secundario" name="accion" value="restaurar">Volver a las del servidor'
                "</button></form>")
    return ('<p class="as-caja as-srv">Ahora usa las instrucciones del servidor (prompts/system.md). En cuanto'
            " guardes un paso, usa lo que configures aquí.</p>")


async def _resumen(c: Cuenta, perfil: dict) -> str:
    from .resultados import dias_de_horario
    e = estilo(perfil)
    activas = reglas_de(perfil)

    def caja(titulo: str, paso: str, lineas: list[str]) -> str:
        lis = "".join(f"<li>{escape(x)}</li>" for x in lineas if x)
        return (f'<div class="as-caja as-res"><div class="as-top"><h3>{titulo}</h3>'
                f'<a class="as-ed" href="{_href(paso)}">Editar</a></div><ul>{lis}</ul></div>')

    from . import empresa  # empresa importa panel
    datos_empresa, catalogo = await empresa.de(c), await empresa.productos(c)
    if datos_empresa:
        negocio = [datos_empresa.get("nombre") or empresa.nombre_giro(datos_empresa) or "Tu Perfil de empresa",
                   panel.plural_(len(catalogo), "producto o servicio", "productos y servicios"),
                   panel.plural_(sum(1 for q in datos_empresa.get("faq") or [] if q.get("r")),
                                 "pregunta frecuente", "preguntas frecuentes")]
    else:
        negocio = ["Aún no llenas tu Perfil de empresa",
                   "Mientras, usa lo que guardaste antes" if perfil.get("negocio", "").strip() else ""]
    encendido = await panel.activo(c)
    if c.en_revision:
        boton = f'<p class="ayuda">{panel.REVISION}</p>'
    elif encendido:
        boton = '<a class="boton" href="/panel/asistente">Ir a tus conversaciones</a>'
    else:
        boton = ('<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="1">'
                 '<button class="boton">Encender tu asistente</button></form>')
    faltan = [t for k, t, _ in PASOS if k not in _hechos(perfil)]
    titulo = ("Tu asistente está contestando" if encendido else
              "Tu asistente está listo" if not faltan else "Ya casi está")
    bajada = ("Revisa que todo se vea bien. Con Editar regresas a ese paso." if not faltan else
              f"Te falta: {', '.join(faltan)}. Igual puedes encenderlo; usa lo que ya guardaste.")
    return (_cabeza("resumen", titulo, bajada)
            + '<div class="as-resumen">'
            + caja("Personalidad", "personalidad", [
                PERSONALIDADES[e["personalidad"]][0],
                (f"{TRATOS[e['trato']][0]}, respuestas {LARGOS[e['largo']][0].lower()},"
                f" emojis: {EMOJIS[e['emojis']][0].lower()}")], )
            + caja("Reglas y límites", "reglas",
                   ["Nunca inventa datos"] + [t for k, t, *_ in REGLAS if activas[k]]
                   + [f"No debe: {perfil['no_hacer'][:80]}" if perfil.get("no_hacer") else ""])
            + caja("Tu equipo", "equipo", [
                f"Atiende una persona {dias_de_horario(c)}",
                f"Cuando contestas tú, se calla {c.human_pause_hours:g} h",
                "Te avisa " + " y ".join(x for x in ("por DM" if c.aviso_dm and c.admin_ig_ids else "",
                                                      f"por correo a {c.owner_email}" if c.aviso_correo and c.owner_email
                                                      else "") if x) if (c.aviso_dm and c.admin_ig_ids) or
                (c.aviso_correo and c.owner_email) else "Sin avisos"])
            + caja("Lo que sabe de tu negocio", "empresa", negocio)
            + "</div>"
            + _servidor(c)
            + '<p class="as-nota">Además siempre sigue las reglas base de la plataforma: no inventa precios, no comparte'
            " datos de otras personas y respeta los límites de Instagram.</p>"
            f'<div class="as-pie"><a class="boton secundario" href="{_href("probar")}">Probar tu asistente</a>{boton}</div>')


async def pagina(c: Cuenta, paso: str = "", aviso: str = "", nota: str = "", perfil: dict | None = None) -> HTMLResponse:
    """Un paso de Configura. `perfil`: lo que trae el formulario cuando no se guardó (para no perderlo)."""
    guardado = await panel.perfil(c)
    perfil = guardado if perfil is None else perfil
    hechos = _hechos(guardado)
    if paso not in ORDEN and paso != "resumen":
        paso = next((k for k in ORDEN if k not in hechos), "resumen")
    if paso == "probar":
        paso = "resumen"
    if paso == "personalidad":
        cuerpo = _personalidad(perfil)
    elif paso == "escribe":
        cuerpo = _escribe(perfil)
    elif paso == "reglas":
        cuerpo = _reglas(perfil)
    elif paso == "equipo":
        cuerpo = _equipo(c, perfil)
    else:
        cuerpo = await _resumen(c, guardado)
    if paso != "resumen":
        cuerpo = ('<form method="post" action="/panel/asistente/paso">'
                  # Enter en un campo cuenta como Siguiente.
                  '<button class="oculto" name="accion" value="siguiente" tabindex="-1" aria-hidden="true">'
                  "Siguiente</button>"
                  f'<input type="hidden" name="paso" value="{paso}">{cuerpo}</form>')
    nombre = await panel.usuario(c)
    return panel.pagina(
        f"<style>{ESTILO}</style>"
        + panel.cabecera(salir=True, menu="asistente", cuenta=c, sub="configura", perfil=nombre,
                         foto=await panel.foto_perfil(c))
        + panel.encabezado("Tu asistente · Configura")
        + '<main class="as-main"><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + f'<div class="asis">{_lista(paso, guardado, hechos)}<div class="as-cont">'
        + (f'<div class="aviso as-aviso">{escape(nota)}</div>' if nota else "")
        + cuerpo + "</div></div></div></main>"
    )


@router.get("/asistente/configura")
async def ver(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return panel.entrar()
    q = request.query_params
    return await pagina(c, q.get("paso", ""), aviso="Guardado." if q.get("ok") == "1" else "")


@router.post("/asistente/paso")
async def guardar(request: Request):
    panel.de_aqui(request)
    c = panel.cuenta_de_sesion(request)
    if not c:
        return RedirectResponse("/panel", status_code=303)
    f = await panel.form(request)
    paso = f.get("paso", "")
    if paso not in ORDEN:
        return RedirectResponse("/panel/asistente/configura", status_code=303)
    antes = await panel.perfil(c)
    datos = dict(antes)
    if paso == "personalidad":
        k = f.get("personalidad", "")
        if k not in PERSONALIDADES:
            return await pagina(c, paso, nota="Elige una personalidad para seguir.")
        if k != antes.get("personalidad"):  # otra personalidad: empieza con su forma de escribir
            trato, largo, emojis = PERSONALIDADES[k][5]
            datos.update(personalidad=k, trato=trato, largo=largo, emojis=emojis)
    elif paso == "escribe":
        datos.update({k: v for k, v in estilo(antes).items() if k not in datos or k == "personalidad"})
        for campo, opciones in (("trato", TRATOS), ("largo", LARGOS), ("emojis", EMOJIS)):
            if f.get(campo) in opciones:
                datos[campo] = f[campo]
    elif paso == "reglas":
        datos["reglas"] = {k: f.get(f"r_{k}") == "1" for k, *_ in REGLAS}
        datos["no_hacer"] = f.get("no_hacer", "").strip()[:MAX_TEXTO]
        datos["pasar_equipo"] = f.get("pasar_equipo", "").strip()[:MAX_TEXTO]
        datos["instrucciones"] = f.get("instrucciones", "").strip()[:panel.MAX_INSTRUCCIONES]
    elif paso == "equipo":
        datos[panel.HUMANO[0]] = f.get(panel.HUMANO[0], "").strip()[:panel.MAX_CAMPO]
    datos["pasos"] = sorted(set(datos.get("pasos") or []) | {paso})
    nombre = await panel.usuario(c)
    prompt = panel.generar(nombre, datos, await panel.negocio_de_empresa(c))
    if paso in ESCRITOS and (motivo := await panel.revisar_instrucciones(c, prompt)):
        log.warning("@%s: no se guardó %s: %s", nombre, paso, motivo)
        return await pagina(c, paso, nota=f"No se guardó: {motivo} Corrígelo y vuelve a intentar.", perfil=datos)
    if paso == "equipo":
        from . import ajustes
        await ajustes.guardar_del_form(c, await request.form())
    await c.store.set("perfil", json.dumps(datos, ensure_ascii=False))
    c.prompt_destino().parent.mkdir(parents=True, exist_ok=True)
    c.prompt_destino().write_text(prompt, encoding="utf-8")
    await c.store.set("asistente_listo", "1")
    log.info("@%s guardó %s de su asistente desde el panel", nombre, paso)
    siguiente = ORDEN[ORDEN.index(paso) + 1] if paso != ORDEN[-1] else "resumen"
    return RedirectResponse(f"{_href(siguiente)}&ok=1", status_code=303)


ESTILO = """
body:has(.asis){background:var(--negro)}
.as-main{background:var(--negro);color:var(--claro)}
.asis{display:grid;grid-template-columns:17rem minmax(0,1fr);gap:1.75rem;align-items:start}
.as-pasos{background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;padding:1.25rem;position:sticky;top:1rem}
.as-t{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--gris);margin:0 0 .75rem}
.as-paso{display:flex;gap:.75rem;align-items:center;padding:.65rem .6rem;border-radius:.8rem;text-decoration:none;color:var(--claro)}
.as-paso:hover{background:#35323F}
.as-paso.on{background:#3A3746}
.as-paso b{display:block;font-size:.92rem;font-weight:600}
.as-paso small{display:block;font-size:.78rem;color:var(--gris);line-height:1.35}
.as-n{width:1.8rem;height:1.8rem;flex:none;border-radius:50%;border:1px solid #53505F;display:grid;place-items:center;font-size:.8rem;color:var(--gris)}
.as-paso.ok .as-n{background:var(--neon);border-color:var(--neon);color:var(--tinta)}
.as-paso.on .as-n{border-color:var(--acento-claro);color:var(--acento-claro)}
.as-barra{height:.3rem;background:#3A3746;border-radius:9px;margin:1rem 0 .5rem}
.as-barra i{display:block;height:100%;background:var(--acento-claro);border-radius:9px}
.as-nota{display:block;color:var(--gris);font-size:.8rem;line-height:1.5}
.as-num{color:var(--acento-claro);font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;font-weight:600;margin:0}
.as-h{font-size:1.9rem;letter-spacing:-.02em;margin:.3rem 0 .25rem}
.as-baj{color:var(--gris);margin:0 0 1.4rem}
/* Cada paso va en su tarjeta, como en Tu marca y Crear campaña; lo de adentro, un tono más hondo. */
.as-cont{background:var(--grafito);border-radius:1.5rem;padding:2rem 2.25rem;min-width:0}
.as-cont .as-caja,.as-cont .as-pers{background:#2A2834}
.as-cont .aviso,.as-main .aviso{background:var(--grafito);color:var(--claro)}.as-cont .aviso{background:#2A2834}
.as-caja{background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;padding:1.4rem 1.5rem;margin:0 0 1rem;min-width:0}
fieldset.as-caja legend{float:left;width:100%;padding:0;font-weight:600;font-size:1.02rem}
.as-caja h3{margin:0 0 .25rem;font-size:1.05rem}
.as-caja>label:first-child,.as-caja>.sub:first-child{margin-top:0}.as-main input[type=checkbox]{accent-color:var(--acento-claro)}
.as-main .ayuda{color:var(--gris)}
.as-main label{color:var(--claro)}
.as-main textarea,.as-main input[type=text],.as-main input[type=time],.as-main input[type=email]{background:#25232F;color:var(--claro);border-color:#45424F}
.as-main .boton{background:var(--acento);color:var(--tinta)}
.as-main .boton.secundario{background:transparent;color:var(--claro);border-color:#53505F}
.as-main .par{border-color:var(--linea-oscura)}
.as-main .par .quitar{color:var(--gris)}
.as-main .chips label{background:#25232F;border-color:#45424F;color:var(--claro)}
.as-main .chips label:has(input:checked){background:var(--acento);color:var(--tinta);border-color:var(--acento)}
.as-main .opcion{background:#25232F;border-color:#45424F}
.as-main .opcion span{color:var(--gris)}
.as-main .opcion:has(input:checked){background:rgba(243,154,54,.12);border-color:var(--acento-claro);box-shadow:inset 0 0 0 1px var(--acento-claro)}
.as-main .sub{border-top-color:var(--linea-oscura)}
.as-tarjetas{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:1rem;border:0;padding:0;margin:0;min-width:0}
.as-pers{position:relative;margin:0;font-weight:400;display:flex;flex-direction:column;background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;overflow:hidden;cursor:pointer}
.as-pers input{position:absolute;opacity:0;pointer-events:none}
.as-pers:has(input:checked){border-color:var(--acento-claro);box-shadow:0 0 0 3px rgba(243,154,54,.18)}
.as-pers:has(input:focus-visible){outline:2px solid var(--acento-claro);outline-offset:2px}
.as-img{position:relative;display:block;aspect-ratio:3/2;background:#25232F}
.as-img img,.as-arte{display:block;width:100%;height:100%;object-fit:cover}
.as-ck{position:absolute;top:.7rem;right:.7rem;width:1.6rem;height:1.6rem;border-radius:50%;background:var(--acento-claro);color:var(--tinta);font-weight:700;font-size:.85rem;display:none;place-items:center}
.as-pers:has(input:checked) .as-ck{display:grid}
.as-in{display:block;padding:1rem 1.1rem 1.15rem}
.as-nom{display:block;font-size:1.05rem;margin-bottom:.5rem}
.as-rasgos{display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:.6rem}
.as-rasgos span{font-size:.74rem;border:1px solid #45424F;border-radius:999px;padding:.15rem .6rem;color:#E6E3EC}
.as-lbl{display:block;font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--gris)}
.as-uso{display:block;font-size:.86rem;color:#E6E3EC;line-height:1.45;margin-bottom:.6rem}
.as-dlg,.as-muestra{display:block;background:#25232F;border:1px solid var(--linea-oscura);border-radius:.8rem;padding:.65rem .8rem;font-size:.85rem;line-height:1.45}
.as-dlg .u,.as-muestra .u{display:block;color:var(--gris)}
.as-dlg .a,.as-muestra .a{display:block;color:var(--claro);margin-top:.3rem}
.as-muestra{border-style:dashed;padding:1rem 1.1rem;font-size:.92rem}
.as-muestra b{display:block;margin-bottom:.3rem}
.as-seg{display:inline-flex;flex-wrap:wrap;background:#25232F;border:1px solid var(--linea-oscura);border-radius:.8rem;padding:.25rem;gap:.25rem;margin-top:.4rem}
.as-seg label{position:relative;margin:0;font-weight:500;font-size:.92rem;padding:.5rem 1.1rem;border-radius:.6rem;cursor:pointer;color:#E6E3EC}
.as-seg input{position:absolute;opacity:0;pointer-events:none}
.as-seg label:has(input:checked){background:var(--acento);color:var(--tinta);font-weight:600}
.as-seg label:has(input:focus-visible){outline:2px solid var(--acento-claro)}
.v-usted,.v-detalle,.v-emoji2{display:none}
form:has(#trato-usted:checked) .v-usted{display:inline}form:has(#trato-usted:checked) .v-tu{display:none}
form:has(#largo-detalle:checked) .v-detalle{display:inline}form:has(#largo-breve:checked) .v-pregunta{display:none}
form:has(#emojis-nunca:checked) .v-emoji{display:none}form:has(#emojis-seguido:checked) .v-emoji2{display:inline}
.as-fila{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin:0;padding:.9rem 0;border-top:1px solid var(--linea-oscura);font-weight:400;cursor:pointer}
.as-fila:first-child{border-top:0;padding-top:0}
.as-fila b{display:block;font-size:.95rem}
.as-fila em{font-style:normal;font-size:.72rem;color:var(--neon);margin-left:.4rem;font-weight:500}
.as-fila small{display:block;font-size:.85rem;color:var(--gris)}
.as-sw{-webkit-appearance:none;appearance:none;flex:none;width:2.9rem;height:1.75rem;border-radius:999px;background:#45424F;position:relative;cursor:pointer;margin:0;transition:background .15s}
.as-sw::after{content:"";position:absolute;left:.2rem;top:.2rem;width:1.35rem;height:1.35rem;border-radius:50%;background:var(--blanco);transition:left .15s}
.as-sw:checked{background:var(--acento-claro)}.as-sw:checked::after{left:1.35rem}
.as-sw:disabled{background:rgba(198,250,44,.4);cursor:default}
.as-sw:focus-visible{outline:2px solid var(--acento-claro);outline-offset:2px}
.as-mas{margin-top:1.25rem;border-top:1px solid var(--linea-oscura);padding-top:1rem}
.as-mas summary{cursor:pointer;color:var(--gris);font-size:.92rem}
.as-resumen{display:grid;grid-template-columns:repeat(auto-fill,minmax(18rem,1fr));gap:1rem}
.as-res{margin:0}.as-res ul{margin:.4rem 0 0;padding-left:1.1rem;color:#E6E3EC;font-size:.9rem;line-height:1.7}
.as-top{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.as-ed{font-size:.82rem;color:var(--acento-claro);border:1px solid rgba(243,154,54,.45);border-radius:999px;padding:.25rem .8rem;text-decoration:none}
.as-res+.as-nota,.as-resumen+.as-nota{margin-top:1rem}
.as-pie{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:1.5rem;flex-wrap:wrap}
.as-pie form{margin:0}
.as-srv{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;margin-top:1rem;color:#d1d1d6;font-size:.9rem}
@media(max-width:900px){.asis{grid-template-columns:1fr}.as-pasos{position:static;display:flex;gap:.4rem;overflow-x:auto;padding:.75rem}
.as-t,.as-barra,.as-pasos .as-nota,.as-paso small,.as-paso:not(.on) b{display:none}.as-paso{flex:none;padding:.35rem}.as-paso.on{padding-right:.8rem}.as-paso b{font-size:.82rem;white-space:nowrap}}
@media(max-width:560px){.as-cont{padding:1.4rem 1.1rem}.as-img{aspect-ratio:2/1}.as-h{font-size:1.55rem}.as-caja{padding:1.1rem}.as-seg{display:flex}.as-seg label{flex:1;text-align:center;padding:.5rem .6rem}}
.as-n{color:var(--acento);border-color:rgba(243,154,54,.45)}
.as-paso.on .as-n{visibility:hidden}
.as-barra i{background:var(--neon)}
.as-pers:has(input:checked){border-color:var(--neon)}
.as-ck{background:var(--neon);color:var(--tinta)}
.as-main .opcion:has(input:checked){background:rgba(198,250,44,.07);border-color:var(--neon);box-shadow:inset 0 0 0 1px var(--neon)}
.as-main .chips label:has(input:checked),.as-seg label:has(input:checked){background:var(--neon);border-color:var(--neon);color:var(--tinta)}
.as-sw:checked{background:var(--neon)}
.as-paso.ok .as-n{background:var(--acento);border-color:var(--acento);color:var(--tinta)}
.as-pers:has(input:checked){border-color:var(--acento)}
.as-ck{background:var(--acento);color:var(--tinta)}
.as-main .opcion:has(input:checked){background:rgba(243,154,54,.10);border-color:var(--acento);box-shadow:inset 0 0 0 1px var(--acento)}
.as-main .chips label:has(input:checked),.as-seg label:has(input:checked){background:var(--acento);border-color:var(--acento);color:var(--tinta)}
.as-sw:checked{background:var(--acento)}
"""
