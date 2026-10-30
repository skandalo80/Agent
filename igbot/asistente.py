"""Tu asistente > Configura, para los clientes: pasos cortos en vez de un formulario largo.

1 Personalidad (6 tarjetas y un toque personal opcional), 2 Cómo escribe (trato, largo y emojis), 3 Reglas y límites
(interruptores y dos cajas), 4 Tu equipo (qué decir si piden una persona, y de ajustes.py la
pausa, el horario en que atiende una persona y los avisos) y un resumen con Editar, desde donde
se prueba (/panel/probar) y se enciende. Lo que sabe del negocio (datos, catálogo, preguntas y políticas) viene del
Perfil de empresa (empresa.py); si aún no lo llena, de lo que guardó antes en Configura.

Cada paso guarda solo lo suyo en kv "perfil" y vuelve a escribir el prompt (panel.generar).
Lo que el dueño escribe a mano pasa por panel.revisar_instrucciones; elegir de una lista no.
Las reglas base (prompts/base.md) siguen al final de todo prompt, como antes.
Sin JavaScript: radios y casillas con CSS (:has) y un POST por paso.
La cuenta principal se configura igual; su prompt va aparte del repo (Cuenta.prompt_destino) y
POST /panel/guardar con accion=restaurar la regresa a prompts/system.md (ya sin botón en el panel).
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

# clave: (nombre, cómo atiende, ideal para, pregunta y respuesta de ejemplo, cómo habla (va al prompt),
#         (trato, largo, emojis) con que empieza)
PERSONALIDADES = {
    "cercano": ("Cercano y amable",
                "Atiende con calidez y confianza, como si tus clientes hablaran con alguien de tu propio equipo.",
                "Tiendas, restaurantes, belleza y servicios cotidianos.",
                ("¿Tienen envío a Monterrey?", ("¡Sí! Te ayudo a revisar las opciones de envío a Monterrey."
                 " ¿Qué producto te interesa?")),
                "Cercano y amable: cálido, natural y conversacional, como alguien del equipo que atiende con gusto.",
                ("tu", "equilibrada", "aveces")),
    "profesional": ("Profesional y confiable",
                    "Transmite seriedad, resuelve dudas con claridad y genera confianza en cada conversación.",
                    "Consultorios, despachos, inmobiliarias y servicios profesionales.",
                    ("¿Qué necesito para una cita?", ("Con gusto te orientamos. ¿Qué tipo de cita necesitas? Así te"
                     " comparto los requisitos correspondientes.")),
                    "Profesional y confiable: claro, preciso y respetuoso. Transmites seguridad y vas al punto.",
                    ("tu", "equilibrada", "nunca")),
    "divertido": ("Divertido y desenfadado",
                  ("Conversa de forma relajada, muestra la personalidad de tu marca y hace cada conversación más"
                   " amena."),
                  "Moda, gastronomía, entretenimiento y marcas juveniles.",
                  ("¿Todavía tienen los tenis?", "¡Vamos a encontrar tu par! ¿Qué modelo y talla buscas?"),
                  ("Divertido y desenfadado: espontáneo, creativo y expresivo, con humor suave cuando cabe y sin"
                   " bromas que puedan ofender."),
                  ("tu", "breve", "seguido")),
    "empatico": ("Empático y paciente",
                 ("Escucha, resuelve inquietudes con paciencia y acompaña al cliente, sobre todo cuando surge un"
                  " problema."),
                 "Salud, bienestar, clínicas y atención posventa.",
                 ("Mi pedido llegó dañado.", ("Lamento mucho que haya llegado así. Vamos a revisar qué pasó y cómo"
                  " podemos ayudarte. ¿Me compartes una foto?")),
                 ("Empático y paciente: escuchas, reconoces cómo se siente la persona y la acompañas con calma,"
                  " paso a paso."),
                 ("tu", "equilibrada", "nunca")),
    "energico": ("Enérgico y motivador",
                 ("Contagia entusiasmo, impulsa a tus clientes a dar el siguiente paso y mantiene la conversación"
                  " con energía."),
                 "Gimnasios, coaches, deportes y cursos.",
                 ("Nunca he tomado una clase.", ("¡Todos empezamos alguna vez! Te ayudo a encontrar una clase para"
                  " principiantes. ¿Qué te gustaría aprender?")),
                 ("Enérgico y motivador: dinámico y positivo. Animas a dar el siguiente paso sin presionar ni"
                  " prometer de más."),
                 ("tu", "breve", "aveces")),
    "premium": ("Elegante y premium",
                "Cuida cada detalle y transmite calidad, atención personalizada y una experiencia distinguida.",
                "Joyería, hoteles, arquitectura y marcas de lujo.",
                ("¿Hacen piezas a la medida?", ("Sí, podemos orientarte sobre las opciones de diseño personalizado."
                 " ¿Qué pieza tienes en mente?")),
                ("Elegante y premium: refinado y discreto, cuidado en cada palabra, con un trato atento y exclusivo,"
                 " sin exagerar."),
                ("usted", "equilibrada", "nunca")),
}
# La tabla "¿Cuándo pasar la conversación a tu equipo?" (al final de Reglas y límites). Cada caso se pasa o no,
# y al pasarlo el cliente recibe el mensaje predeterminado, lo que conteste la IA o nada.
# (clave, caso, razón de pasar_a_humano, se pasa al empezar, cuándo en el prompt, ejemplo del mensaje)
CASOS = [
    ("persona", "Pide hablar con una persona", "pide_persona", True, "Si pide hablar con una persona",
     "Ej. Claro, le aviso a alguien del equipo para que te escriba por aquí."),
    ("quejas", "Una queja, reclamo o reembolso", "queja", True,
     "Si es una queja, un reclamo, un reembolso o un caso delicado",
     "Ej. Lamento lo que pasó. Le paso tu caso a alguien del equipo para que lo resuelva contigo."),
    ("enojado", "Nota al cliente molesto o enojado", "molesto", True, "Si notas a la persona molesta o enojada",
     "Ej. Entiendo tu molestia. Le pido a alguien del equipo que te atienda en un momento."),
    ("precio", "Quiere negociar el precio", "precio", True, "Si quiere negociar el precio o pide un trato especial",
     "Ej. Le paso tu mensaje a alguien del equipo para ver qué opciones hay."),
    ("otro", "Otro caso", "otro", False, "", "Ej. Le paso tu pedido a alguien del equipo."),
]
DE_RAZON = {razon: k for k, _, razon, *_ in CASOS} | {"fuera_catalogo": "otro"}
MODOS = {"fijo": "Predeterminado", "ia": "La IA contesta", "nada": "Sin mensaje"}
MAX_NOMBRE = 40  # nombre del asistente
MAX_TOQUE = 500  # "¿Quieres darle un toque más personal?" en Personalidad
TRATOS = {"tu": ("De tú", "Tuteas a las personas."),
          "usted": ("De usted", "Hablas de usted a las personas.")}
# El largo va con un tope de palabras: "dos o tres frases" solo, el modelo lo estira con frases largas.
LARGOS = {"breve": ("Muy breves", "Contestas en una o dos frases cortas: unas 25 palabras como máximo."),
          "equilibrada": ("Equilibradas", ("Contestas en dos o tres frases cortas, claras y al punto: unas 50 palabras"
                                           " como máximo. Si hay más que contar, di lo principal y deja el resto para"
                                           " cuando lo pregunte.")),
          "detalle": ("Con detalle", ("Puedes dar respuestas más completas cuando ayuden, sin pasar de un párrafo"
                                     " corto: unas 90 palabras como máximo."))}
EMOJIS = {"nunca": ("Nunca", "No usas emojis."),
          "aveces": ("De vez en cuando", "Puedes usar algún emoji de vez en cuando."),
          "seguido": ("Seguido", "Usas emojis con frecuencia, sin exagerar.")}
# El tono de antes (amigable, profesional o formal): sigue funcionando hasta que elija personalidad.
TONO_VIEJO = {"amigable": ("cercano", "tu", "breve", "aveces"),
              "profesional": ("profesional", "tu", "equilibrada", "nunca"),
              "formal": ("profesional", "usted", "equilibrada", "nunca")}
# (clave, título, para qué sirve, encendida al empezar, lo que va al prompt encendida, y apagada)
REGLAS = [
    ("ia", "Dice que es una IA al saludar", "En su primer mensaje aclara que es un asistente virtual.",
     True, "En tu primer mensaje de cada conversación, aclara que eres un asistente virtual con inteligencia artificial.",
     ""),
    ("preguntar", "Pregunta antes de recomendar", "Hace una o dos preguntas para entender qué busca la persona.",
     True, "Antes de recomendar algo, haces una o dos preguntas para entender qué busca la persona.",
     "Si te piden una recomendación, la das directo con lo que ya sabes, sin hacer preguntas para indagar."),
    ("siguiente", "Invita al siguiente paso", "Sugiere comprar, agendar o visitar cuando la persona muestra interés.",
     True, "Cuando la persona muestra interés, la invitas al siguiente paso: comprar, agendar o visitar.",
     # Apagada también va al prompt: sin nada, el modelo vende por costumbre (ofrece cotizar, agendar...).
     "No empujas a comprar, cotizar, agendar ni visitar, ni cierras tus mensajes ofreciéndolo: contestas lo que"
     " te preguntan. Si la persona pide avanzar, la ayudas."),
    ("links", "Comparte el link del producto", "Si preguntan por algo que tiene link, lo manda.",
     True, "Si preguntan por un producto o servicio que tiene link en tu información, compártelo.", ""),
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


def pases(perfil: dict) -> dict[str, dict]:
    """Cada caso de la tabla: {"on", "modo", "msg"} (y "texto" en Otro caso). Sin tabla guardada, sale de lo de
    antes: las reglas quejas y enojado con su mensaje al transferir, y "¿Cuándo debe pasar...?" como Otro caso."""
    guardados, reglas = perfil.get("pases") or {}, perfil.get("reglas") or {}
    out = {}
    for k, _, _, encendido, *_ in CASOS:
        if k in guardados:
            g = guardados[k]
            out[k] = {"on": bool(g.get("on")), "modo": g.get("modo") if g.get("modo") in MODOS else "ia",
                      "msg": g.get("msg", ""), "texto": g.get("texto", "")}
            continue
        msg = perfil.get(f"msg_{k}", "").strip() if k in ("quejas", "enojado") else ""
        texto = perfil.get("pasar_equipo", "").strip() if k == "otro" else ""
        out[k] = {"on": bool(reglas.get(k, encendido)) if k != "otro" else bool(texto),
                  "modo": "fijo" if msg else "ia", "msg": msg, "texto": texto}
    return out


def mensaje_al_pasar(perfil: dict, razon: str) -> str | None:
    """Lo que recibe el cliente al pasarlo a tu equipo, si no lo escribe la IA: el mensaje predeterminado o ""."""
    caso = pases(perfil)[DE_RAZON.get(razon, "otro")]
    if caso["modo"] == "nada":
        return ""
    return caso["msg"].strip() if caso["modo"] == "fijo" and caso["msg"].strip() else None


def pases_prompt(perfil: dict) -> list[str]:
    out = ["", "CUÁNDO PASAR LA CONVERSACIÓN A TU EQUIPO (usa pasar_a_humano con esa razón)"]
    for k, _, razon, _, cuando, _ in CASOS:
        caso = pases(perfil)[k]
        if k == "otro":
            if caso["on"] and caso["texto"].strip():
                # Lo que escribe el dueño va escapado y entre etiquetas: son datos, no instrucciones nuevas.
                out += [f"- En estos casos (razón {razon}):",
                        f"<cuando_pasar_al_equipo>\n{escape(caso['texto'].strip())}\n</cuando_pasar_al_equipo>"]
            continue
        if not caso["on"]:
            out.append(f"- {cuando}: no la pases a tu equipo; atiéndela tú con calma en lo que puedas.")
        elif caso["modo"] == "ia":
            out.append(f"- {cuando}: usa pasar_a_humano (razón {razon}) y dile con tus palabras que alguien del"
                       " equipo le escribe por aquí.")
        else:
            out.append(f"- {cuando}: usa pasar_a_humano (razón {razon}). El mensaje al cliente lo manda el sistema:"
                       " no hace falta que escribas nada más.")
    if humano := perfil.get("humano", "").strip():  # lo de antes en Tu equipo, para "La IA contesta"
        out += ["Si pide hablar con una persona y le escribes tú, el dueño quiere que digas esto:",
                f"<si_piden_una_persona>\n{escape(humano)}\n</si_piden_una_persona>"]
    return out


def tono_prompt(perfil: dict) -> list[str]:
    """Las líneas de TONO y CÓMO ATIENDES del prompt (panel.generar las pone después de la presentación)."""
    e = estilo(perfil)
    out = []
    if nombre := " ".join(perfil.get("nombre_asistente", "").split()):
        out += [f"Te llamas {escape(nombre)}; preséntate con ese nombre cuando saludes.", ""]
    out += ["TONO", PERSONALIDADES[e["personalidad"]][4],
           f"- {TRATOS[e['trato']][1]}", f"- {LARGOS[e['largo']][1]}", f"- {EMOJIS[e['emojis']][1]}"]
    if toque := perfil.get("toque", "").strip():
        out += ["Además, el dueño describe así cómo quiere que hables:",
                f"<como_quiere_que_hables>\n{escape(toque)}\n</como_quiere_que_hables>"]
    activas = reglas_de(perfil)
    reglas = [si if activas[k] else no for k, _, _, _, si, no in REGLAS if (si if activas[k] else no)]
    if reglas:
        out += ["", "CÓMO ATIENDES", *(f"- {r}" for r in reglas)]
    # Lo que escribe el dueño va escapado y entre etiquetas: son datos, no instrucciones nuevas.
    if no_hacer := perfil.get("no_hacer", "").strip():
        out += ["", "LO QUE NO DEBES DECIR NI HACER (además de las reglas base)",
                f"<lo_que_no_debes_hacer>\n{escape(no_hacer)}\n</lo_que_no_debes_hacer>"]
    return out + pases_prompt(perfil)


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
            + panel.avance(listos * 100 // len(ORDEN))
            + f'<small class="as-nota">{listos} de {len(ORDEN)} listos. Puedes volver a cualquier paso cuando'
            " quieras.</small></nav>")


def _cabeza(paso: str, titulo: str, bajada: str) -> str:
    if paso in ORDEN:
        antes = f"Paso {ORDEN.index(paso) + 1} de {len(ORDEN)}"
    else:
        antes = "Resumen"
    return f'<p class="as-num">{antes}</p><h2 class="as-h">{titulo}</h2><p class="as-baj">{bajada}</p>'


def _pie(paso: str) -> str:
    i = ORDEN.index(paso)
    atras = f'<a class="boton secundario" href="{_href(ORDEN[i - 1])}">← Anterior</a>' if i else ""
    nombre = PASOS[i + 1][1].lower() if i + 1 < len(ORDEN) else "resumen"
    return panel.pie_config(atras, f'<button class="boton" name="accion" value="siguiente">Siguiente: {nombre} →</button>')


def _radios(nombre: str, opciones: dict, actual: str, pregunta: str, ayuda: str) -> str:
    botones = "".join(
        f'<label><input type="radio" id="{nombre}-{k}" name="{nombre}" value="{k}"{" checked" if k == actual else ""}>'
        f"{etiqueta}</label>" for k, (etiqueta, _) in opciones.items())
    return (f'<fieldset class="as-caja"><legend>{pregunta}</legend><p class="ayuda">{ayuda}</p>'
            f'<div class="as-seg">{botones}</div></fieldset>')


def _tarjeta(k: str, elegida: bool) -> str:
    nombre, como, uso, (pregunta, respuesta), *_ = PERSONALIDADES[k]
    return (f'<label class="as-pers"><input type="radio" name="personalidad" value="{k}"{" checked" if elegida else ""}>'
            f'<span class="as-ck" aria-hidden="true">✓</span>'
            f'<span class="as-in"><b class="as-nom">{nombre}</b><span class="as-para">{como}</span>'
            f'<span class="as-uso"><b>Ideal para:</b> {uso}</span>'
            f'<span class="as-lbl">Ejemplo</span>'
            f'<span class="as-dlg"><span class="u">Cliente: {pregunta}</span>'
            f'<span class="a">Asistente: {respuesta}</span></span></span></label>')


def _opcional(nombre: str, titulo: str, ayuda: str, ejemplo: str, valor: str, maximo: int) -> str:
    """Un campo opcional plegado: la casilla lo abre (CSS :has); sin marcar, al guardar se vacía."""
    return (f'<div class="as-opt"><label class="as-optc"><input type="checkbox" name="{nombre}_on" value="1"'
            f'{" checked" if valor else ""}><span><b>{titulo}</b> <span class="as-opc">(Opcional)</span></span></label>'
            f'<div class="as-optb">' + (f'<p class="ayuda">{ayuda}</p>' if ayuda else "")
            + f'<textarea name="{nombre}" rows="3" maxlength="{maximo}" aria-label="{titulo}" placeholder="{ejemplo}">'
            f"{escape(valor)}</textarea></div></div>")


def _personalidad(perfil: dict) -> str:
    actual = perfil.get("personalidad") if perfil.get("personalidad") in PERSONALIDADES else ""
    return (_cabeza("personalidad", "Elige su personalidad",
                    "Elige el estilo que mejor represente a tu marca. Así definiremos cómo se comunica tu asistente"
                    " con tus clientes.")
            + '<fieldset class="as-tarjetas"><legend class="oculto">Personalidad</legend>'
            + "".join(_tarjeta(k, k == actual) for k in PERSONALIDADES) + "</fieldset>"
            + '<div class="as-caja as-toque as-nombre"><input type="hidden" name="opcionales" value="1">'
            '<label for="nombre_asistente">¿Cómo se llama tu asistente? <span class="as-opc">(Opcional)</span>'
            '</label><p class="ayuda">Se presenta con este nombre al saludar.</p>'
            f'<input type="text" id="nombre_asistente" name="nombre_asistente" maxlength="{MAX_NOMBRE}" placeholder="Ej. Sofía"'
            f' value="{escape(perfil.get("nombre_asistente", ""))}"><div class="as-opt">'
            + _opcional("toque", "¿Quieres darle un toque más personal?",
                        "Describe brevemente cómo te gustaría que hablara tu asistente. Puedes mencionar algo que deba"
                        " hacer o evitar.",
                        "Ejemplo: Que explique todo con paciencia, recomiende productos sin presionar y hable como"
                        " alguien de nuestro equipo.", perfil.get("toque", ""), MAX_TOQUE) + "</div></div>"
            + _pie("personalidad"))


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
    for k, titulo, ayuda, *_ in REGLAS:
        filas += (f'<label class="as-fila"><span><b>{titulo}</b><small>{ayuda}</small></span>'
                  f'<input type="checkbox" class="as-sw" name="r_{k}" value="1"{" checked" if activas[k] else ""}>'
                  "</label>")
    extra = perfil.get("instrucciones", "")
    return (_cabeza("reglas", "Reglas y límites", "Qué puede hacer por su cuenta y qué no.")
            + f'<div class="as-caja">{filas}</div>'
            '<div class="as-caja as-toque"><input type="hidden" name="opcionales" value="1">'
            + _opcional("no_hacer", "¿Qué no debe decir o hacer?", "",
                        "Ej. No prometer fechas de entrega exactas, no hablar de la competencia.",
                        perfil.get("no_hacer", ""), MAX_TEXTO)
            # Las instrucciones libres de antes no se pierden: aquí siguen.
            + _opcional("instrucciones", "Algo más que deba saber", "",
                        "Ej. Saluda por su nombre y siempre invita a visitar la tienda.", extra, panel.MAX_INSTRUCCIONES)
            + "</div>" + _tabla_pases(perfil) + _pie("reglas"))


def _tabla_pases(perfil: dict) -> str:
    """Una fila por caso: si se pasa (interruptor) y qué recibe el cliente; el mensaje se ve solo con Predeterminado."""
    filas = ""
    for k, titulo, _, _, _, ejemplo in CASOS:
        caso = pases(perfil)[k]
        modos = "".join(f'<label><input type="radio" name="m_{k}" value="{m}"{" checked" if m == caso["modo"] else ""}>'
                        f"{nombre}</label>" for m, nombre in MODOS.items())
        otro = (f'<textarea class="as-pase-desc" name="t_otro" rows="2" maxlength="{MAX_TEXTO}" aria-label="Cuándo pasarla"'
                ' placeholder="Ej. Si piden más de 10 piezas o una cotización especial.">'
                f'{escape(caso["texto"])}</textarea>' if k == "otro" else "")
        filas += (f'<div class="as-pase as-pase-{k}"><b class="as-pase-caso">{titulo}</b>{otro}'
                  f'<label class="as-pase-on"><input type="checkbox" class="as-sw" name="p_{k}" value="1"'
                  f'{" checked" if caso["on"] else ""} aria-label="Pasar a tu equipo: {titulo}"><span>Pasar</span></label>'
                  f'<div class="as-pase-msg"><div class="as-seg">{modos}</div>'
                  f'<textarea class="as-pase-fijo" name="msg_{k}" rows="2" maxlength="{MAX_TEXTO}"'
                  f' aria-label="Mensaje predeterminado: {titulo}" placeholder="{ejemplo}">{escape(caso["msg"])}</textarea>'
                  "</div></div>")
    return ('<div class="as-caja as-pases"><h3 class="as-pases-t">¿Cuándo pasar la conversación a tu equipo?</h3>'
            '<p class="ayuda">Marca los casos que atiende una persona y elige qué recibe el cliente al pasarlo.'
            " Al pasarla, te avisamos y el asistente deja de contestar en ese chat.</p>"
            '<div class="as-pase as-pase-cab" aria-hidden="true"><span>Caso</span><span>Pasar</span>'
            "<span>Qué recibe el cliente</span></div>" + filas + "</div>")


def _equipo(c: Cuenta, perfil: dict) -> str:
    from . import ajustes  # ajustes importa panel
    # Lo que dice al pasar a una persona ya está en Reglas (mensaje al transferir) y aquí, fuera de horario;
    # el texto "humano" que alguien guardó antes sigue en sus instrucciones (panel.generar).
    return (_cabeza("equipo", "Tu equipo", "Cuándo entra una persona y cómo te avisamos.")
            + f'<div class="as-caja">{ajustes.campos(c)}</div>'
            + _pie("equipo"))


async def _resumen(c: Cuenta, perfil: dict) -> str:
    e = estilo(perfil)
    activas = reglas_de(perfil)

    def caja(titulo: str, paso: str, lineas: list[str]) -> str:
        lis = "".join(f"<li>{escape(x)}</li>" for x in lineas if x)
        return f'<div class="as-caja as-res">{panel.cab_tarjeta(titulo, _href(paso))}<ul>{lis}</ul></div>'

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
        # Aquí se pausa al asistente completo (para todos los chats); en Conversaciones solo se pausa cada chat.
        boton = ('<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="0">'
                 '<input type="hidden" name="volver" value="configura">'
                 '<button class="boton secundario">Pausar tu asistente</button></form>'
                 '<a class="boton" href="/panel/asistente">Ir a tus conversaciones</a>')
    else:
        boton = ('<form method="post" action="/panel/estado"><input type="hidden" name="activo" value="1">'
                 '<input type="hidden" name="volver" value="configura">'
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
                f" emojis: {EMOJIS[e['emojis']][0].lower()}"),
                f"Se llama {perfil['nombre_asistente']}" if perfil.get("nombre_asistente") else "",
                f"Tu toque: {perfil['toque'][:80]}" if perfil.get("toque") else ""])
            + caja("Reglas y límites", "reglas",
                   ["Nunca inventa datos"] + [t for k, t, *_ in REGLAS if activas[k]]
                   + [f"No debe: {perfil['no_hacer'][:80]}" if perfil.get("no_hacer") else ""]
                   + ["Pasa a tu equipo: " + ", ".join(t.lower() for k, t, *_ in CASOS if pases(perfil)[k]["on"])
                      if any(c["on"] for c in pases(perfil).values()) else "No pasa conversaciones a tu equipo"])
            + caja("Tu equipo", "equipo", [
                f"Cuando contestas tú, se calla {c.human_pause_hours:g} h",
                f"Te avisa por correo a {c.owner_email}" if c.aviso_correo and c.owner_email else "Sin avisos"])
            + caja("Lo que sabe de tu negocio", "empresa", negocio)
            + "</div>"
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
    return panel.pagina(
        f"<style>{ESTILO}{panel.COMUN}</style>"
        + await panel.menu_de(c, "asistente", sub="configura")
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
    return await pagina(c, q.get("paso", ""), aviso={"1": "Guardado.", "aplicado": "Cambios aplicados."}.get(q.get("ok", ""), ""))


def _texto(f: dict, campo: str, maximo: int) -> str:
    """Lo escrito en un campo opcional; si su casilla quedó sin marcar, nada."""
    if f.get("opcionales") == "1" and f.get(f"{campo}_on") != "1":
        return ""
    return f.get(campo, "").strip()[:maximo]


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
        datos["personalidad"] = k
        datos["toque"] = _texto(f, "toque", MAX_TOQUE)
        datos["nombre_asistente"] = " ".join(f.get("nombre_asistente", "").split())[:MAX_NOMBRE]
    elif paso == "escribe":
        datos.update({k: v for k, v in estilo(antes).items() if k not in datos or k == "personalidad"})
        for campo, opciones in (("trato", TRATOS), ("largo", LARGOS), ("emojis", EMOJIS)):
            if f.get(campo) in opciones:
                datos[campo] = f[campo]
    elif paso == "reglas":
        datos["reglas"] = {k: f.get(f"r_{k}") == "1" for k, *_ in REGLAS}
        datos["pases"] = {k: {"on": f.get(f"p_{k}") == "1", "modo": f.get(f"m_{k}") if f.get(f"m_{k}") in MODOS else "ia",
                              "msg": f.get(f"msg_{k}", "").strip()[:MAX_TEXTO],
                              **({"texto": f.get("t_otro", "").strip()[:MAX_TEXTO]} if k == "otro" else {})}
                          for k, *_ in CASOS}
        for viejo in ("msg_quejas", "msg_enojado", "pasar_equipo"):  # ya van en la tabla
            datos.pop(viejo, None)
        datos["no_hacer"] = _texto(f, "no_hacer", MAX_TEXTO)
        datos["instrucciones"] = _texto(f, "instrucciones", panel.MAX_INSTRUCCIONES)
    datos["pasos"] = sorted(set(datos.get("pasos") or []) | {paso})
    nombre = await panel.usuario(c)
    prompt = panel.generar(nombre, datos, await panel.negocio_de_empresa(c))
    destino = c.prompt_destino()
    igual = destino.is_file() and destino.read_text(encoding="utf-8") == prompt  # ya se revisó al guardarlo
    if (not igual and (paso in ESCRITOS or (paso == "personalidad" and (datos["toque"] or datos["nombre_asistente"])))
            and (motivo := await panel.revisar_instrucciones(c, prompt))):
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
    if f.get("accion") == "aplicar":  # guarda y se queda en el paso
        return RedirectResponse(f"{_href(paso)}&ok=aplicado", status_code=303)
    siguiente = ORDEN[ORDEN.index(paso) + 1] if paso != ORDEN[-1] else "resumen"
    return RedirectResponse(f"{_href(siguiente)}&ok=1", status_code=303)


ESTILO = """
body:has(.asis){background:var(--negro)}
.as-main{background:var(--negro);color:var(--claro)}
.asis{display:grid;grid-template-columns:17rem minmax(0,1fr);gap:1.75rem;align-items:start}
.as-pasos{background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;padding:1.25rem;position:sticky;top:1rem}
.as-t{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--gris);margin:0 0 .75rem}
.as-paso{display:flex;gap:.75rem;align-items:center;padding:.65rem .6rem;border-radius:.8rem;text-decoration:none;color:var(--claro)}
.as-paso:hover{background:#2A2C30}
.as-paso.on{background:#2E3035}
.as-paso b{display:block;font-size:.92rem;font-weight:600}
.as-paso small{display:block;font-size:.78rem;color:var(--gris);line-height:1.35}
.as-n{width:1.8rem;height:1.8rem;flex:none;border-radius:50%;border:1px solid #46484F;display:grid;place-items:center;font-size:.8rem;color:var(--gris)}
.as-paso.ok .as-n{background:var(--neon);border-color:var(--neon);color:var(--tinta)}
.as-paso.on .as-n{border-color:var(--acento-claro);color:var(--acento-claro)}
.as-barra{height:.3rem;background:#2E3035;border-radius:9px;margin:1rem 0 .5rem}
.as-barra i{display:block;height:100%;background:var(--acento-claro);border-radius:9px}
.as-nota{display:block;color:var(--gris);font-size:.8rem;line-height:1.5}
.as-num{color:var(--acento-claro);font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;font-weight:600;margin:0}
.as-h{font-size:1.9rem;letter-spacing:-.02em;margin:.3rem 0 .25rem}
.as-baj{color:var(--gris);margin:0 0 1.4rem}
/* Cada paso va en su tarjeta, como en Tu marca y Crear campaña; lo de adentro, un tono más hondo. */
.as-cont{background:var(--grafito);border-radius:1.5rem;padding:2rem 2.25rem;min-width:0}
.as-cont .as-caja,.as-cont .as-pers{background:#1B1C1F}
.as-cont .aviso,.as-main .aviso{background:var(--grafito);color:var(--claro)}.as-cont .aviso{background:#1B1C1F}
.as-caja{background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;padding:1.4rem 1.5rem;margin:0 0 1rem;min-width:0}
fieldset.as-caja legend{float:left;width:100%;padding:0;font-weight:600;font-size:1.02rem}
.as-caja h3{margin:0 0 .25rem;font-size:1.05rem}
.as-caja>label:first-child,.as-caja>.sub:first-child{margin-top:0}.as-main input[type=checkbox]{accent-color:var(--acento-claro)}
.as-main .ayuda{color:var(--gris)}
.as-main label{color:var(--claro)}
.as-main textarea,.as-main input[type=text],.as-main input[type=time],.as-main input[type=email]{background:#18191C;color:var(--claro);border-color:#3A3C42}
.as-main .boton{background:var(--acento);color:var(--tinta)}
.as-main .boton.secundario{background:transparent;color:var(--claro);border-color:#46484F}
.as-main .par{border-color:var(--linea-oscura)}
.as-main .par .quitar{color:var(--gris)}
.as-main .chips label{background:#18191C;border-color:#3A3C42;color:var(--claro)}
.as-main .chips label:has(input:checked){background:var(--acento);color:var(--tinta);border-color:var(--acento)}
.as-main .opcion{background:#18191C;border-color:#3A3C42}
.as-main .opcion span{color:var(--gris)}
.as-main .opcion:has(input:checked){background:rgba(255,138,61,.12);border-color:var(--acento-claro);box-shadow:inset 0 0 0 1px var(--acento-claro)}
.as-main .sub{border-top-color:var(--linea-oscura)}
.as-tarjetas{display:grid;grid-template-columns:repeat(var(--pers-columnas),minmax(0,1fr));gap:var(--hueco);border:0;padding:0;margin:0;min-width:0}
.as-pers{position:relative;margin:0;font-weight:400;display:flex;flex-direction:column;background:var(--grafito);border:1px solid var(--linea-oscura);border-radius:1.25rem;overflow:hidden;cursor:pointer}
.as-pers input{position:absolute;opacity:0;pointer-events:none}
.as-pers:has(input:checked){border-color:var(--acento-claro);box-shadow:0 0 0 3px rgba(255,138,61,.18)}
.as-pers:has(input:focus-visible){outline:2px solid var(--acento-claro);outline-offset:2px}
.as-ck{position:absolute;top:.9rem;right:.9rem;width:1.6rem;height:1.6rem;border-radius:50%;background:var(--acento-claro);color:var(--tinta);font-weight:700;font-size:.85rem;display:none;place-items:center}
.as-pers:has(input:checked) .as-ck{display:grid}
.as-in{display:block;padding:1.15rem 1.2rem 1.25rem}
.as-nom{display:block;font-size:1.05rem;margin-bottom:.35rem;padding-right:2rem}
.as-para{display:block;font-size:.86rem;color:#E4E4E2;line-height:1.5;margin-bottom:.6rem}
.as-toque{margin-top:1.25rem}
.as-opc{font-weight:400;color:var(--gris)}
.as-opt+.as-opt{margin-top:.9rem;padding-top:.9rem;border-top:1px solid var(--linea-oscura)}
.as-optc{display:flex;gap:.7rem;align-items:center;margin:0;cursor:pointer;font-weight:400}
.as-optc input{width:1.15rem;height:1.15rem;flex:none;margin:0;accent-color:var(--acento)}
.as-optb{display:none;margin-top:.7rem}
.as-sub{padding:0 0 .9rem}
.as-pases-t{font-size:var(--t-tarjeta);font-weight:var(--peso-tarjeta);margin:0}
.as-pase{display:grid;grid-template-columns:var(--pase-columnas);column-gap:var(--hueco);align-items:start;padding:var(--hueco) 0;border-top:1px solid var(--linea-oscura)}
.as-pase-on{grid-column:2;grid-row:1}.as-pase-msg{grid-column:3;grid-row:1/span 2}.as-pase-desc{grid-column:1;grid-row:2;margin-top:.6rem}
.as-pase-cab{font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:var(--gris);padding-bottom:.5rem;margin-top:var(--hueco)}
.as-pase-cab+.as-pase{border-top:0}
.as-pase-caso{display:block;font-weight:600;padding-top:.45rem}
.as-pase-on{display:flex;align-items:center;gap:.5rem;margin:0;padding-top:.2rem;font-weight:500;cursor:pointer}
.as-pase-on span{display:none}
.as-pase-msg .as-seg{margin-top:0}
.as-pase-fijo{display:none;margin-top:.6rem}
.as-pase:has(input[value=fijo]:checked) .as-pase-fijo{display:block}
.as-pase:has(.as-pase-on input:not(:checked)) .as-pase-msg{opacity:.4;pointer-events:none}
@media(max-width:900px){.as-pase{grid-template-columns:1fr auto;row-gap:.6rem}.as-pase-desc{grid-column:1/-1;grid-row:2;margin:0}.as-pase-msg{grid-column:1/-1;grid-row:auto}.as-pase-cab{display:none}.as-pase-on span{display:inline}
.as-pase-msg .as-seg{display:flex;flex-wrap:nowrap}.as-pase-msg .as-seg label{flex:1;text-align:center;padding:.5rem .3rem;font-size:.8rem}}
.as-nombre>.as-opt{margin-top:1rem;padding-top:1rem;border-top:1px solid var(--linea-oscura)}
.as-opt:has(.as-optc input:checked) .as-optb{display:block}
.as-lbl{display:block;margin-bottom:.35rem;font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--gris)}
.as-uso{display:block;font-size:.86rem;color:#E4E4E2;line-height:1.45;margin-bottom:.75rem}
.as-uso b{font-weight:600}
.as-dlg,.as-muestra{display:block;background:#18191C;border:1px solid var(--linea-oscura);border-radius:.8rem;padding:.65rem .8rem;font-size:.85rem;line-height:1.45}
.as-dlg .u,.as-muestra .u{display:block;color:var(--gris)}
.as-dlg .a,.as-muestra .a{display:block;color:var(--claro);margin-top:.3rem}
.as-muestra{border-style:dashed;padding:1rem 1.1rem;font-size:.92rem}
.as-muestra b{display:block;margin-bottom:.3rem}
.as-seg{display:inline-flex;flex-wrap:wrap;background:#18191C;border:1px solid var(--linea-oscura);border-radius:.8rem;padding:.25rem;gap:.25rem;margin-top:.4rem}
.as-seg label{position:relative;margin:0;font-weight:500;font-size:.92rem;padding:.5rem 1.1rem;border-radius:.6rem;cursor:pointer;color:#E4E4E2}
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
.as-sw{-webkit-appearance:none;appearance:none;flex:none;width:2.9rem;height:1.75rem;border-radius:999px;background:#3A3C42;position:relative;cursor:pointer;margin:0;transition:background .15s}
.as-sw::after{content:"";position:absolute;left:.2rem;top:.2rem;width:1.35rem;height:1.35rem;border-radius:50%;background:var(--blanco);transition:left .15s}
.as-sw:checked{background:var(--acento-claro)}.as-sw:checked::after{left:1.35rem}
.as-sw:disabled{background:rgba(198,250,44,.4);cursor:default}
.as-sw:focus-visible{outline:2px solid var(--acento-claro);outline-offset:2px}
.as-resumen{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:1rem}
.as-res{margin:0}.as-res ul{margin:.4rem 0 0;padding-left:1.1rem;color:#E4E4E2;font-size:.9rem;line-height:1.7}
.as-top{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.as-ed{font-size:.82rem;color:var(--acento-claro);border:1px solid rgba(255,138,61,.45);border-radius:999px;padding:.25rem .8rem;text-decoration:none}
.as-res+.as-nota,.as-resumen+.as-nota{margin-top:1rem}
.as-pie{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:1.5rem;flex-wrap:wrap}
.as-pie form{margin:0}
@media(max-width:900px){.asis{grid-template-columns:1fr}.as-pasos{position:static;display:flex;gap:.4rem;overflow-x:auto;padding:.75rem}
.as-t,.as-barra,.as-pasos .as-nota,.as-paso small,.as-paso:not(.on) b{display:none}.as-paso{flex:none;padding:.35rem}.as-paso.on{padding-right:.8rem}.as-paso b{font-size:.82rem;white-space:nowrap}}
@media(max-width:560px){.as-cont{padding:1.4rem 1.1rem}.as-h{font-size:1.55rem}.as-caja{padding:1.1rem}.as-seg{display:flex}.as-seg label{flex:1;text-align:center;padding:.5rem .6rem}}
.as-n{color:var(--acento);border-color:rgba(255,138,61,.45)}
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
.as-main .opcion:has(input:checked){background:rgba(255,138,61,.10);border-color:var(--acento);box-shadow:inset 0 0 0 1px var(--acento)}
.as-main .chips label:has(input:checked),.as-seg label:has(input:checked){background:var(--acento);border-color:var(--acento);color:var(--tinta)}
.as-sw:checked{background:var(--acento)}
/* Pasos arriba en horizontal; la barra de avance corre por debajo de los pasos. */
.asis{grid-template-columns:minmax(0,1fr);gap:1.25rem}
.as-pasos{position:relative;top:auto;display:flex;gap:.35rem;align-items:stretch;overflow-x:auto;scrollbar-width:none;padding:.6rem .7rem 1.05rem}
.as-t,.as-pasos .as-nota{display:none}
.as-barra{position:absolute;left:1.3rem;right:1.3rem;bottom:.45rem;margin:0;height:.3rem!important}
.as-paso{flex:1 1 0;min-width:0;line-height:1.2}
.as-paso>span:last-child{min-width:0}
.as-paso small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:900px){.as-paso{flex:none}}
.as-pasos{min-height:5.25rem;box-sizing:border-box}
"""
