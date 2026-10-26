"""Contenido › Campañas › Nueva campaña: el asistente por pasos (sencillo por fuera, dimensiones claras por dentro).

Siete pasos y un resumen: Objetivo, Mensaje (con el producto, si es sobre uno), Enfoque del contenido (de qué
tratan los posts), Formatos, Qué se ve, Estilo y variedad, y Cantidad y fechas.
Sin JavaScript: cada paso es ?paso=N y cada botón (también los del riel de pasos) es un formulario normal que
guarda el borrador (kv "campana_borrador") y lleva al paso que se tocó. En el resumen, "Crear el brief" le pide a
Claude tres ideas de campaña (brief.conceptos); en el paso Brief elige una, la ajusta y "Escribir los posts" arma la
campaña con campanas.armar a partir de ese brief.

Por dentro, lo que eligió se traduce a lo que ya usa campanas.proponer: los temas salen del objetivo y del
mensaje, los formatos y horarios tal cual (o todos, con "Que la IA decida"), y el resto viaja a Claude como
texto (objetivo, mensaje, producto, visual, estilo), entre etiquetas y escapado: lo escribió el cliente.

Las opciones de Objetivo y Mensaje salen del Perfil de empresa (empresa.opciones_campana, que se escriben al
guardar el perfil); si aún no hay, van las de respaldo de aquí. Sin empresa o sin Tu marca, antes de los pasos
se le pide llenarlas: es lo que se usa para que el contenido sea suyo y no genérico.
"""
import json
import time
from datetime import date, timedelta
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from . import brief, campanas, empresa, panel, tools
from .contenido import marca_de, modo_de, sin_plan
from .cuenta import Cuenta, usar

router = APIRouter(prefix="/panel/contenido")
URL = f"{campanas.BASE}/nueva"
BORRADOR = "campana_borrador"

PASOS = ("Objetivo", "Mensaje", "Enfoque del contenido", "Formatos", "Qué se ve", "Estilo y variedad", "Cantidad y fechas")
RESUMEN = len(PASOS) + 1
OCULTOS = {5}  # Qué se ve: fuera del asistente (las imágenes usan lo de siempre: productos, personas, tus fotos)
VISIBLES = [i for i in range(1, RESUMEN) if i not in OCULTOS]


def vecino(n: int, paso: int) -> int:
    """El paso visible siguiente (paso=1) o anterior (paso=-1); RESUMEN después del último."""
    i = n + paso
    while i in OCULTOS:
        i += paso
    return i

BRIEF = RESUMEN + 1  # después del resumen: elegir la idea de la campaña y ajustar su brief
MAX_MENSAJES = 2
MAX_TEXTO, MAX_CONDICION = 400, 120

# Las imágenes de las tarjetas: igbot/static/tarjetas/campana/<sección>-<clave>.webp (el usuario las hace aparte).
# Mientras no estén, cada tarjeta lleva su color de respaldo.
TARJETAS = campanas.panel.STATIC / "tarjetas" / "campana"

# enfoque del contenido: (título, descripción, tema de campanas.TEMAS, color de respaldo)
ENFOQUES = {
    "consejos": ("Consejos e ideas útiles", "Enseña algo práctico que la audiencia pueda aplicar.",
                 "ensena", "#2d2640"),
    "beneficios": ("Beneficios y ventajas", "Destaca qué gana el cliente con tu producto o servicio.",
                   "beneficios", "#3a2f25"),
    "problemas": ("Problemas y soluciones", "Conecta con una necesidad y muestra cómo puedes resolverla.",
                  "problemas", "#1f3029"),
    "datos": ("Datos curiosos y descubrimientos", "Despierta curiosidad con datos, mitos o información inesperada.",
              "dato", "#26262a"),
    "demostraciones": ("Demostraciones y ejemplos", "Explica cómo funciona algo o muestra un caso concreto.",
                       "producto", "#33302a"),
    "historias": ("Historias y situaciones cotidianas", "Presenta escenas con las que tus clientes puedan"
                  " identificarse.", "historias", "#4a2312"),
    "ofertas": ("Ofertas y promociones", "Comunica descuentos, paquetes, novedades o incentivos de compra.",
                "promo", "#36203E"),
}
MAX_ENFOQUES = campanas.MAX_TEMAS


def sugeridos(obj: dict) -> set[str]:
    """Los enfoques que van con el objetivo (los temas que ya le servían)."""
    temas = OBJETIVOS[base_de(obj, OBJETIVOS, PALABRAS_OBJETIVO)][2]
    return {k for k, v in ENFOQUES.items() if v[2] in temas}


# objetivo: (título, descripción, temas que más le sirven, color de respaldo)
OBJETIVOS = {
    "atraer": ("Atraer clientes nuevos", "Que más gente descubra tu negocio y se anime a escribirte o visitarte.",
               ("marca", "producto", "ensena", "detras"), "#3a2f25"),
    "vender": ("Vender más", "Más pedidos, reservas o compras de lo que ya ofreces.",
               ("producto", "promo", "clientes", "ensena"), "#4a2312"),
    "interaccion": ("Más interacción", "Más comentarios, guardados y mensajes. Cada post trae su regla de respuesta.",
                    ("pregunta", "dato", "ensena", "producto"), "#2d2640"),
    "educar": ("Enseñar y resolver dudas", "Que tu audiencia entienda lo que haces y sepa elegir.",
               ("ensena", "dato", "producto", "pregunta"), "#1f3029"),
    "comunidad": ("Crear comunidad", "Que tus clientes participen, se reconozcan y vuelvan.",
                  ("pregunta", "detras", "clientes", "marca"), "#33302a"),
    "marca": ("Fortalecer tu marca", "Ser el negocio que la gente reconoce y recomienda.",
              ("marca", "detras", "clientes", "dato"), "#26262a"),
}
# Las opciones del Perfil de empresa traen sus propias claves: se reconocen por sus palabras.
PALABRAS_OBJETIVO = (
    ("interaccion", ("interac", "coment", "convers", "particip", "engag")),
    ("vender", ("vend", "compr", "pedido", "reserv", "promo", "descuent", "cita", "agend", "lanz")),
    ("educar", ("enseñ", "educ", "duda", "explic", "aprend")),
    ("comunidad", ("comunidad", "fidel", "vuelv", "lealtad")),
    ("marca", ("marca", "reconoc", "confianza", "prestigio", "posicion")),
    ("atraer", ("atraer", "nuevos", "descubr", "seguidor", "visita", "alcance", "conocer")),
)
# mensaje: (título, descripción, temas)
MENSAJES = {
    "distinto": ("Lo que te hace distinto", "Por qué te eligen a ti y no a otro.", ("marca",)),
    "producto": ("Un producto o servicio", "Enfócate en algo concreto de lo que vendes.", ("producto",)),
    "consejos": ("Consejos y respuestas", "Lo que tus clientes siempre preguntan, contado fácil.", ("ensena", "dato")),
    "humano": ("El lado humano", "Tu equipo, tu proceso y los momentos reales del día.", ("detras",)),
    "novedad": ("Una novedad o temporada", "Lo nuevo, lo de temporada o lo que está por llegar.", ("fecha", "promo")),
    "clientes": ("Lo que dicen tus clientes", "Reseñas y experiencias reales, sin inventar testimonios.", ("clientes",)),
}
PALABRAS_MENSAJE = (
    ("producto", ("producto", "servicio", "menú", "catálogo", "platillo")),
    ("clientes", ("reseña", "testimon", "opinion", "opinión", "dicen")),
    ("humano", ("equipo", "proceso", "detrás", "humano", "cómo trabaj", "historia")),
    ("consejos", ("consejo", "tip", "duda", "pregunta", "guía", "cómo")),
    ("novedad", ("nuevo", "novedad", "temporada", "lanz")),
    ("distinto", ("distint", "diferen", "elig", "propuesta", "calidad")),
)
# Sin opciones del Perfil de empresa: qué mensajes van con cada objetivo de respaldo.
MENSAJES_DE = {"atraer": ("distinto", "producto", "humano", "clientes"),
               "vender": ("producto", "novedad", "clientes", "distinto"),
               "interaccion": ("consejos", "producto", "humano", "clientes"),
               "educar": ("consejos", "producto", "distinto", "humano"),
               "comunidad": ("humano", "clientes", "consejos", "novedad"),
               "marca": ("distinto", "humano", "clientes", "consejos")}
ACCIONES = {"lanzar": "Lanzarlo", "conocer": "Darlo a conocer", "promo": "Promoción con descuento",
            "impulsar": "Volver a impulsarlo"}
FORMATOS = {  # clave: (título, descripción, color)
    "foto": ("Foto protagonista", "Una fotografía que destaca el producto, servicio o escena sin textos gráficos"
             " añadidos.", "#3a2f25"),
    "titular": ("Foto con titular", "Una fotografía acompañada de una frase breve que destaca la idea principal.",
                "#33302a"),
    "texto": ("Diseño de texto", "Una frase, consejo o mensaje destacado con los colores y la tipografía de la marca.",
              "#26262a"),
    "collage": ("Collage", "Varias fotografías reunidas en una composición para mostrar opciones, detalles o"
                " distintos aspectos de algo.", "#1f3029"),
}
VISUAL = {
    "productos": ("Tus productos", "Lo que vendes, sus detalles y cómo se ve en uso.", "#3a2f25"),
    "personas": ("Personas y momentos", "Clientes, tu equipo, manos trabajando, gente disfrutando.", "#33302a"),
    "espacio": ("Tu espacio", "Tu local, tu oficina, tu taller, el ambiente.", "#1f3029"),
    "procesos": ("Procesos", "Cómo se prepara, se hace, se empaca o se entrega.", "#26262a"),
    "graficos": ("Ilustraciones y gráficos", "Íconos y diagramas para explicar ideas.", "#2d2640"),
    "propias": ("Tus propias fotos", "Usamos tus fotos de Tu marca y del producto como referencia.", "#4a2312"),
}
TEXTO = {  # clave: (título, descripción, color)
    "minimo": ("Texto mínimo", "Una frase muy breve o unas pocas palabras.", "#3a2f25"),
    "titular": ("Un titular", "Una frase principal que destaque el mensaje.", "#33302a"),
    "info": ("Más información", "Algunos datos clave, como precio, horario o pasos, fáciles de leer.", "#1f3029"),
}
VARIEDAD = {
    "reconocible": ("Consistente", "Mantén una identidad visual uniforme. Las publicaciones comparten colores,"
                    " iluminación y estilo fotográfico para que tu perfil se vea ordenado y reconocible.", "#33302a"),
    "coherencia": ("Equilibrado", "Combina diferentes escenas y composiciones sin perder armonía. Ideal para mostrar"
                   " tus productos, personas y espacios manteniendo una imagen de marca coherente.", "#3a2f25"),
    "diverso": ("Diverso", "Haz que cada publicación se sienta diferente. Alterna escenarios, encuadres, colores y"
                " protagonistas para crear un perfil dinámico y lleno de contrastes.", "#1f3029"),
    "experimental": ("Creativo", "Explora ideas inesperadas y composiciones originales. Dale libertad a la IA para"
                     " proponer imágenes llamativas, experimentar con conceptos y sorprender a tu audiencia.",
                     "#4a2312"),
}
ARMADO = {"plantilla": "Con tu plantilla", "libre": "Diseño libre"}
HORARIOS = {**{k: f"{v[0]} · {v[1]}" for k, v in campanas.HORARIOS.items()}, "ia": "Que la IA decida"}
DIAS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
MAX_POSTS = 30


# --- el borrador ---

def inicial(marca: dict) -> dict:
    lunes = campanas.lunes_que_viene()
    return {"objetivo": "", "mensajes": [], "producto": "", "accion": "conocer", "descuento": "", "desde": "",
            "hasta": "", "condicion": "", "texto": "", "con_producto": False, "con_texto": False, "preguntan": True, "funciona": False,
            "mezcla": campanas.MEZCLA_INICIAL, "formatos": ["foto", "titular"],
            "visual": ["productos", "personas", "propias"], "textos_img": ["titular"],
            "variedad": "coherencia", "armado": modo_de(marca), "nombre": "", "inicio": lunes.isoformat(),
            "semanas": 2, "n_posts": 8, "dias": [0, 1, 2, 3, 4], "horarios": ["manana", "noche"], "hechos": []}


async def leer(c: Cuenta) -> dict:
    guardado = json.loads(await c.store.get(BORRADOR) or "{}")
    return {**inicial(await marca_de(c)), **guardado}


async def escribir(c: Cuenta, b: dict) -> None:
    await c.store.set(BORRADOR, json.dumps(b, ensure_ascii=False))


def de_campana(camp: dict) -> dict:
    """Para "Editar estrategia": el borrador con lo que eligió para esa campaña (si se armó con el asistente)."""
    b = dict(camp.get("asistente") or {})
    b.update(nombre=camp.get("nombre", ""), inicio=camp["inicio"], semanas=camp.get("semanas", 2),
             n_posts=camp.get("n_posts") or len(camp.get("posts", [])) or 8, cid=camp["id"],
             hechos=list(range(1, RESUMEN)))
    if camp.get("brief") and not b.get("brief"):  # campañas armadas antes de guardar las tres ideas
        b["brief"] = {"conceptos": [camp["brief"]], "elegido": 0}
    if not b.get("dias"):
        b["dias"] = list(campanas.PUBLICAR.get(camp.get("publicar"), campanas.PUBLICAR["lun-sab"])[1])
    return b


# --- las opciones (del Perfil de empresa o las de respaldo) ---

async def opciones(c: Cuenta, objetivo: str = "") -> tuple[list[dict], list[dict]]:
    """Objetivos y los mensajes del objetivo elegido: [{k, titulo, descripcion, ejemplo?}]. Salen ya escritos del
    Perfil de empresa (empresa.generar_opciones, al guardarlo); aquí no se le pregunta nada a Claude."""
    if datos := await empresa.opciones_campana(c):
        if datos.get("version") != empresa.VERSION_OPCIONES:
            empresa.programar_opciones(c)  # las de antes no traían mensajes por objetivo: se rehacen una vez
        return datos["objetivos"][:6], empresa.mensajes_de(datos, objetivo)[:6]
    objetivos = [{"k": k, "titulo": v[0], "descripcion": v[1]} for k, v in OBJETIVOS.items()]
    claves = MENSAJES_DE.get(objetivo, tuple(MENSAJES))
    return objetivos, [{"k": k, "titulo": MENSAJES[k][0], "descripcion": MENSAJES[k][1]} for k in claves]


def base_de(op: dict, propias: dict, palabras: tuple) -> str:
    """A cuál de las de respaldo se parece una opción (para saber qué temas le sirven)."""
    if op.get("k") in propias:
        return op["k"]
    texto = f"{op.get('titulo', '')} {op.get('descripcion', '')}".lower()
    return next((k for k, claves in palabras if any(p in texto for p in claves)), next(iter(propias)))


async def productos(c: Cuenta) -> list[dict]:
    return [p for p in await empresa.productos(c) if isinstance(p, dict) and p.get("id") and p.get("nombre")]


def precio_de(p: dict) -> str:
    return empresa.precio_texto(p)


async def lista_para_empezar(c: Cuenta) -> tuple[bool, bool]:
    """(empresa lista, marca lista): sin las dos no se puede crear una campaña."""
    return await empresa.empresa_lista(c), await panel.marca_lista(c)


# --- de lo que eligió a la campaña ---

async def a_campana(c: Cuenta, b: dict) -> dict:
    """Lo que necesita campanas.proponer, sacado del borrador."""
    objetivos, mensajes = await opciones(c, b["objetivo"])
    obj = next((o for o in objetivos if o["k"] == b["objetivo"]), objetivos[0])
    base = base_de(obj, OBJETIVOS, PALABRAS_OBJETIVO)
    msgs = [m for m in mensajes if m["k"] in b["mensajes"]]
    temas = list(OBJETIVOS[base][2])
    for m in msgs:
        for t in MENSAJES[base_de(m, MENSAJES, PALABRAS_MENSAJE)][2]:
            if t not in temas:
                temas.insert(0, t)
    if enfoques := [ENFOQUES[k][2] for k in b.get("enfoques", []) if k in ENFOQUES]:
        temas = enfoques + [t for t in temas if t not in enfoques][:max(0, campanas.MIN_TEMAS - len(enfoques))]
    temas = temas[:campanas.MAX_TEMAS]
    with usar(c):
        fotos = len(campanas.fotos_marca(c, await marca_de(c)))
    formatos = [k for k in b["formatos"] if k in campanas.FORMATOS]
    if "ia" in b["formatos"] or not formatos:
        formatos = [k for k in campanas.FORMATOS if k != "collage" or fotos >= campanas.MIN_FOTOS_COLLAGE]
    horarios = [k for k in b["horarios"] if k in campanas.HORARIOS]
    if "ia" in b["horarios"] or not horarios:
        horarios = list(campanas.HORARIOS)
    prod = next((p for p in await productos(c) if p["id"] == b["producto"]), None)
    producto = ""
    if prod:
        producto = f"{prod['nombre']}" + (f" ({precio_de(prod)})" if precio_de(prod) else "")
        if prod.get("descripcion"):
            producto += f": {prod['descripcion']}"
        producto += f". Qué quiere: {ACCIONES.get(b['accion'], ACCIONES['conocer']).lower()}"
        if b["accion"] == "promo" and b["descuento"]:
            producto += f", {b['descuento']} % de descuento"
            if b["desde"] and b["hasta"]:
                producto += f" del {campanas.fecha_larga(b['desde'])} al {campanas.fecha_larga(b['hasta'])}"
            if b["condicion"]:
                producto += f" ({b['condicion']})"
        producto += "."
    visual = ", ".join(VISUAL[k][0] for k in b["visual"] if k in VISUAL)
    titular = (f"Texto en las fotos con titular (se alternan): {', '.join(TEXTO[k][0].lower() for k in textos(b))}. "
               if "titular" in b["formatos"] else "")
    estilo = (f"{titular}Variedad visual: {VARIEDAD[b['variedad']][0]}"
              f" ({VARIEDAD[b['variedad']][1]})")
    inicio = date.fromisoformat(b["inicio"])
    return {"nombre": b["nombre"].strip() or f"{obj['titulo']} · {campanas.fecha_larga(b['inicio'])}"[:campanas.MAX_NOMBRE],
            "inicio": inicio.isoformat(), "semanas": b["semanas"], "publicar": "lun-sab",
            "dias_semana": sorted(b["dias"]), "n_posts": b["n_posts"], "tema": b["texto"],
            "modo": "explorar", "mezcla": b["mezcla"], "temas": temas, "formatos": formatos, "horarios": horarios,
            "clientes": False,  # Ideas sugeridas (lo que preguntan, repetir lo que funcionó) está fuera por ahora
            "objetivo": f"{obj['titulo']}: {obj.get('descripcion', '')}".strip(": "),
            "mensaje": "; ".join(f"{m['titulo']}: {m.get('descripcion', '')}" for m in msgs),
            "producto": producto, "producto_id": prod["id"] if prod else "", "visual": visual, "estilo": estilo,
            "fotos_propias": "propias" in b["visual"], "modo_img": b["armado"], "regla": base == "interaccion",
            "ves": [[k, f"{VISUAL[k][0]}: {VISUAL[k][1]}"] for k in b["visual"] if k in VISUAL and k != "propias"],
            "variedad": b["variedad"],
            "semilla": b.get("semilla") or 0,
            "asistente": {k: v for k, v in b.items() if k not in ("cid", "hechos")}}


# --- guardar cada paso ---

def _lista(f, nombre: str, validas) -> list[str]:
    return [v for v in f.getlist(nombre) if v in validas]


def _fecha(texto: str) -> str:
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return ""


def del_form(f, paso: int, b: dict, objetivos: list[dict], mensajes: list[dict], prods: list[dict]) -> str:
    """Pasa al borrador lo que vino del paso; devuelve '' o lo que falta (el paso no se da por hecho)."""
    g = lambda k: str(f.get(k, "")).strip()  # noqa: E731
    if paso == 1:
        if g("objetivo") in {o["k"] for o in objetivos} and g("objetivo") != b["objetivo"]:
            b["objetivo"] = g("objetivo")
            b["mensajes"] = []  # los mensajes dependen del objetivo: se eligen de nuevo
            b["hechos"] = [h for h in b.get("hechos", []) if h != 2]
        return "" if b["objetivo"] else "Elige qué quieres lograr."
    if paso == 2:
        b["mensajes"] = _lista(f, "mensajes", {m["k"] for m in mensajes})[:MAX_MENSAJES]
        b["con_producto"], b["con_texto"] = g("con_producto") == "1", g("con_texto") == "1"
        b["producto"] = g("producto") if b["con_producto"] and g("producto") in {p["id"] for p in prods} else ""
        b["accion"] = g("accion") if g("accion") in ACCIONES else "conocer"
        b["descuento"] = g("descuento") if g("descuento").isdigit() and 1 <= int(g("descuento")) <= 90 else ""
        b["desde"], b["hasta"] = _fecha(g("desde")), _fecha(g("hasta"))
        b["condicion"], b["texto"] = g("condicion")[:MAX_CONDICION], g("texto")[:MAX_TEXTO] if b["con_texto"] else ""
        if not b["mensajes"]:
            return "Elige al menos un mensaje."
        if b["producto"] and b["accion"] == "promo" and not b["descuento"]:
            return "Escribe el porcentaje de descuento (de 1 a 90)."
        if b["desde"] and b["hasta"] and b["hasta"] < b["desde"]:
            return "La promoción no puede terminar antes de empezar."
        return ""
    if paso == 3:
        b["enfoques"] = _lista(f, "enfoques", set(ENFOQUES))[:MAX_ENFOQUES]
        return "" if b["enfoques"] else "Elige al menos un enfoque."
    if paso == 4:
        b["formatos"] = _lista(f, "formatos", FORMATOS)
        b["textos_img"] = _lista(f, "textos_img", TEXTO)
        if "titular" in b["formatos"] and not b["textos_img"]:
            return "Elige cuánto texto llevan las fotos con titular."
        return "" if b["formatos"] else "Elige al menos un formato."
    if paso == 5:
        b["visual"] = _lista(f, "visual", VISUAL)
        return "" if b["visual"] else "Elige al menos una cosa que se vea."
    if paso == 6:
        b["variedad"] = g("variedad") if g("variedad") in VARIEDAD else b["variedad"]
        return ""
    if paso == 7:
        b["nombre"] = g("nombre")[:campanas.MAX_NOMBRE]
        b["inicio"] = _fecha(g("inicio")) or b["inicio"]
        b["semanas"] = int(g("semanas")) if g("semanas").isdigit() and int(g("semanas")) in campanas.SEMANAS else 2
        b["dias"] = sorted({int(d) for d in f.getlist("dias") if d.isdigit() and 0 <= int(d) <= 6})
        b["horarios"] = _lista(f, "horarios", HORARIOS)
        n = int(g("n_posts")) if g("n_posts").isdigit() else 0
        b["n_posts"] = max(1, min(n, MAX_POSTS)) if n else b["n_posts"]
        if not b["dias"]:
            return "Elige al menos un día para publicar."
        if not b["horarios"]:
            return "Elige al menos un horario."
        dias = len(campanas.dias_de({"inicio": b["inicio"], "semanas": b["semanas"], "dias_semana": b["dias"]}))
        if b["n_posts"] > dias * campanas.MAX_POSTS_DIA:
            return (f"En {b['semanas']} semana{'s' if b['semanas'] > 1 else ''} con esos días caben hasta"
                    f" {dias * campanas.MAX_POSTS_DIA} posts (dos por día). Agrega días o semanas, o pide menos.")
        return ""
    return ""


def faltan(b: dict) -> list[int]:
    return [i for i in VISIBLES if i not in b.get("hechos", [])]


# --- páginas ---

def img(seccion: str, clave: str, color: str, texto: str = "", alta: bool = False) -> str:
    """alta: la imagen completa, vertical (2:3); si no, 16:10."""
    archivo = f"{seccion}-{clave}.webp"
    fondo = f"{color} url(/panel/static/tarjetas/campana/{archivo})" if (TARJETAS / archivo).is_file() else color
    return f'<span class="wiz-img{" alta" if alta else ""}" style="--fondo:{fondo}">{"" if (TARJETAS / archivo).is_file() else escape(texto)}</span>'


def op(nombre: str, valor: str, titulo: str, desc: str, tipo: str = "radio", marcada: bool = False,
       imagen: str = "", em: str = "", apagada: bool = False) -> str:
    return (f'<label class="wiz-op{" apagada" if apagada else ""}">'
            f'<input type="{tipo}" name="{nombre}" value="{escape(valor)}"{" checked" if marcada else ""}'
            f'{" disabled" if apagada else ""}>{imagen}'
            f'<span class="cuerpo"><b>{escape(titulo)}</b><small>{escape(desc)}</small>'
            f'{f"<em>{em}</em>" if em else ""}</span></label>')


def textos(b: dict) -> list[str]:
    """Cuánto texto llevan las fotos con titular (borradores de antes: una sola opción)."""
    return [k for k in b.get("textos_img") or [b.get("texto_img", "titular")] if k in TEXTO] or ["titular"]


def abre(nombre: str, titulo: str, marcada) -> str:
    """Un check que, al marcarlo, abre lo que sigue en el bloque (sin JS: .wiz-bloque:has)."""
    return (f'<label class="wiz-abre"><input type="checkbox" name="{nombre}" value="1"{" checked" if marcada else ""}>'
            f'<span>{escape(titulo)}</span></label><div class="wiz-si abre">')


def chip(nombre: str, valor: str, texto: str, marcada: bool, tipo: str = "radio") -> str:
    return (f'<label class="wiz-chip"><input type="{tipo}" name="{nombre}" value="{escape(str(valor))}"'
            f'{" checked" if marcada else ""}><span>{escape(texto)}</span></label>')


def riel(b: dict, actual: int) -> str:
    """Los pasos a la izquierda: botones del mismo formulario, así saltar a otro guarda el que está abierto."""
    hechos = set(b.get("hechos", []))
    filas = "".join(
        f'<button class="wiz-paso{" hecho" if i in hechos and i != actual else ""}{" actual" if i == actual else ""}"'
        f' name="ir" value="{i}"><b>{"✓" if i in hechos and i != actual else n}</b>{PASOS[i - 1]}</button>'
        for n, i in enumerate(VISIBLES, 1))
    listo = not faltan(b)
    return (f'<nav class="wiz-pasos" aria-label="Pasos de la campaña"><p>Tu campaña</p>{filas}<div class="sep"></div>'
            f'<button class="wiz-paso{" actual" if actual == RESUMEN else ""}{"" if listo else " apagado"}" name="ir"'
            f' value="{RESUMEN}"><b>✓</b>Resumen</button>'
            + (f'<button class="wiz-paso{" actual" if actual == BRIEF else ""}" name="ir" value="{BRIEF}"><b>✓</b>Brief'
               "</button>" if b.get("brief") else "") + "</nav>")


def cab(n: int, titulo: str, bajada: str) -> str:
    v = VISIBLES.index(n) + 1  # el número que ve: sin contar los pasos ocultos
    return (f'<div class="wiz-cab"><p class="ante">Paso {v} de {len(VISIBLES)} · {PASOS[n - 1]}</p><h2>{titulo}</h2>'
            f'<p class="baj">{bajada}</p><div class="wiz-barra"><i style="width:{round(100 * v / len(VISIBLES))}%"></i>'
            "</div></div>")


def pie(n: int) -> str:
    siguiente = "Ver resumen" if n == VISIBLES[-1] else "Continuar"
    return ('<div class="wiz-pie">'
            + (f'<button class="boton secundario" name="ir" value="{vecino(n, -1)}">‹ Anterior</button>'
               if n > VISIBLES[0] else f'<a class="boton secundario" href="{campanas.BASE}">Cancelar</a>')
            + f'<span>Paso {VISIBLES.index(n) + 1} de {len(VISIBLES)}</span>'
            f'<button class="boton" name="ir" value="{vecino(n, 1)}">{siguiente} ›</button></div>')


def nota(titulo: str, texto: str) -> str:
    return f'<p class="wiz-nota"><b>{titulo}</b> {texto}</p>'


def paso_1(b: dict, objetivos: list[dict]) -> str:
    ops = "".join(op("objetivo", o["k"], o["titulo"], o.get("descripcion", ""), marcada=o["k"] == b["objetivo"])
                  for o in objetivos)  # sin miniatura: cada opción es de su negocio
    return (cab(1, "¿Qué quieres lograr con esta campaña?",
                "Elige el resultado que buscas. Las opciones salen de tu perfil de empresa, así que hablan de tu"
                " negocio.")
            + f'<div class="wiz-ops">{ops}</div>'
            + pie(1))


def paso_2(b: dict, mensajes: list[dict], prods: list[dict], objetivos: list[dict]) -> str:
    ops = "".join(op("mensajes", m["k"], m["titulo"], m.get("descripcion", ""), "checkbox", m["k"] in b["mensajes"])
                  for m in mensajes)
    opciones_prod = '<option value="">Ninguno en especial</option>' + "".join(
        f'<option value="{escape(p["id"])}"{" selected" if p["id"] == b["producto"] else ""}>{escape(p["nombre"])}'
        + (f" · {escape(precio_de(p))}" if precio_de(p) else "") + "</option>" for p in prods)
    producto = (
        '<div class="wiz-bloque prod-cam">' + abre("con_producto", "¿Es sobre un producto?", b.get("con_producto") or b["producto"])
        + ('<p class="ayuda">Sale de los productos de tu perfil de empresa, con su precio y sus fotos.</p>'
           f'<div class="wiz-campos"><div><label for="w-prod">Producto</label><select id="w-prod" name="producto">'
           f"{opciones_prod}</select></div></div>"
           '<label class="t" style="margin-top:1rem">¿Qué quieres hacer con él?</label><div class="wiz-chips">'
           + "".join(chip("accion", k, v, b["accion"] == k) for k, v in ACCIONES.items())
           + '</div><div class="wiz-si promo wiz-campos">'
           f'<div><label for="w-desc">Descuento (%)</label><input type="number" id="w-desc" name="descuento" min="1"'
           f' max="90" value="{escape(b["descuento"])}" placeholder="20"></div>'
           f'<div><label for="w-desde">Desde</label><input type="date" id="w-desde" name="desde" value="{b["desde"]}"></div>'
           f'<div><label for="w-hasta">Hasta</label><input type="date" id="w-hasta" name="hasta" value="{b["hasta"]}"></div>'
           f'<div><label for="w-cond">Condición (opcional)</label><input type="text" id="w-cond" name="condicion"'
           f' maxlength="{MAX_CONDICION}" value="{escape(b["condicion"])}" placeholder="Ej. solo en sucursal"></div></div>'
           if prods else
           '<p class="ayuda">Agrega tus productos en tu <a href="/panel/empresa">perfil de empresa</a> y podrás elegir'
           " uno para lanzarlo, darlo a conocer o hacerle una promoción.</p>")
        + "</div></div>")
    obj = next((o["titulo"] for o in objetivos if o["k"] == b["objetivo"]), "")
    return (cab(2, "¿Qué quieres comunicar?",
                (f"Ideas pensadas para «{escape(obj)}». " if obj else "")
                + f"Es el tema de la campaña: lo que la gente se va a llevar en la cabeza. Elige hasta {MAX_MENSAJES}.")
            + f'<div class="wiz-ops">{ops}</div>'
            + '<div class="wiz-bloque">' + abre("con_texto", "Cuéntalo en tus palabras", b.get("con_texto") or b["texto"])
            + '<p class="ayuda">Un dato, una fecha o una idea que no debe faltar.</p>'
            f'<textarea name="texto" rows="2" maxlength="{MAX_TEXTO}" aria-label="Tu idea" placeholder="Ej. Temporada'
            f' de otoño: queremos más visitas entre semana.">{escape(b["texto"])}</textarea></div></div>' + producto
            + pie(2))


def paso_3(b: dict, objetivos: list[dict]) -> str:
    obj = next((o for o in objetivos if o["k"] == b["objetivo"]), None)
    van = sugeridos(obj) if obj else set()
    ops = "".join(op("enfoques", k, v[0], v[1], "checkbox", k in b.get("enfoques", []),
                     img("enfoque", k, v[3], v[0]), em="Va con tu objetivo" if k in van else "")
                  for k, v in ENFOQUES.items())
    return (cab(3, "¿De qué van a tratar tus posts?",
                f"Elige uno o varios (hasta {MAX_ENFOQUES}). Los posts de la campaña se reparten entre ellos.")
            + f'<div class="wiz-ops tres">{ops}</div>'
            + pie(3))

def paso_4(b: dict, fotos: int) -> str:
    sin_collage = fotos < campanas.MIN_FOTOS_COLLAGE
    ops = ""
    for k, (t, d, color) in FORMATOS.items():
        usa = campanas.FORMATOS[k][1] if k in campanas.FORMATOS else None
        em = ("Sube 2 fotos en Tu marca" if k == "collage" and sin_collage else
              "" if usa is None else "1 imagen" if usa else "No usa tu cupo")
        ops += op("formatos", k, t, d, "checkbox", k in b["formatos"] and not (k == "collage" and sin_collage),
                  img("formato", k, color, t, alta=True), em, apagada=k == "collage" and sin_collage)
    return (cab(4, "¿Qué formatos te gustan?",
                "Elige uno o varios. Se reparten parejo para que veas cuál le funciona mejor a tu audiencia.")
            + f'<div class="formatos-cam"><div class="wiz-ops cuatro">{ops}</div>'
            '<div class="wiz-si titular"><h3 class="wiz-sub">Texto dentro de la imagen</h3><p class="ayuda">Para'
            ' Foto con titular: elige una, dos o las tres. No es el pie de foto: es lo que va escrito sobre la'
            ' imagen.</p>'
            '<div class="wiz-ops tres">'
            + "".join(op("textos_img", k, t, d, "checkbox", k in textos(b), img("texto", k, color, t, alta=True))
                      for k, (t, d, color) in TEXTO.items()) + "</div></div></div>"
            + nota("Carrusel y reel", "llegarán después. Las historias de Instagram también están en camino.") + pie(4))


def paso_5(b: dict) -> str:
    ops = "".join(op("visual", k, t, d, "checkbox", k in b["visual"], img("visual", k, color, t))
                  for k, (t, d, color) in VISUAL.items())
    return (cab(5, "¿Qué quieres que se vea en las imágenes?",
                "No es el mensaje ni el objetivo: son las cosas que vamos a ver en cada foto. Elige las que quieras.")
            + f'<div class="wiz-ops tres">{ops}</div>'
            + nota("Tip:", "si quieres que se vea tu producto real y no uno parecido, sube sus fotos en tu perfil de"
                   " empresa. Las usamos como referencia en cada imagen.") + pie(5))


def paso_6(b: dict) -> str:
    variedad = "".join(op("variedad", k, t, d, marcada=b["variedad"] == k, imagen=img("variedad", k, color, t, alta=True),
                          em="Recomendado" if k == "coherencia" else "")
                       for k, (t, d, color) in VARIEDAD.items())
    return (cab(6, "¿Cómo quieres que se vea esta campaña?",
                "Solo cambia esta campaña. Tus colores, tu tipografía y tu logo siguen siendo los de Tu marca.")
            + '<h3 class="wiz-sub">Variedad visual</h3><p class="ayuda">Qué tan distintas se ven las fotos entre'
            ' sí: el lugar, el encuadre y la luz.</p>'
            f'<div class="wiz-ops cuatro">{variedad}</div>' + pie(6))


def paso_7(b: dict) -> str:
    semanas = "".join(f'<option value="{n}"{" selected" if b["semanas"] == n else ""}>{n} semana{"s" if n > 1 else ""}'
                      "</option>" for n in campanas.SEMANAS)
    return (cab(7, "¿Cuánto y cuándo?", "Puedes empezar con pocos posts y estirar la campaña después desde el"
                " calendario.")
            + '<div class="wiz-campos">'
            f'<div><label for="w-nombre">Nombre de la campaña</label><input type="text" id="w-nombre" name="nombre"'
            f' maxlength="{campanas.MAX_NOMBRE}" value="{escape(b["nombre"])}" placeholder="Ej. Otoño en tu negocio"></div>'
            f'<div><label for="w-inicio">Empieza</label><input type="date" id="w-inicio" name="inicio"'
            f' value="{b["inicio"]}"></div>'
            f'<div><label for="w-semanas">Duración</label><select id="w-semanas" name="semanas">{semanas}</select></div>'
            f'<div><label for="w-n">Número de posts</label><input type="number" id="w-n" name="n_posts" min="1"'
            f' max="{MAX_POSTS}" value="{b["n_posts"]}"></div></div>'
            '<h3 class="wiz-sub">Días en que publicas</h3><p class="ayuda">Los posts se reparten en estos días. Si son'
            ' más posts que días, algunos días llevan dos.</p><div class="wiz-chips">'
            + "".join(chip("dias", i, d, i in b["dias"], "checkbox") for i, d in enumerate(DIAS)) + "</div>"
            '<h3 class="wiz-sub">Horario</h3><p class="ayuda">Elige uno o varios para comparar.</p><div class="wiz-chips">'
            + "".join(chip("horarios", k, v, k in b["horarios"], "checkbox") for k, v in HORARIOS.items()) + "</div>"
            + pie(7))


async def resumen(c: Cuenta, b: dict, objetivos: list[dict], mensajes: list[dict], prods: list[dict]) -> str:
    obj = next((o["titulo"] for o in objetivos if o["k"] == b["objetivo"]), "Sin elegir")
    msgs = " · ".join(m["titulo"] for m in mensajes if m["k"] in b["mensajes"]) or "Sin elegir"
    prod = next((p for p in prods if p["id"] == b["producto"]), None)
    producto = "Ninguno en especial"
    if prod:
        producto = f"{prod['nombre']} · {ACCIONES[b['accion']]}"
        if b["accion"] == "promo" and b["descuento"]:
            producto += f" {b['descuento']} %" + (f" del {campanas.fecha_larga(b['desde'])} al"
                                                   f" {campanas.fecha_larga(b['hasta'])}" if b["desde"] and b["hasta"] else "")
    enfoques = " · ".join(ENFOQUES[k][0] for k in b.get("enfoques", []) if k in ENFOQUES) or "Según tu objetivo"
    ini = date.fromisoformat(b["inicio"])
    fin = ini + timedelta(days=7 * b["semanas"] - 1)
    dias = ", ".join(DIAS[d] for d in b["dias"])
    horas = " y ".join(HORARIOS[h].split(" · ")[-1] for h in b["horarios"])
    imagenes_ = 0
    formatos = [k for k in b["formatos"] if k in campanas.FORMATOS]
    if "ia" in b["formatos"] or not formatos:
        formatos = [k for k in campanas.FORMATOS if k != "collage"]
    imagenes_ = round(b["n_posts"] * sum(campanas.FORMATOS[k][1] for k in formatos) / len(formatos))
    bloques = [
        ("Objetivo", obj, 1, False), ("Mensaje", msgs + (f" · «{b['texto']}»" if b["texto"] else ""), 2, False),
        ("Producto", producto, 2, True), ("Enfoque del contenido", enfoques, 3, False),
        ("Formatos", " · ".join(FORMATOS[k][0] for k in b["formatos"] if k in FORMATOS)
         + (f" ({', '.join(TEXTO[k][0].lower() for k in textos(b))})" if "titular" in b["formatos"] else ""), 4, False),
        ("Estilo y variedad", VARIEDAD[b["variedad"]][0], 6, False),
        ("Cantidad y fechas", f"{escape(b['nombre']) or 'Sin nombre'} · {b['n_posts']} posts ·"
         f" {campanas.de_a(ini, fin)} · {dias} · {horas}", 7, True)]
    tarjetas = "".join(
        f'<div class="wiz-res{" ancho" if ancho else ""}"><h4>{t}</h4><p>{v if t == "Cantidad y fechas" else escape(v)}</p>'
        f'<button class="enlace" name="ir" value="{n}">Editar</button></div>' for t, v, n, ancho in bloques)
    with usar(c):
        quedan = await campanas.ia_restantes(c)
    editar = bool(b.get("cid"))
    return ('<div class="wiz-cab"><p class="ante">Resumen</p>'
            f'<h2>{"Revisa la estrategia de tu campaña" if editar else "Revisa tu campaña antes de generarla"}</h2>'
            '<p class="baj">Toca Editar en cualquier bloque para cambiarlo. Antes de los posts armamos el brief: la idea'
            " central de la campaña. Nada de esto gasta imágenes: primero ves el calendario con textos e ideas, y las"
            " imágenes las generas al final."
            + (" Los posts que ya tienen imagen se quedan en su día." if editar else "")
            + '</p><div class="wiz-barra"><i style="width:100%"></i></div></div>'
            f'<div class="wiz-resumen">{tarjetas}</div>{await cruce(c, b)}'
            f'<div class="wiz-total"><div><b>{b["n_posts"]} posts</b><span>en {b["semanas"]} semana'
            f'{"s" if b["semanas"] > 1 else ""}</span></div><div><b>{len(b["formatos"])} formato'
            f'{"s" if len(b["formatos"]) != 1 else ""}</b><span>repartidos parejo</span></div>'
            f'<div><b>~{imagenes_} imágenes</b><span>de tu cupo, solo al generarlas</span></div>'
            '<div><b>Tu empresa + Tu marca</b><span>como contexto de cada post</span></div></div>'
            f'<div class="wiz-pie"><button class="boton secundario" name="ir" value="{len(PASOS)}">‹ Anterior</button>'
            + (f'<span><button class="boton secundario" name="accion" value="brief"{"" if quedan else " disabled"}>'
               f'Otras ideas de brief</button></span><button class="boton" name="ir" value="{BRIEF}">Ver el brief ›'
               "</button></div>" if b.get("brief") else
               f'<span>{"Primero armamos el brief" if quedan else "Por hoy ya no quedan propuestas; mañana puedes pedir más."}'
               f'</span><button class="boton" name="accion" value="brief"{"" if quedan else " disabled"}>'
               "Crear el brief ›</button></div>"))


async def cruce(c: Cuenta, b: dict) -> str:
    """Cómo se cruzan lo que se ve y el formato: el mismo plan que usará campanas.proponer (misma semilla)."""
    camp = await a_campana(c, b)
    ves = camp["ves"]
    if not ves:
        return ""
    with usar(c):
        fichas = campanas.plan_de(camp, campanas.huecos(camp))
    dias: dict[tuple, list[str]] = {}
    for f in fichas:
        dias.setdefault((f["ve"], f["formato"]), []).append(f["dia"])
    total = len(ves) * len(camp["formatos"])

    def celda(ve: str, formato: str) -> str:
        if not (lista := dias.get((ve, formato))):
            return "<td>·</td>"
        d = date.fromisoformat(lista[0])
        mas = f"<small>y {len(lista) - 1} más</small>" if len(lista) > 1 else ""
        return f'<td class="si">{DIAS[d.weekday()]} {d.day}{mas}</td>'

    filas = "".join(f'<tr><th class="fila">{escape(VISUAL[k][0])}</th>'
                    + "".join(celda(k, f) for f in camp["formatos"]) + "</tr>" for k, _ in ves)
    n = len(fichas)
    return ('<div class="cruce"><h4>Cómo se cruzan</h4><p>Cada post lleva una combinación distinta de lo que se ve y'
            f" su formato. Con {n} posts cubrimos {len(dias)} de las {total} combinaciones"
            + (", sin repetir ninguna.</p>" if n <= total else "; cuando ya salieron todas, empiezan a repetirse.</p>")
            + "<table><tr><th></th>" + "".join(f"<th>{escape(campanas.FORMATOS[f][0])}</th>" for f in camp["formatos"])
            + f"</tr>{filas}</table></div>")


def paso_brief(b: dict, quedan: int) -> str:
    """Las tres ideas de campaña (tarjetas) y, debajo, el brief de la elegida para ajustarlo. Sin JavaScript:
    el brief de cada idea está en la página y el CSS enseña el de la tarjeta marcada."""
    conceptos, elegido = b["brief"]["conceptos"], b["brief"].get("elegido", 0)
    tarjetas = "".join(
        f'<label class="wiz-op"><input type="radio" name="concepto" value="{i}" id="concepto-{i}"'
        f'{" checked" if i == elegido else ""}><span class="cuerpo"><b>{escape(x["titulo"])}</b>'
        f'<small>{escape(x["resumen"])}</small>'
        + (f'<em>Pilar fuerte: {escape(x["pilar_fuerte"])}</em>' if x.get("pilar_fuerte") else "")
        + "</span></label>" for i, x in enumerate(conceptos))

    def editor(i: int, x: dict) -> str:
        def campos(lista) -> str:
            return "".join(
                f'<div class="campo"><label for="b{i}_{k}">{t}</label><textarea id="b{i}_{k}" name="b{i}_{k}"'
                f' rows="{r}" maxlength="{brief.LIMITES[k]}">{escape(x.get(k, ""))}</textarea></div>' for k, t, r in lista)

        pilares = "".join(
            f'<div><input name="b{i}_p{j}_nombre" value="{escape(p["nombre"])}" maxlength="{brief.LIMITES_PILAR["nombre"]}"'
            f' aria-label="Pilar {j + 1}"><textarea name="b{i}_p{j}_detalle" rows="3" aria-label="Detalle del pilar {j + 1}"'
            f' maxlength="{brief.LIMITES_PILAR["detalle"]}">{escape(p.get("detalle", ""))}</textarea></div>'
            for j, p in enumerate(x.get("pilares", [])))
        # Los pilares van después del mensaje clave, como en el brief.
        cuerpo = (campos(brief.CAMPOS[:2]) + f'<div class="campo"><label>Pilares</label><div class="pilares">{pilares}'
                  "</div></div>" + campos(brief.CAMPOS[2:]))
        return (f'<div class="brief-ed" id="brief-{i}"><h3>Brief de “{escape(x["titulo"])}”</h3>'
                "<p>Puedes cambiar cualquier texto. Lo que dejes aquí es lo que se sigue en cada post.</p>"
                f"{cuerpo}</div>")

    return ('<div class="wiz-cab"><p class="ante">Brief</p><h2>Elige la idea de tu campaña</h2><p class="baj">Con tu'
            " empresa, tu marca y lo que elegiste, te proponemos tres ideas distintas a tus campañas anteriores. Elige"
            " una y ajusta su brief: cada post se escribe a partir de él.</p>"
            '<div class="wiz-barra"><i style="width:100%"></i></div></div>'
            f'<div class="conceptos">{tarjetas}</div>' + "".join(editor(i, x) for i, x in enumerate(conceptos))
            + f'<div class="wiz-pie"><button class="boton secundario" name="ir" value="{RESUMEN}">‹ Resumen</button>'
            f'<button class="boton secundario" name="accion" value="brief"{"" if quedan else " disabled"}>Otras 3 ideas'
            f'</button><button class="boton" name="accion" value="proponer"{"" if quedan else " disabled"}>'
            f'{"Volver a escribir los posts" if b.get("cid") else "Escribir los posts"} ›</button></div>'
            + ("" if quedan else nota("Por hoy ya no quedan propuestas.", "Mañana puedes pedir más.")))


def brief_del_form(f, b: dict) -> None:
    """Lo que ajustó del brief (de las tres ideas) y cuál eligió."""
    conceptos = b["brief"]["conceptos"]
    for i, x in enumerate(conceptos):
        for k, _, _ in brief.CAMPOS:
            if (v := f.get(f"b{i}_{k}")) is not None:
                x[k] = " ".join(str(v).split())[:brief.LIMITES[k]]
        pilares = []
        for j, p in enumerate(x.get("pilares", [])):
            nombre = " ".join(str(f.get(f"b{i}_p{j}_nombre", p["nombre"])).split())[:brief.LIMITES_PILAR["nombre"]]
            detalle = " ".join(str(f.get(f"b{i}_p{j}_detalle", p.get("detalle", ""))).split())
            if nombre:
                pilares.append({"nombre": nombre, "detalle": detalle[:brief.LIMITES_PILAR["detalle"]]})
        x["pilares"] = pilares or x.get("pilares", [])
    elegido = str(f.get("concepto", ""))
    if elegido.isdigit() and int(elegido) < len(conceptos):
        b["brief"]["elegido"] = int(elegido)


def bloqueo(hay_empresa: bool, hay_marca: bool) -> str:
    def tarjeta(listo: bool, titulo: str, texto: str, url: str, boton: str) -> str:
        return (f'<div><span class="est {"ok" if listo else "no"}">{"✓ Listo" if listo else "Falta"}</span>'
                f"<b>{titulo}</b><small>{texto}</small>"
                f'<a class="boton chico{" secundario" if listo else ""}" href="{url}">{"Revisar" if listo else boton}</a></div>')
    return ('<div class="wiz-main bloqueo-cam"><div class="wiz-cab"><p class="ante">Antes de crear una campaña</p>'
            "<h2>Necesitamos conocer tu negocio</h2><p class=\"baj\">Con tu empresa y tu marca, cada post habla de lo que"
            " vendes, con tu tono y tus colores. Sin eso, el contenido sale genérico.</p></div>"
            '<div class="bloq-cam">'
            + tarjeta(hay_empresa, "Perfil de empresa", "Qué vendes, a quién y tus productos con precio y fotos.",
                      "/panel/empresa", "Completar tu empresa")
            + tarjeta(hay_marca, "Tu marca", "Tu estilo visual, colores, tipografía y logo.", "/panel/contenido/marca",
                      "Completar Tu marca")
            + "</div></div>")


async def pagina(c: Cuenta, paso: int, aviso: str = "", b: dict | None = None):
    hay_empresa, hay_marca = await lista_para_empezar(c)
    b = b or await leer(c)
    hero = '<span class="listo-cam"><i></i>Tu empresa y tu marca ya están listas</span>'
    if not (hay_empresa and hay_marca):
        cuerpo, hero = bloqueo(hay_empresa, hay_marca), ""
    else:
        objetivos, mensajes = await opciones(c, b["objetivo"])
        prods = await productos(c)
        if paso == BRIEF and not b.get("brief"):
            paso = RESUMEN
        if paso in OCULTOS:
            paso = vecino(paso, 1)
        if paso >= RESUMEN and faltan(b):
            paso = faltan(b)[0]
        if paso == 1:
            contenido = paso_1(b, objetivos)
        elif paso == 2:
            contenido = paso_2(b, mensajes, prods, objetivos)
        elif paso == 3:
            contenido = paso_3(b, objetivos)
        elif paso == 4:
            with usar(c):
                fotos = len(campanas.fotos_marca(c, await marca_de(c)))
            contenido = paso_4(b, fotos)
        elif paso == 5:
            contenido = paso_5(b)
        elif paso == 6:
            contenido = paso_6(b)
        elif paso == 7:
            contenido = paso_7(b)
        elif paso == BRIEF:
            contenido = paso_brief(b, await campanas.ia_restantes(c))
        else:
            contenido = await resumen(c, b, objetivos, mensajes, prods)
        # Un solo formulario con el riel adentro; el primer botón (invisible) es el que usa Enter: seguir.
        sigue = BRIEF if paso == BRIEF else min(vecino(paso, 1), RESUMEN)
        cuerpo = (f'<form method="post" action="{URL}" class="wiz" id="wiz-form">'
                  f'<button class="enter-cam" name="ir" value="{sigue}" tabindex="-1" aria-hidden="true">Seguir</button>'
                  f'<input type="hidden" name="paso" value="{paso}">{riel(b, paso)}'
                  f'<div class="wiz-main">{contenido}</div></form>')
    editar = bool(b.get("cid"))
    return panel.pagina(
        panel.cabecera(salir=True, menu="contenido", cuenta=c, sub="campanas", perfil=await panel.usuario(c),
                       foto=await panel.foto_perfil(c))
        + campanas.cabeza(f'<div class="der-cam">{hero}{campanas.boton_todas()}</div>', "", "Editar estrategia" if editar else "Nueva campaña")
        + '<div class="oscuro-pag"><main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + cuerpo + "</div></main></div>")


def _paso(valor) -> int:
    return int(valor) if str(valor).isdigit() and 1 <= int(valor) <= BRIEF else 1


@router.get("/campanas/nueva")
async def ver(request: Request):
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if c.imagenes_mes <= 0:
        return sin_plan()
    q = request.query_params
    if cid := q.get("editar"):  # "Editar estrategia" de una campaña: el resumen con lo que eligió
        if not (camp := await campanas.leer(c, cid)):
            return RedirectResponse(campanas.BASE, status_code=303)
        await escribir(c, de_campana(camp))
        return RedirectResponse(f"{URL}?paso={RESUMEN}", status_code=303)
    if q.get("empezar") == "1" or (not q.get("paso") and (await leer(c)).get("cid")):
        await c.store.set(BORRADOR, "{}")
    return await pagina(c, _paso(q.get("paso", 1)))


@router.post("/campanas/nueva")
async def guardar(request: Request):
    panel.de_aqui(request)
    if not (c := panel.cuenta_de_sesion(request)):
        return RedirectResponse("/panel", status_code=303)
    if c.imagenes_mes <= 0:
        return sin_plan()
    f = await request.form()
    paso, b = _paso(f.get("paso")), await leer(c)
    objetivos, mensajes = await opciones(c, b["objetivo"])
    b.setdefault("semilla", int(time.time()))  # el mismo cruce en el resumen y al escribir los posts
    if paso < RESUMEN:
        antes = {k: v for k, v in b.items() if k not in ("hechos", "brief")}
        error = del_form(f, paso, b, objetivos, mensajes, await productos(c))
        if b.get("brief") and {k: v for k, v in b.items() if k not in ("hechos", "brief")} != antes:
            b.pop("brief")  # cambió la campaña: el brief de antes ya no le queda
        hechos = set(b.get("hechos", []))
        (hechos.discard if error else hechos.add)(paso)
        b["hechos"] = sorted(hechos)
        await escribir(c, b)
        ir = _paso(f.get("ir", paso))
        if error and ir > paso:  # adelante no se pasa con algo que falta; atrás o a otro paso, sí
            return await pagina(c, paso, error, b)
        return RedirectResponse(f"{URL}?paso={ir}", status_code=303)
    if paso == BRIEF and b.get("brief"):
        brief_del_form(f, b)
    await escribir(c, b)
    accion = f.get("accion")
    if accion not in ("brief", "proponer") or (accion == "proponer" and not b.get("brief")):
        return RedirectResponse(f"{URL}?paso={_paso(f.get('ir', paso))}", status_code=303)
    if faltan(b):
        return RedirectResponse(f"{URL}?paso={faltan(b)[0]}", status_code=303)
    hay_empresa, hay_marca = await lista_para_empezar(c)
    if not (hay_empresa and hay_marca):
        return RedirectResponse(URL, status_code=303)
    datos = await a_campana(c, b)
    camp = await campanas.leer(c, b["cid"]) if b.get("cid") else {}
    with usar(c):
        hoy = tools.now().date()
    if not camp and date.fromisoformat(datos["inicio"]) < hoy:
        return await pagina(c, 7, "La campaña no puede empezar en un día que ya pasó.", b)
    if accion == "brief":
        return await crear_brief(c, b, datos)
    datos["brief"] = b["brief"]["conceptos"][b["brief"].get("elegido", 0)]
    return await campanas.armar(c, datos, camp, borrador=True)


async def crear_brief(c: Cuenta, b: dict, datos: dict):
    """Las tres ideas de campaña con su brief (una llamada a Claude, cuenta como una propuesta del día)."""
    if not await campanas.ia_restantes(c):
        return await pagina(c, RESUMEN, "Por hoy ya no quedan propuestas; mañana puedes pedir más.", b)
    with usar(c):
        otras = [x for x in await campanas.todas_las(c) if x.get("id") != b.get("cid")]

    async def trabajo():
        with usar(c):
            await campanas.usar_ia(c)
            conceptos = await brief.conceptos(c, datos, otras)
        b["brief"] = {"conceptos": conceptos, "elegido": 0}
        await escribir(c, b)
        yield f"{URL}?paso={BRIEF}"

    return campanas.trabajando(c, "Pensando las ideas de tu campaña…", "Tarda unos segundos.", trabajo,
                               f"{URL}?paso={RESUMEN}")
