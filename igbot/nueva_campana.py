"""Contenido › Campañas › Nueva campaña: el asistente por pasos (sencillo por fuera, dimensiones claras por dentro).

Primero el tipo de campaña: "Promocionar un producto o servicio" (Producto, La oferta, Estilo y Fechas: anuncios
limpios del producto real, en varias versiones) o "Contenido para tu cuenta", con sus pasos: Objetivo, Mensaje
(con el producto, si es sobre uno), Enfoque del contenido (de qué tratan los posts), Formatos, Qué se ve, Estilo y
variedad, y Cantidad y fechas. Al final, un resumen.
Sin JavaScript: cada paso es ?paso=N y cada botón (también los del riel de pasos) es un formulario normal que
guarda el borrador (kv "campana_borrador") y lleva al paso que se tocó. En el resumen, "Crear el brief" le pide a
Claude tres ideas de campaña (brief.conceptos); en el paso Brief elige una, la ajusta y "Escribir los posts" arma la
campaña con campanas.armar a partir de ese brief.

Por dentro, lo que eligió se traduce a lo que ya usa campanas.proponer: los temas salen del objetivo y del
mensaje, los formatos tal cual (o todos, con "Que la IA decida") y los horarios tal cual (o todos, a la hora en que
está conectado su público, con "Cuando está tu público"), y el resto viaja a Claude como
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

from . import brief, campanas, empresa, limites, marco, tienda_shopify, tools
from .contenido import TOKEN_FOTO, archivo, guardar_fotos, marca_de, modo_de, sin_plan, url_imagen
from .cuenta import Cuenta, usar

router = APIRouter(prefix="/panel/contenido")
URL = f"{campanas.BASE}/nueva"
BORRADOR = "campana_borrador"

PASOS = ("Objetivo", "Mensaje", "Enfoque del contenido", "Formatos", "Qué se ve", "Estilo y variedad", "Cantidad y fechas")
RESUMEN = len(PASOS) + 1
OCULTOS = {5}  # Qué se ve: fuera del asistente (el plan de cada imagen sale del ángulo del post y del perfil)
VISIBLES = [i for i in range(1, RESUMEN) if i not in OCULTOS]
BRIEF = RESUMEN + 1  # después del resumen: elegir la idea de la campaña y ajustar su brief
# El primer paso y los del camino de producto (números aparte: los de siempre no cambian de URL).
TIPO, PRODUCTO, OFERTA = 10, 11, 12
TIPOS = {  # clave: (título, descripción, color de respaldo)
    "producto": ("Promocionar un producto o servicio", "Eliges uno de tus productos o servicios y lo que quieres hacer"
                 " con él: lanzarlo, darlo a conocer o una promoción. Salen anuncios limpios, con tu producto real, en"
                 " varias versiones para elegir.", "#4a2312"),
    "contenido": ("Contenido para tu cuenta", "Una campaña de varias semanas para atraer, enseñar, crear comunidad o"
                  " fortalecer tu marca. Es la campaña de siempre.", "#2d2640"),
}
DE_PRODUCTO = [TIPO, PRODUCTO, OFERTA, 6, 7]
NOMBRES_PRODUCTO = {TIPO: "Tipo de campaña", PRODUCTO: "Producto", OFERTA: "La oferta", 6: "Estilo", 7: "Fechas"}
VERSIONES = (2, 4, 6)  # cuántas versiones del anuncio (cada una es un post de la campaña)


def de_producto(b: dict) -> bool:
    return b.get("tipo") == "producto"


def visibles(b: dict) -> list[int]:
    """Los pasos que ve, en orden, según el tipo de campaña."""
    return DE_PRODUCTO if de_producto(b) else [TIPO, *VISIBLES]


def nombre_paso(b: dict, n: int) -> str:
    return NOMBRES_PRODUCTO[n] if de_producto(b) else NOMBRES_PRODUCTO[TIPO] if n == TIPO else PASOS[n - 1]


def orden(b: dict, n: int) -> int:
    """Dónde va el paso n en el camino (el resumen y el brief al final)."""
    lista = [*visibles(b), RESUMEN, BRIEF]
    return lista.index(n) if n in lista else 0


def vecino(b: dict, n: int, paso: int) -> int:
    """El paso visible siguiente (paso=1) o anterior (paso=-1); RESUMEN después del último."""
    lista = visibles(b)
    i = (lista.index(n) if n in lista else 0) + paso
    return RESUMEN if i >= len(lista) else lista[max(i, 0)]
MAX_MENSAJES = 2
MAX_TEXTO, MAX_CONDICION = 400, 120

# Las imágenes de las tarjetas: igbot/static/tarjetas/campana/<sección>-<clave>.webp (el usuario las hace aparte).
# Mientras no estén, cada tarjeta lleva su color de respaldo.
TARJETAS = campanas.marco.STATIC / "tarjetas" / "campana"

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
ACCIONES = {"lanzar": "Lanzarlo", "conocer": "Darlo a conocer", "promo": "Promoción",
            "impulsar": "Volver a impulsarlo"}
PROMOS = {"pct": "% de descuento", "2x1": "2x1", "3x2": "3x2", "otra": "Otra"}  # qué promoción
MAX_PROMO = 80
FORMATOS = {  # clave: (título, descripción, color)
    "foto": ("Foto protagonista", "Una fotografía que destaca el producto, servicio o escena sin textos gráficos"
             " añadidos.", "#3a2f25"),
    "titular": ("Foto con titular", "Una fotografía acompañada de una frase breve que destaca la idea principal.",
                "#33302a"),
    "texto": ("Diseño de texto", "Una frase, consejo o mensaje destacado con los colores y la tipografía de la marca.",
              "#26262a"),
    "bento": ("Bento grid", "Varios recuadros de distintos tamaños en una sola imagen: el producto, sus detalles y su"
              " uso, ordenados como una cuadrícula.", "#2a2833"),
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
HORARIOS = {**{k: f"{v[0]} · {v[1]}" for k, v in campanas.HORARIOS.items()}, "ia": "Cuando está tu público"}
MAX_PREGUNTAN = 3  # preguntas de sus clientes que se enseñan en el paso Mensaje
DIAS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
MAX_POSTS = 30
MAX_HISTORIAS = 30
MAX_CANDIDATOS_REF = 18  # fotos para elegir como referencia en el paso Estilo y variedad


# --- el borrador ---

def inicial(marca: dict) -> dict:
    lunes = campanas.lunes_que_viene()
    return {"tipo": "contenido", "versiones": 4,
            "objetivo": "", "mensajes": [], "producto": "", "accion": "conocer", "promo": "pct", "promo_otra": "", "descuento": "", "desde": "",
            "hasta": "", "condicion": "", "texto": "", "con_producto": False, "con_texto": False, "preguntan": True, "funciona": False,
            "mezcla": campanas.MEZCLA_INICIAL, "formatos": ["foto", "titular"],
            "visual": ["productos", "personas", "propias"], "textos_img": ["titular"],
            "variedad": "coherencia", "armado": modo_de(marca), "nombre": "", "inicio": lunes.isoformat(),
            "semanas": 2, "n_posts": 8, "n_historias": 0, "dias": [0, 1, 2, 3, 4], "horarios": ["manana", "noche"], "hechos": []}


async def leer(c: Cuenta) -> dict:
    guardado = json.loads(await c.store.get(BORRADOR) or "{}")
    return {**inicial(await marca_de(c)), **guardado}


async def escribir(c: Cuenta, b: dict) -> None:
    await c.store.set(BORRADOR, json.dumps(b, ensure_ascii=False))


def de_campana(camp: dict) -> dict:
    """Para "Ver planificación": el borrador con lo que eligió para esa campaña (si se armó con el asistente)."""
    b = dict(camp.get("asistente") or {})
    b.update(nombre=camp.get("nombre", ""), inicio=camp["inicio"], semanas=camp.get("semanas", 2),
             n_posts=camp.get("n_posts") or len(camp.get("posts", [])) or 8,
             n_historias=camp.get("n_historias") or 0, cid=camp["id"],
             hechos=[*range(1, RESUMEN), TIPO, PRODUCTO, OFERTA])
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
        from . import sugeridos
        return sugeridos.mezclar(datos, await sugeridos.de(c), objetivo)  # los de cada lunes, al final
    objetivos = [{"k": k, "titulo": v[0], "descripcion": v[1]} for k, v in OBJETIVOS.items()]
    claves = MENSAJES_DE.get(objetivo, tuple(MENSAJES))
    return objetivos, [{"k": k, "titulo": MENSAJES[k][0], "descripcion": MENSAJES[k][1]} for k in claves]


def base_de(op: dict, propias: dict, palabras: tuple) -> str:
    """A cuál de las de respaldo se parece una opción (para saber qué temas le sirven): la que dijo la IA al
    escribirla ("tipo"); en las de antes, por sus palabras."""
    if op.get("k") in propias:
        return op["k"]
    if op.get("tipo") in propias:
        return op["tipo"]
    texto = f"{op.get('titulo', '')} {op.get('descripcion', '')}".lower()
    return next((k for k, claves in palabras if any(p in texto for p in claves)), next(iter(propias)))


async def productos(c: Cuenta) -> list[dict]:
    return [p for p in await empresa.productos(c) if isinstance(p, dict) and p.get("id") and p.get("nombre")]


def precio_de(p: dict) -> str:
    return empresa.precio_texto(p)


async def lista_para_empezar(c: Cuenta) -> tuple[bool, bool]:
    """(empresa lista, marca lista): sin las dos no se puede crear una campaña."""
    return await empresa.empresa_lista(c), await marco.marca_lista(c)


# --- de lo que eligió a la campaña ---

async def a_campana(c: Cuenta, b: dict) -> dict:
    """Lo que necesita campanas.proponer, sacado del borrador."""
    if de_producto(b):
        return await a_campana_producto(c, b)
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
        temas = enfoques  # lo que eligió, tal cual (con uno solo, todos los posts son de ese enfoque)
    temas = temas[:campanas.MAX_TEMAS]
    fuera = (*campanas.RETIRADOS, *campanas.APARTE)  # el anuncio es de las campañas de producto
    formatos = [k for k in b["formatos"] if k in campanas.FORMATOS and k not in fuera]
    if "ia" in b["formatos"] or not formatos:
        formatos = [k for k in campanas.FORMATOS if k not in fuera]
    horarios = [k for k in b["horarios"] if k in campanas.HORARIOS]
    horas = {}
    if "ia" in b["horarios"] or not horarios:  # a la hora en que más seguidores suyos están conectados, si se sabe
        horarios = list(campanas.HORARIOS)
        horas = await campanas.horas_de(c)
    prod = next((p for p in await productos(c) if p["id"] == b["producto"]), None)
    producto = texto_producto(b, prod)
    # Qué se ve solo cuenta si el dueño lo eligió: con el paso oculto, lo decide el plan con los datos de su perfil.
    ves = [] if 5 in OCULTOS else [k for k in b["visual"] if k in VISUAL and k != "propias"]
    visual = ", ".join(VISUAL[k][0] for k in ves)
    titular = (f"Texto en las fotos con titular (se alternan): {', '.join(TEXTO[k][0].lower() for k in textos(b))}. "
               if "titular" in b["formatos"] else "")
    estilo = (f"{titular}Variedad visual: {VARIEDAD[b['variedad']][0]}"
              f" ({VARIEDAD[b['variedad']][1]})")
    inicio = date.fromisoformat(b["inicio"])
    return {"nombre": b["nombre"].strip() or f"{obj['titulo']} · {campanas.fecha_larga(b['inicio'])}"[:campanas.MAX_NOMBRE],
            "inicio": inicio.isoformat(), "semanas": b["semanas"], "publicar": "lun-sab",
            "dias_semana": sorted(b["dias"]), "n_posts": b["n_posts"], "n_historias": b.get("n_historias", 0),
            "tema": b["texto"],
            "modo": "repetir" if b.get("funciona") else "explorar", "mezcla": b["mezcla"], "temas": temas,
            "enfoques": bool(enfoques), "formatos": formatos, "horarios": horarios, "horas": horas,
            "clientes": bool(b.get("preguntan")),  # responde lo que más le preguntan (paso Mensaje)
            "objetivo": f"{obj['titulo']}: {obj.get('descripcion', '')}".strip(": "),
            "mensaje": "; ".join(f"{m['titulo']}: {m.get('descripcion', '')}"
                                 + (f" (por ejemplo: «{m['ejemplo']}»)" if m.get("ejemplo") else "") for m in msgs),
            "producto": producto, "producto_id": prod["id"] if prod else "", "visual": visual, "estilo": estilo,
            "fotos_propias": "propias" in b["visual"], "modo_img": b["armado"], "regla": base == "interaccion",
            "ves": [[k, f"{VISUAL[k][0]}: {VISUAL[k][1]}"] for k in ves],
            "variedad": b["variedad"],
            "referencias": b.get("referencias", []),
            "adn_visual": (b.get("adn") or {}).get("texto", "") if (b.get("adn") or {}).get("fotos") == b.get(
                "referencias") else "",
            "semilla": b.get("semilla") or 0,
            "asistente": {k: v for k, v in b.items() if k not in ("cid", "hechos", "adn")}}


def promo_texto(b: dict) -> str:
    """La promoción tal como se dice: "20 % de descuento", "2x1", "3x2" o la que escribió."""
    tipo = b.get("promo", "pct")
    if tipo == "pct":
        return f"{b['descuento']} % de descuento" if b["descuento"] else ""
    if tipo == "otra":
        return b.get("promo_otra", "")
    return PROMOS.get(tipo, "")


def promo_del_form(g, b: dict) -> str:
    """La acción y la promoción (con sus fechas) que vinieron del paso; devuelve '' o lo que falta."""
    b["accion"] = g("accion") if g("accion") in ACCIONES else "conocer"
    b["promo"] = g("promo") if g("promo") in PROMOS else "pct"
    b["descuento"] = g("descuento") if g("descuento").isdigit() and 1 <= int(g("descuento")) <= 90 else ""
    b["promo_otra"] = " ".join(g("promo_otra").split())[:MAX_PROMO]
    b["desde"], b["hasta"] = _fecha(g("desde")), _fecha(g("hasta"))
    b["condicion"] = g("condicion")[:MAX_CONDICION]
    if b["accion"] == "promo" and b["promo"] == "pct" and not b["descuento"]:
        return "Escribe el porcentaje de descuento (de 1 a 90)."
    if b["accion"] == "promo" and b["promo"] == "otra" and not b["promo_otra"]:
        return "Escribe cuál es la promoción."
    if b["desde"] and b["hasta"] and b["hasta"] < b["desde"]:
        return "La promoción no puede terminar antes de empezar."
    return ""


def texto_producto(b: dict, prod: dict | None) -> str:
    """El producto elegido y lo que quiere hacer con él, como lo lee Claude."""
    if not prod:
        return ""
    producto = f"{prod['nombre']}" + (f" ({precio_de(prod)})" if precio_de(prod) else "")
    if prod.get("descripcion"):
        producto += f": {prod['descripcion']}"
    producto += f". Qué quiere: {ACCIONES.get(b['accion'], ACCIONES['conocer']).lower()}"
    if b["accion"] == "promo" and (promo := promo_texto(b)):
        producto += f", {promo}"
        if b["desde"] and b["hasta"]:
            producto += f" del {campanas.fecha_larga(b['desde'])} al {campanas.fecha_larga(b['hasta'])}"
        if b["condicion"]:
            producto += f" ({b['condicion']})"
    return producto + "."


def nombre_producto(b: dict, prods: list[dict]) -> str:
    """El nombre que lleva una campaña de producto si no le pone uno: el producto y lo que quiere hacer."""
    prod = next((p for p in prods if p["id"] == b["producto"]), None)
    accion = (promo_texto(b) if b["accion"] == "promo" else "") or ACCIONES.get(b["accion"], ACCIONES["conocer"])
    return (f"{prod['nombre']} · {accion}" if prod else accion)[:campanas.MAX_NOMBRE]


async def a_campana_producto(c: Cuenta, b: dict) -> dict:
    """Promocionar un producto o servicio: solo anuncios de ese producto, una pieza por versión."""
    prod = next((p for p in await productos(c) if p["id"] == b["producto"]), None)
    accion = ACCIONES.get(b["accion"], ACCIONES["conocer"])
    horarios = [k for k in b["horarios"] if k in campanas.HORARIOS]
    if "ia" in b["horarios"] or not horarios:
        horarios = list(campanas.HORARIOS)
    return {"nombre": b["nombre"].strip()[:campanas.MAX_NOMBRE] or nombre_producto(b, [prod] if prod else []),
            "inicio": b["inicio"], "semanas": b["semanas"], "publicar": "lun-sab", "dias_semana": sorted(b["dias"]),
            "n_posts": b["versiones"], "n_historias": 0, "tema": b["texto"], "modo": "explorar", "mezcla": b["mezcla"],
            "temas": ["promo", "producto"] if b["accion"] == "promo" else ["producto", "beneficios"],
            "formatos": ["anuncio"], "horarios": horarios, "clientes": False,
            "objetivo": f"{TIPOS['producto'][0]}: {accion.lower()}",
            "mensaje": f"Un solo producto o servicio: {prod['nombre']}" if prod else "",
            "producto": texto_producto(b, prod), "producto_id": prod["id"] if prod else "", "visual": "",
            "estilo": f"Anuncios limpios. Variedad visual: {VARIEDAD[b['variedad']][0]} ({VARIEDAD[b['variedad']][1]})",
            "fotos_propias": True, "modo_img": "libre", "regla": False, "ves": [], "variedad": b["variedad"],
            "referencias": b.get("referencias", []),
            "adn_visual": (b.get("adn") or {}).get("texto", "") if (b.get("adn") or {}).get("fotos") == b.get(
                "referencias") else "",
            "semilla": b.get("semilla") or 0,
            "asistente": {k: v for k, v in b.items() if k not in ("cid", "hechos", "adn")}}


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
    if paso == TIPO:
        b["tipo"] = g("tipo") if g("tipo") in TIPOS else b.get("tipo", "contenido")
        return ""
    if paso == PRODUCTO:
        b["producto"] = g("producto") if g("producto") in {p["id"] for p in prods} else ""
        b["con_producto"] = bool(b["producto"])
        return "" if b["producto"] else "Elige qué quieres promocionar."
    if paso == OFERTA:
        error = promo_del_form(g, b)
        b["versiones"] = int(g("versiones")) if g("versiones").isdigit() and int(g("versiones")) in VERSIONES else 4
        b["con_texto"] = g("con_texto") == "1"
        b["texto"] = g("texto")[:MAX_TEXTO] if b["con_texto"] else ""
        if error:
            return error
        if b["accion"] == "promo" and b["desde"]:  # los anuncios salen mientras dura la promoción
            b["inicio"] = b["desde"]
            if b["hasta"]:
                dias = (date.fromisoformat(b["hasta"]) - date.fromisoformat(b["desde"])).days + 1
                b["semanas"] = max(1, min(-(-dias // 7), max(campanas.SEMANAS)))
        return ""
    if paso == 1:
        if g("objetivo") in {o["k"] for o in objetivos} and g("objetivo") != b["objetivo"]:
            b["objetivo"] = g("objetivo")
            b["mensajes"] = []  # los mensajes dependen del objetivo: se eligen de nuevo
            b["hechos"] = [h for h in b.get("hechos", []) if h != 2]
        b["funciona"] = g("funciona") == "1"
        return "" if b["objetivo"] else "Elige qué quieres lograr."
    if paso == 2:
        b["mensajes"] = _lista(f, "mensajes", {m["k"] for m in mensajes})[:MAX_MENSAJES]
        b["preguntan"] = g("preguntan") == "1"
        b["con_producto"], b["con_texto"] = g("con_producto") == "1", g("con_texto") == "1"
        b["producto"] = g("producto") if b["con_producto"] and g("producto") in {p["id"] for p in prods} else ""
        error = promo_del_form(g, b)
        b["texto"] = g("texto")[:MAX_TEXTO] if b["con_texto"] else ""
        if not b["mensajes"]:
            return "Elige al menos un mensaje."
        return error if b["producto"] else ""
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
        if de_producto(b):  # una pieza por versión del anuncio, sin historias
            b["n_posts"], b["n_historias"] = b["versiones"], 0
        else:
            n = int(g("n_posts")) if g("n_posts").isdigit() else 0
            b["n_posts"] = max(1, min(n, MAX_POSTS)) if n else b["n_posts"]
            h = g("n_historias")
            b["n_historias"] = min(int(h), MAX_HISTORIAS) if h.isdigit() else 0
        if not b["dias"]:
            return "Elige al menos un día para publicar."
        if not b["horarios"]:
            return "Elige al menos un horario."
        dias = len(campanas.dias_de({"inicio": b["inicio"], "semanas": b["semanas"], "dias_semana": b["dias"]}))
        if b["n_posts"] > dias * campanas.MAX_POSTS_DIA:
            return (f"En {b['semanas']} semana{'s' if b['semanas'] > 1 else ''} con esos días caben hasta"
                    f" {dias * campanas.MAX_POSTS_DIA} posts (dos por día). Agrega días o semanas, o pide menos.")
        if b["n_historias"] > dias * campanas.MAX_HISTORIAS_DIA:
            return (f"En {b['semanas']} semana{'s' if b['semanas'] > 1 else ''} con esos días caben hasta"
                    f" {dias * campanas.MAX_HISTORIAS_DIA} historias (tres por día). Agrega días o semanas, o pide menos.")
        return ""
    return ""


def faltan(b: dict) -> list[int]:
    """Los pasos que faltan (el tipo de campaña siempre trae uno elegido)."""
    return [i for i in visibles(b) if i != TIPO and i not in b.get("hechos", [])]


# --- páginas ---

def img(seccion: str, clave: str, color: str, texto: str = "", alta: bool = False) -> str:
    """alta: la imagen completa, vertical (2:3); si no, 16:10."""
    archivo = f"{seccion}-{clave}.webp"
    fondo = f"{color} url(/panel/static/tarjetas/campana/{archivo})" if (TARJETAS / archivo).is_file() else color
    return f'<span class="wiz-img{" alta" if alta else ""}" style="--fondo:{fondo}">{"" if (TARJETAS / archivo).is_file() else escape(texto)}</span>'


# Los brillitos de IA de los sugeridos de cada lunes (en lima: un acento chico).
BRILLOS = campanas.BRILLOS_IA


def op(nombre: str, valor: str, titulo: str, desc: str, tipo: str = "radio", marcada: bool = False,
       imagen: str = "", em: str = "", apagada: bool = False, sugerido: bool = False) -> str:
    return (f'<label class="wiz-op{" apagada" if apagada else ""}{" sugerida" if sugerido else ""}">'
            f'<input type="{tipo}" name="{nombre}" value="{escape(valor)}"{" checked" if marcada else ""}'
            f'{" disabled" if apagada else ""}>{imagen}'
            f'<span class="cuerpo">{f"<i class=sug-ia>{BRILLOS}Sugerido</i>" if sugerido else ""}'
            f'<b>{escape(titulo)}</b><small>{escape(desc)}</small>'
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
        f' name="ir" value="{i}"><b>{"✓" if i in hechos and i != actual else n}</b>{nombre_paso(b, i)}</button>'
        for n, i in enumerate(visibles(b), 1))
    listo = not faltan(b)
    avance = 100 if actual in (RESUMEN, BRIEF) else len(hechos & set(visibles(b))) * 100 // len(visibles(b))
    return (f'<nav class="wiz-pasos" aria-label="Pasos de la campaña"><p>Tu campaña</p>{filas}'
            f'<button class="wiz-paso{" actual" if actual == RESUMEN else ""}{"" if listo else " apagado"}" name="ir"'
            f' value="{RESUMEN}"><b>✓</b>Resumen</button>'
            + (f'<button class="wiz-paso{" actual" if actual == BRIEF else ""}" name="ir" value="{BRIEF}"><b>✓</b>Brief'
               "</button>" if b.get("brief") else "") + marco.avance(avance) + "</nav>")


def cab(b: dict, n: int, titulo: str) -> str:
    lista = visibles(b)  # el número que ve: sin contar los pasos ocultos
    v = lista.index(n) + 1 if n in lista else n
    return (f'<div class="wiz-cab"><p class="ante">Paso {v} de {len(lista)} · {nombre_paso(b, n)}</p>'
            f'<h2>{titulo}</h2></div>')  # sin subtítulo


def pie(b: dict, n: int) -> str:
    lista = visibles(b)
    siguiente = "Ver resumen" if n == lista[-1] else "Continuar"
    return ('<div class="wiz-pie">'
            + (f'<button class="boton secundario" name="ir" value="{vecino(b, n, -1)}">‹ Anterior</button>'
               if n != lista[0] else f'<a class="boton secundario" href="{campanas.BASE}">Cancelar</a>')
            + f'<span>Paso {lista.index(n) + 1 if n in lista else n} de {len(lista)}</span>'
            f'<button class="boton" name="ir" value="{vecino(b, n, 1)}">{siguiente} ›</button></div>')


def nota(titulo: str, texto: str) -> str:
    return f'<p class="wiz-nota"><b>{titulo}</b> {texto}</p>'


def paso_tipo(b: dict) -> str:
    ops = "".join(op("tipo", k, t, d, marcada=b.get("tipo", "contenido") == k, imagen=img("tipo", k, color, t))
                  for k, (t, d, color) in TIPOS.items())
    return (cab(b, TIPO, "¿Qué tipo de campaña quieres?")
            + f'<div class="wiz-ops dos">{ops}</div>' + pie(b, TIPO))


def paso_producto(b: dict, prods: list[tuple[dict, str]], vendidos: list[str] = ()) -> str:
    """Sus productos y servicios (Tu empresa o Shopify) como tarjetas con su foto y su precio. Los que más se vendieron
    este mes en su tienda (tienda_shopify.mas_vendidos) van primero, sugeridos."""
    prods = sorted(prods, key=lambda x: vendidos.index(x[0]["id"]) if x[0]["id"] in vendidos else len(vendidos))
    if not prods:
        return (cab(b, PRODUCTO, "¿Qué quieres promocionar?")
                + nota("Todavía no tienes productos.", 'Agrégalos en <a href="/panel/empresa">Tu empresa</a> (o tráelos de'
                       " tu Shopify) con su foto y su precio, y vuelve aquí.") + pie(b, PRODUCTO))
    ops = "".join(
        op("producto", x["id"], x["nombre"], " · ".join(v for v in (precio_de(x), dict(empresa.TIPOS).get(x.get("tipo"), "")) if v),
           marcada=x["id"] == b["producto"],
           imagen=f'<span class="wiz-img" style="--fondo:#26262a{f" url({u})" if u else ""}">'
                  f'{"" if u else "Sin foto"}</span>',
           em="Más vendido este mes" if x["id"] in vendidos else "", sugerido=x["id"] in vendidos)
        for x, u in prods)
    return (cab(b, PRODUCTO, "¿Qué quieres promocionar?")
            + f'<div class="wiz-ops cuatro prods-cam">{ops}</div>'
            + '<p class="ayuda" style="margin-top:1rem">¿No está? Agrégalo en <a href="/panel/empresa">Tu empresa</a>.</p>'
            + pie(b, PRODUCTO))


def campos_promo(b: dict) -> str:
    """Con "Promoción": cuál (porcentaje, 2x1, 3x2 u otra que escribe), sus fechas y su condición (sin JS: :has)."""
    tipo = b.get("promo", "pct")
    return ('<div class="wiz-si promo"><label class="t" style="margin-top:1rem">¿Qué promoción?</label>'
            '<div class="wiz-chips">' + "".join(chip("promo", k, v, tipo == k) for k, v in PROMOS.items()) + "</div>"
            '<div class="wiz-campos" style="margin-top:1rem">'
            f'<div class="solo-pct"><label for="w-desc">Descuento (%)</label><input type="number" id="w-desc"'
            f' name="descuento" min="1" max="90" value="{escape(b["descuento"])}" placeholder="20"></div>'
            f'<div class="solo-otra"><label for="w-otra">¿Cuál?</label><input type="text" id="w-otra" name="promo_otra"'
            f' maxlength="{MAX_PROMO}" value="{escape(b.get("promo_otra", ""))}" placeholder="Ej. envío gratis"></div>'
            f'<div><label for="w-desde">Desde</label><input type="date" id="w-desde" name="desde" value="{b["desde"]}"></div>'
            f'<div><label for="w-hasta">Hasta</label><input type="date" id="w-hasta" name="hasta" value="{b["hasta"]}"></div>'
            f'<div><label for="w-cond">Condición (opcional)</label><input type="text" id="w-cond" name="condicion"'
            f' maxlength="{MAX_CONDICION}" value="{escape(b["condicion"])}" placeholder="Ej. solo en sucursal"></div>'
            "</div></div>")


def paso_oferta(b: dict, prods: list[dict]) -> str:
    prod = next((p for p in prods if p["id"] == b["producto"]), None)
    nombre = escape(prod["nombre"]) + (f" · {escape(precio_de(prod))}" if precio_de(prod) else "") if prod else ""
    return (cab(b, OFERTA, "¿Qué quieres hacer con él?")
            + '<div class="wiz-bloque prod-cam">' + (f'<label class="t">{nombre}</label>' if nombre else "")
            + '<label class="t" style="margin-top:1rem">¿Qué quieres hacer?</label><div class="wiz-chips">'
            + "".join(chip("accion", k, v, b["accion"] == k) for k, v in ACCIONES.items())
            + "</div>" + campos_promo(b) + "</div>"
            '<div class="wiz-bloque"><label class="t">¿Cuántas versiones del anuncio?</label><p class="ayuda">Cada'
            " versión es una imagen distinta del mismo anuncio y sale como un post de la campaña. Así ves cuál"
            ' funciona mejor; las que no te gusten las borras.</p><div class="wiz-chips">'
            + "".join(chip("versiones", n, str(n), b.get("versiones", 4) == n) for n in VERSIONES) + "</div></div>"
            + '<div class="wiz-bloque">' + abre("con_texto", "Cuéntalo en tus palabras", b.get("con_texto") or b["texto"])
            + '<p class="ayuda">Un dato que no debe faltar en el anuncio (opcional).</p>'
            f'<textarea name="texto" rows="2" maxlength="{MAX_TEXTO}" aria-label="Tu idea" placeholder="Ej. Solo esta'
            f' semana, o envío gratis.">{escape(b["texto"])}</textarea></div></div>'
            + pie(b, OFERTA))


def paso_1(b: dict, objetivos: list[dict], funciono: str = "") -> str:
    ops = "".join(op("objetivo", o["k"], o["titulo"], o.get("descripcion", ""), marcada=o["k"] == b["objetivo"],
                     sugerido=bool(o.get("sugerido"))) for o in objetivos)  # sin miniatura: cada opción es de su negocio
    repetir = ""
    if funciono:  # ya hay resultados suficientes: puede apoyarse en lo que mejor le funcionó
        lineas = "".join(f"<li>{escape(x.lstrip('- '))}</li>" for x in funciono.splitlines() if x.strip())
        repetir = ('<div class="wiz-bloque">' + abre("funciona", "Repetir lo que funcionó", b.get("funciona"))
                   + '<p class="ayuda">Lo que mejor te ha funcionado en tus campañas. La mayoría de los posts lo'
                   f' repite y unos pocos prueban algo nuevo.</p><ul class="wiz-lista">{lineas}</ul></div></div>')
    return (cab(b, 1, "¿Qué quieres lograr con esta campaña?")
            + f'<div class="wiz-ops">{ops}</div>' + repetir
            + pie(b, 1))


def paso_2(b: dict, mensajes: list[dict], prods: list[dict], objetivos: list[dict], preguntan: list[str] = ()) -> str:
    ops = "".join(op("mensajes", m["k"], m["titulo"], m.get("descripcion", ""), "checkbox", m["k"] in b["mensajes"],
                     em=f"«{escape(m['ejemplo'])}»" if m.get("ejemplo") else "", sugerido=bool(m.get("sugerido")))
                  for m in mensajes)  # el ejemplo: cómo sonaría un post con ese mensaje
    responder = ""
    if preguntan:  # lo que más le preguntaron sus clientes esta semana (Contenido sugerido)
        responder = ('<div class="wiz-bloque">' + abre("preguntan", "Responder lo que más te preguntan", b.get("preguntan"))
                     + '<p class="ayuda">Algunos posts contestan las dudas que más te llegan por mensaje y en'
                     ' comentarios.</p><ul class="wiz-lista">'
                     + "".join(f"<li>{escape(t)}</li>" for t in preguntan) + "</ul></div></div>")
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
           + "</div>" + campos_promo(b)
           if prods else
           '<p class="ayuda">Agrega tus productos en tu <a href="/panel/empresa">perfil de empresa</a> y podrás elegir'
           " uno para lanzarlo, darlo a conocer o hacerle una promoción.</p>")
        + "</div></div>")
    return (cab(b, 2, "¿Qué quieres comunicar?")
            + f'<div class="wiz-ops">{ops}</div>'
            + '<div class="wiz-bloque">' + abre("con_texto", "Cuéntalo en tus palabras", b.get("con_texto") or b["texto"])
            + '<p class="ayuda">Un dato, una fecha o una idea que no debe faltar.</p>'
            f'<textarea name="texto" rows="2" maxlength="{MAX_TEXTO}" aria-label="Tu idea" placeholder="Ej. Temporada'
            f' de otoño: queremos más visitas entre semana.">{escape(b["texto"])}</textarea></div></div>' + producto
            + responder + pie(b, 2))


def paso_3(b: dict, objetivos: list[dict]) -> str:
    obj = next((o for o in objetivos if o["k"] == b["objetivo"]), None)
    van = sugeridos(obj) if obj else set()
    ops = "".join(op("enfoques", k, v[0], v[1], "checkbox", k in b.get("enfoques", []),
                     em="Va con tu objetivo" if k in van else "")  # sin foto: solo el texto
                  for k, v in ENFOQUES.items())
    return (cab(b, 3, "¿De qué van a tratar tus posts?")
            + f'<div class="wiz-ops cuatro">{ops}</div>'
            + pie(b, 3))

def paso_4(b: dict, fotos: int) -> str:
    ops = ""
    for k, (t, d, color) in FORMATOS.items():  # todas las piezas las genera la IA completas: 1 imagen cada una
        em = "1 imagen" if k in campanas.FORMATOS else ""
        ops += op("formatos", k, t, d, "checkbox", k in b["formatos"], img("formato", k, color, t, alta=True), em)
    return (cab(b, 4, "¿Qué formatos te gustan?")
            + f'<div class="formatos-cam"><div class="wiz-ops cinco">{ops}</div>'
            '<div class="wiz-si titular"><h3 class="wiz-sub">Texto dentro de la imagen</h3><p class="ayuda">Para'
            ' Foto con titular: elige una, dos o las tres. No es el pie de foto: es lo que va escrito sobre la'
            ' imagen.</p>'
            '<div class="wiz-ops tres">'
            + "".join(op("textos_img", k, t, d, "checkbox", k in textos(b), img("texto", k, color, t, alta=True))
                      for k, (t, d, color) in TEXTO.items()) + "</div></div></div>"
            + nota("Carrusel y reel", "llegarán después. Las historias de Instagram también están en camino.") + pie(b, 4))


def paso_5(b: dict) -> str:
    ops = "".join(op("visual", k, t, d, "checkbox", k in b["visual"], img("visual", k, color, t))
                  for k, (t, d, color) in VISUAL.items())
    return (cab(b, 5, "¿Qué quieres que se vea en las imágenes?")
            + f'<div class="wiz-ops tres">{ops}</div>'
            + nota("Tip:", "si quieres que se vea tu producto real y no uno parecido, sube sus fotos en tu perfil de"
                   " empresa. Las usamos como referencia en cada imagen.") + pie(b, 5))


async def candidatos_ref(c: Cuenta, b: dict) -> list[str]:
    """Las fotos que puede elegir como referencia: las que ya eligió o subió aquí, las de Tu marca y las de sus
    productos (las que siguen en disco)."""
    with usar(c):
        marca = await marca_de(c)
        prods = await productos(c)
    tokens = b.get("referencias", []) + b.get("refs_subidas", []) + marca.get("fotos", []) + [
        t for p in prods for t in p.get("imagenes", []) if isinstance(t, str)]
    return [t for t in dict.fromkeys(tokens) if TOKEN_FOTO.match(t) and archivo(c, t).is_file()][:MAX_CANDIDATOS_REF]


def paso_6(b: dict, candidatos: list[tuple[str, str]] = ()) -> str:
    variedad = "".join(op("variedad", k, t, d, marcada=b["variedad"] == k, imagen=img("variedad", k, color, t, alta=True),
                          em="Recomendado" if k == "coherencia" else "")
                       for k, (t, d, color) in VARIEDAD.items())
    return (cab(b, 6, "¿Cómo quieres que se vean tus anuncios?" if de_producto(b) else "¿Cómo quieres que se vea esta campaña?")
            + '<h3 class="wiz-sub">Variedad visual</h3><p class="ayuda">Qué tan distintas se ven las fotos entre'
            ' sí: el lugar, el encuadre y la luz.</p>'
            f'<div class="wiz-ops dos ops-variedad">{variedad}</div>' + referencias(b, candidatos) + pie(b, 6))


def referencias(b: dict, candidatos: list[tuple[str, str]]) -> str:
    """Fotos de referencia (opcional): las elige de las que ya tiene o las sube; con ellas manda su estilo."""
    fotos = "".join(f'<label class="ref-cam"><input type="checkbox" name="referencias" value="{t}"'
                    f'{" checked" if t in b.get("referencias", []) else ""}><img src="{u}" alt="" loading="lazy"></label>'
                    for t, u in candidatos)
    return ('<h3 class="wiz-sub">Fotos de referencia <span class="opc">(opcional)</span></h3>'
            f'<p class="ayuda">De 3 a {brief.MAX_REFERENCIAS} fotos con el estilo que quieres (luz, colores, ambiente),'
            " parecidas entre sí. Mandan en la dirección visual: la IA copia su estilo, no lo que aparece en ellas.</p>"
            + (f'<div class="refs-cam">{fotos}</div>' if fotos else "")
            + '<label class="subir-ref">Subir fotos<input type="file" name="ref_nuevas" multiple'
            ' accept="image/jpeg,image/png,image/webp"></label>'
            f'<p class="ayuda chica">Elige o sube hasta {brief.MAX_REFERENCIAS}. Se guardan al pasar de paso.</p>')


def paso_7(b: dict) -> str:
    semanas = "".join(f'<option value="{n}"{" selected" if b["semanas"] == n else ""}>{n} semana{"s" if n > 1 else ""}'
                      "</option>" for n in campanas.SEMANAS)
    cuantos = ("" if de_producto(b) else  # en una de producto, uno por versión del anuncio
               f'<div><label for="w-n">Número de posts</label><input type="number" id="w-n" name="n_posts" min="1"'
               f' max="{MAX_POSTS}" value="{b["n_posts"]}"></div>'
               f'<div><label for="w-h">Número de historias</label><input type="number" id="w-h" name="n_historias" min="0"'
               f' max="{MAX_HISTORIAS}" value="{b.get("n_historias", 0)}"></div></div>'
               '<p class="ayuda" style="margin-top:.7rem">Las historias son verticales (9:16), se ven 24 horas y no llevan'
               " texto publicado: lo que se lee va en la imagen. Pon 0 si no quieres historias.</p>")
    return ((cab(b, 7, "¿Cuándo salen tus anuncios?")
             if de_producto(b) else
             cab(b, 7, "¿Cuánto y cuándo?"))
            + '<div class="wiz-campos cinco">'
            f'<div><label for="w-nombre">Nombre de la campaña</label><input type="text" id="w-nombre" name="nombre"'
            f' maxlength="{campanas.MAX_NOMBRE}" value="{escape(b["nombre"])}" placeholder="Ej. Otoño en tu negocio"></div>'
            f'<div><label for="w-inicio">Empieza</label><input type="date" id="w-inicio" name="inicio"'
            f' value="{b["inicio"]}"></div>'
            f'<div><label for="w-semanas">Duración</label><select id="w-semanas" name="semanas">{semanas}</select></div>'
            + (cuantos or "</div>")
            + '<h3 class="wiz-sub">Días en que publicas</h3><p class="ayuda">Los posts y las historias se reparten en'
            ' estos días. Si son más posts que días, algunos días llevan dos.</p><div class="wiz-chips">'
            + "".join(chip("dias", i, d, i in b["dias"], "checkbox") for i, d in enumerate(DIAS)) + "</div>"
            '<h3 class="wiz-sub">Horario</h3><p class="ayuda">Elige uno o varios para comparar.</p><div class="wiz-chips">'
            + "".join(chip("horarios", k, v, k in b["horarios"], "checkbox") for k, v in HORARIOS.items()) + "</div>"
            + pie(b, 7))


async def resumen(c: Cuenta, b: dict, objetivos: list[dict], mensajes: list[dict], prods: list[dict]) -> str:
    obj = next((o["titulo"] for o in objetivos if o["k"] == b["objetivo"]), "Sin elegir")
    msgs = " · ".join(m["titulo"] for m in mensajes if m["k"] in b["mensajes"]) or "Sin elegir"
    prod = next((p for p in prods if p["id"] == b["producto"]), None)
    producto = "Ninguno en especial"
    if prod:
        producto = f"{prod['nombre']} · {ACCIONES[b['accion']]}"
        if b["accion"] == "promo" and (promo := promo_texto(b)):
            producto += f" {promo}" + (f" del {campanas.fecha_larga(b['desde'])} al"
                                                   f" {campanas.fecha_larga(b['hasta'])}" if b["desde"] and b["hasta"] else "")
    enfoques = " · ".join(ENFOQUES[k][0] for k in b.get("enfoques", []) if k in ENFOQUES) or "Según tu objetivo"
    ini = date.fromisoformat(b["inicio"])
    fin = ini + timedelta(days=7 * b["semanas"] - 1)
    dias = ", ".join(DIAS[d] for d in b["dias"])
    horas = " y ".join(HORARIOS[h].split(" · ")[-1] for h in b["horarios"])
    imagenes_ = 0
    historias = b.get("n_historias", 0)
    imagenes_ = b["n_posts"] + historias  # cada pieza la genera la IA completa
    cantidad = f"{b['n_posts']} posts" + (f" y {campanas.cuantos(historias, 'historia')}" if historias else "")
    nombre = b["nombre"]
    if de_producto(b):
        cantidad, imagenes_ = campanas.cuantos(b["versiones"], "post"), b["versiones"]
        nombre = nombre or nombre_producto(b, prods)
    fechas = (f"{escape(nombre) or 'Sin nombre'} · {cantidad} · {campanas.de_a(ini, fin)} · {dias} · {horas}")
    bloques = [
        ("Tipo de campaña", TIPOS["producto"][0], TIPO, False), ("Producto", producto, PRODUCTO, True),
        ("Versiones del anuncio", f"{b['versiones']} versiones" + (f" · «{b['texto']}»" if b["texto"] else ""),
         OFERTA, False), ("Estilo", VARIEDAD[b["variedad"]][0], 6, False), ("Fechas", fechas, 7, True),
    ] if de_producto(b) else [
        ("Tipo de campaña", TIPOS["contenido"][0], TIPO, True),
        ("Objetivo", obj + (" · Repetir lo que funcionó" if b.get("funciona") else ""), 1, False),
        ("Mensaje", msgs + (f" · «{b['texto']}»" if b["texto"] else "")
         + (" · Responde lo que más te preguntan" if b.get("preguntan") else ""), 2, False),
        ("Producto", producto, 2, True), ("Enfoque del contenido", enfoques, 3, False),
        ("Formatos", " · ".join(FORMATOS[k][0] for k in b["formatos"] if k in FORMATOS)
         + (f" ({', '.join(TEXTO[k][0].lower() for k in textos(b))})" if "titular" in b["formatos"] else ""), 4, False),
        ("Estilo y variedad", VARIEDAD[b["variedad"]][0], 6, False),
        ("Cantidad y fechas", fechas, 7, True)]
    tarjetas = "".join(
        f'<div class="wiz-res{" ancho" if ancho else ""}">'
        + marco.cab_tarjeta(t, boton=f'<button class="editar" name="ir" value="{n}">Editar</button>')
        + f'<p>{v if t in ("Cantidad y fechas", "Fechas") else escape(v)}</p></div>' for t, v, n, ancho in bloques)
    with usar(c):
        quedan = await campanas.ia_restantes(c)
    editar = bool(b.get("cid"))
    return ('<div class="wiz-cab"><p class="ante">Resumen</p>'
            f'<h2>{"Revisa la estrategia de tu campaña" if editar else "Revisa tu campaña antes de generarla"}</h2></div>'
            f'<div class="wiz-resumen">{tarjetas}</div>{await cruce(c, b)}'
            f'<div class="wiz-total"><div><b>{cantidad}</b><span>en {b["semanas"]} semana'
            f'{"s" if b["semanas"] > 1 else ""}</span></div>'
            + ("<div><b>Anuncio</b><span>una sola idea, tu producto real</span></div>" if de_producto(b) else
               f'<div><b>{len(b["formatos"])} formato{"s" if len(b["formatos"]) != 1 else ""}</b>'
               "<span>repartidos parejo</span></div>")
            + f'<div><b>~{imagenes_} imágenes</b><span>de tu cupo, solo al generarlas</span></div>'
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
        + (f'<small class="visual">Cómo se ve: {escape(x["estetica"])}</small>' if x.get("estetica") else "")
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
        cuerpo = (campos(brief.CAMPOS_IDEA[:2]) + f'<div class="campo"><label>Pilares</label><div class="pilares">'
                  f"{pilares}</div></div>" + campos(brief.CAMPOS_IDEA[2:])
                  + '<h4 class="brief-sub">Cómo se ve</h4>' + campos(brief.CAMPOS_VISUAL))
        return (f'<div class="brief-ed" id="brief-{i}"><h3>Brief de “{escape(x["titulo"])}”</h3>'
                "<p>Puedes cambiar cualquier texto. Lo que dejes aquí es lo que se sigue en cada post.</p>"
                f"{cuerpo}</div>")

    return ('<div class="wiz-cab"><p class="ante">Brief</p><h2>Elige la idea de tu campaña</h2></div>'
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


async def funciono_de(c: Cuenta) -> str:
    """Lo que mejor le funcionó, si ya tiene resultados suficientes para repetirlo (campanas.CON_RESULTADOS); "" si no."""
    with usar(c):
        try:
            anteriores = await campanas.informe.todas(c, await campanas.todas_las(c))
            if await campanas.con_resultados(c, anteriores) < campanas.CON_RESULTADOS:
                return ""
            return campanas.lo_que_funciono(await campanas.informe.ganadores(c, anteriores))
        except Exception:
            campanas.log.exception("No se pudieron leer los resultados de %s", c.slug)
            return ""


async def preguntan_de(c: Cuenta) -> list[str]:
    """Lo que más le preguntaron sus clientes (Contenido sugerido de hoy, si ya se calculó), para el paso Mensaje."""
    datos = await campanas.ya_sugerido(c)
    return [str(t.get("tema", "")).strip() for t in datos.get("temas", []) if str(t.get("tema", "")).strip()][:MAX_PREGUNTAN]


async def pagina(c: Cuenta, paso: int, aviso: str = "", b: dict | None = None):
    hay_empresa, hay_marca = await lista_para_empezar(c)
    b = b or await leer(c)
    if not (hay_empresa and hay_marca):
        cuerpo = bloqueo(hay_empresa, hay_marca)
    else:
        objetivos, mensajes = await opciones(c, b["objetivo"])
        prods = await productos(c)
        if paso == BRIEF and not b.get("brief"):
            paso = RESUMEN
        if paso in OCULTOS:
            paso = vecino(b, paso - 1, 1)
        if paso not in (*visibles(b), RESUMEN, BRIEF):  # un paso del otro tipo de campaña
            paso = TIPO
        if paso in (RESUMEN, BRIEF) and faltan(b):
            paso = faltan(b)[0]
        if paso == TIPO:
            contenido = paso_tipo(b)
        elif paso == PRODUCTO:
            contenido = paso_producto(b, [(x, url_imagen(c, x["imagenes"][0]) if foto_de(c, x) else "") for x in prods],
                                      await tienda_shopify.mas_vendidos(c, prods))
        elif paso == OFERTA:
            contenido = paso_oferta(b, prods)
        elif paso == 1:
            contenido = paso_1(b, objetivos, await funciono_de(c))
        elif paso == 2:
            contenido = paso_2(b, mensajes, prods, objetivos, await preguntan_de(c))
        elif paso == 3:
            contenido = paso_3(b, objetivos)
        elif paso == 4:
            with usar(c):
                fotos = len(campanas.fotos_marca(c, await marca_de(c)))
            contenido = paso_4(b, fotos)
        elif paso == 5:
            contenido = paso_5(b)
        elif paso == 6:
            contenido = paso_6(b, [(t, url_imagen(c, t)) for t in await candidatos_ref(c, b)])
        elif paso == 7:
            contenido = paso_7(b)
        elif paso == BRIEF:
            contenido = paso_brief(b, await campanas.ia_restantes(c))
        else:
            contenido = await resumen(c, b, objetivos, mensajes, prods)
        # Un solo formulario con el riel adentro; el primer botón (invisible) es el que usa Enter: seguir.
        sigue = BRIEF if paso == BRIEF else RESUMEN if paso == RESUMEN else vecino(b, paso, 1)
        cuerpo = (f'<form method="post" action="{URL}" class="wiz" id="wiz-form" enctype="multipart/form-data">'
                  f'<button class="enter-cam" name="ir" value="{sigue}" tabindex="-1" aria-hidden="true">Seguir</button>'
                  f'<input type="hidden" name="paso" value="{paso}">{riel(b, paso)}'
                  f'<div class="wiz-main">{contenido}</div></form>')
    editar = bool(b.get("cid"))
    return marco.pagina(
        await marco.menu_de(c, "contenido", sub="campanas")
        + campanas.cabeza(f'<a class="boton secundario chico" href="{campanas.BASE}/{escape(str(b["cid"]))}">'
                          '‹ Volver a la campaña</a>' if editar else "", "",
                          "Planificación" if editar else "Nueva campaña")
        + '<div class="oscuro-pag"><main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + cuerpo + "</div></main></div>")


def _paso(valor) -> int:
    return int(valor) if str(valor).isdigit() and (1 <= int(valor) <= BRIEF or int(valor) in NOMBRES_PRODUCTO) else TIPO


def foto_de(c: Cuenta, prod: dict) -> bool:
    """El producto tiene su primera foto en disco (para su tarjeta)."""
    fotos = [t for t in prod.get("imagenes", []) if isinstance(t, str) and TOKEN_FOTO.match(t)]
    return bool(fotos) and fotos[0] == prod["imagenes"][0] and archivo(c, fotos[0]).is_file()


@router.get("/campanas/nueva")
async def ver(request: Request, c: marco.Sesion):
    if c.imagenes_mes <= 0:
        return sin_plan()
    q = request.query_params
    if cid := q.get("editar"):  # "Ver planificación" de una campaña: el resumen con lo que eligió
        if not (camp := await campanas.leer(c, cid)):
            return RedirectResponse(campanas.BASE, status_code=303)
        await escribir(c, de_campana(camp))
        return RedirectResponse(f"{URL}?paso={RESUMEN}", status_code=303)
    if q.get("empezar") == "1" or (not q.get("paso") and (await leer(c)).get("cid")):
        await c.store.set(BORRADOR, "{}")
    return await pagina(c, _paso(q.get("paso", TIPO)))


@router.post("/campanas/nueva")
async def guardar(request: Request, c: marco.Formulario):
    if c.imagenes_mes <= 0:
        return sin_plan()
    f = await request.form()
    paso, b = _paso(f.get("paso")), await leer(c)
    objetivos, mensajes = await opciones(c, b["objetivo"])
    b.setdefault("semilla", int(time.time()))  # el mismo cruce en el resumen y al escribir los posts
    if paso not in (RESUMEN, BRIEF):
        antes = {k: v for k, v in b.items() if k not in ("hechos", "brief")}
        error = del_form(f, paso, b, objetivos, mensajes, await productos(c))
        if paso == 6:
            await referencias_del_form(c, f, b)
        if b.get("brief") and {k: v for k, v in b.items() if k not in ("hechos", "brief")} != antes:
            b.pop("brief")  # cambió la campaña: el brief de antes ya no le queda
        hechos = set(b.get("hechos", []))
        (hechos.discard if error else hechos.add)(paso)
        b["hechos"] = sorted(hechos)
        await escribir(c, b)
        ir = _paso(f.get("ir", paso))
        if error and orden(b, ir) > orden(b, paso):  # adelante no se pasa con algo que falta; atrás o a otro paso, sí
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
    datos["nombre"] = nombre_del_brief(b, datos)
    return await campanas.armar(c, datos, camp, borrador=True)


def nombre_del_brief(b: dict, datos: dict) -> str:
    """La campaña se llama como la idea del brief que eligió, salvo que él le haya puesto otro nombre
    (si el nombre era el de otra idea del brief, cambia a la elegida)."""
    titulo = str(datos["brief"].get("titulo") or "").strip()
    puesto = b.get("nombre", "").strip()
    ideas = {str(x.get("titulo") or "").strip() for x in b["brief"]["conceptos"]}
    return titulo[:campanas.MAX_NOMBRE] if titulo and (not puesto or puesto in ideas) else datos["nombre"]


async def referencias_del_form(c: Cuenta, f, b: dict) -> None:
    """Las fotos de referencia marcadas (de las que podía elegir) y las que subió, hasta MAX_REFERENCIAS."""
    validas = set(await candidatos_ref(c, b))
    elegidas = [t for t in dict.fromkeys(str(x) for x in f.getlist("referencias")) if t in validas]
    subidas = [x for x in f.getlist("ref_nuevas") if getattr(x, "filename", "")]
    if subidas and len(elegidas) < brief.MAX_REFERENCIAS:
        tokens, _ = await guardar_fotos(c, subidas, brief.MAX_REFERENCIAS - len(elegidas))
        elegidas += tokens
        b["refs_subidas"] = (tokens + b.get("refs_subidas", []))[:MAX_CANDIDATOS_REF]
    b["referencias"] = elegidas[:brief.MAX_REFERENCIAS]


async def adn_de(c: Cuenta, b: dict) -> str:
    """El ADN visual de sus referencias, guardado en el borrador mientras no cambien las fotos."""
    refs = b.get("referencias", [])
    if not refs:
        return ""
    if (b.get("adn") or {}).get("fotos") != refs:
        with usar(c):
            b["adn"] = {"fotos": refs, "texto": await brief.adn_visual(c, refs)}
    return b["adn"]["texto"]


# Lo que va diciendo la espera, según avanza la barra (marco.paso_de).
PASOS_BRIEF = ("Leyendo tu empresa y tu marca…", "Revisando tus campañas anteriores…", "Generando opciones de campaña…",
               "Definiendo el mensaje y los pilares…", "Generando dirección visual…", "Puliendo las ideas…")
PASOS_RAPIDA = ("Leyendo tu empresa y tu marca…", "Generando opciones de campaña…", "Escribiendo el brief…",
                "Generando dirección visual…", "Repartiendo los posts en el calendario…", "Generando contenido…",
                "Escribiendo los textos…", "Revisando que todo cuadre…")


async def crear_brief(c: Cuenta, b: dict, datos: dict):
    """Las tres ideas de campaña con su brief (una llamada a Claude, cuenta como una propuesta del día)."""
    if not await campanas.ia_restantes(c):
        return await pagina(c, RESUMEN, "Por hoy ya no quedan propuestas; mañana puedes pedir más.", b)
    with usar(c):
        otras = [x for x in await campanas.todas_las(c) if x.get("id") != b.get("cid")]

    async def trabajo():
        with usar(c):
            await campanas.usar_ia(c)
            datos["adn_visual"] = await adn_de(c, b)  # primero se leen las referencias: mandan en la dirección visual
            conceptos = await brief.conceptos(c, datos, otras)
        b["brief"] = {"conceptos": conceptos, "elegido": 0}
        await escribir(c, b)
        yield f"{URL}?paso={BRIEF}"

    return await campanas.trabajando(c, "Pensando las ideas de tu campaña…", "Tarda unos segundos.", trabajo,
                               f"{URL}?paso={RESUMEN}", segundos=30, pasos=PASOS_BRIEF)


# --- Crear con IA: solo el objetivo y las fechas; lo demás lo decide la IA ---

RAPIDA = f"{campanas.BASE}/rapida"
POSTS_SEMANA = 4  # Crear con IA: posts por semana, de lunes a viernes


def pagina_rapida_html(b: dict, objetivos: list[dict]) -> str:
    ops = "".join(op("objetivo", o["k"], o["titulo"], o.get("descripcion", ""), marcada=o["k"] == b["objetivo"],
                     sugerido=bool(o.get("sugerido"))) for o in objetivos)
    semanas = "".join(f'<option value="{n}"{" selected" if b["semanas"] == n else ""}>{n} semana{"s" if n > 1 else ""}'
                      "</option>" for n in campanas.SEMANAS)
    return (f'<form method="post" action="{RAPIDA}" class="wiz rapida-cam"><div class="wiz-main">'
            '<div class="wiz-cab"><p class="ante">Crear con IA</p><h2>¿Qué quieres lograr con esta campaña?</h2></div>'
            f'<div class="wiz-ops">{ops}</div>'
            '<h3 class="wiz-sub">Cuándo</h3><div class="wiz-campos dos-r">'
            f'<div><label for="r-inicio">Empieza</label><input type="date" id="r-inicio" name="inicio"'
            f' value="{b["inicio"]}"></div>'
            f'<div><label for="r-semanas">Duración</label><select id="r-semanas" name="semanas">{semanas}</select></div>'
            "</div>"
            '<details class="prompt-img rapida-extra"><summary>¿Algo que debamos saber? (opcional)</summary>'
            f'<textarea name="texto" rows="3" maxlength="{MAX_TEXTO}" placeholder="Ej. Tenemos 20 % de descuento en'
            f' noviembre, o abrimos sucursal el día 15.">{escape(b.get("texto", ""))}</textarea></details>'
            '<div class="wiz-pie">'
            f'<a class="boton secundario" href="{campanas.BASE}">Cancelar</a>'
            f'<span>{POSTS_SEMANA} posts por semana, sin imágenes</span>'
            f'<button class="boton" name="accion" value="crear">{BRILLOS} Crear con IA</button></div>'
            "</div></form>")


async def pagina_rapida(c: Cuenta, aviso: str = "", b: dict | None = None):
    hay_empresa, hay_marca = await lista_para_empezar(c)
    b = b or inicial(await marca_de(c))
    if not (hay_empresa and hay_marca):
        cuerpo = bloqueo(hay_empresa, hay_marca)
    else:
        objetivos, _ = await opciones(c)
        if not b["objetivo"] and objetivos:  # uno ya marcado: el sugerido de esta semana, si hay
            b["objetivo"] = next((o["k"] for o in objetivos if o.get("sugerido")), objetivos[0]["k"])
        cuerpo = pagina_rapida_html(b, objetivos)
    return marco.pagina(
        await marco.menu_de(c, "contenido", sub="campanas")
        + campanas.cabeza("", "", "Crear con IA")
        + '<div class="oscuro-pag"><main><div class="contenedor">'
        + (f'<div class="aviso">{escape(aviso)}</div>' if aviso else "")
        + cuerpo + "</div></main></div>")


def borrador_rapido(b: dict, f, objetivos: list[dict], mensajes: list[dict]) -> str:
    """El borrador completo con lo poco que eligió; devuelve '' o lo que falta."""
    g = lambda k: str(f.get(k, "")).strip()  # noqa: E731
    if g("objetivo") not in {o["k"] for o in objetivos}:
        return "Elige qué quieres lograr."
    b["objetivo"] = g("objetivo")
    b["inicio"] = _fecha(g("inicio")) or b["inicio"]
    b["semanas"] = int(g("semanas")) if g("semanas").isdigit() and int(g("semanas")) in campanas.SEMANAS else 2
    b["texto"] = g("texto")[:MAX_TEXTO]
    b["con_texto"] = bool(b["texto"])
    b["mensajes"] = [m["k"] for m in mensajes][:MAX_MENSAJES]
    b["formatos"], b["horarios"] = ["ia"], ["ia"]  # la IA reparte todos los formatos y los horarios
    b["n_posts"] = min(POSTS_SEMANA * b["semanas"], MAX_POSTS)
    return "" if b["mensajes"] else "Ese objetivo todavía no tiene mensajes; elige otro."


@router.get("/campanas/rapida")
async def ver_rapida(c: marco.Sesion):
    if c.imagenes_mes <= 0:
        return sin_plan()
    return await pagina_rapida(c)


@router.post("/campanas/rapida")
async def crear_rapida(request: Request, c: marco.Formulario):
    if c.imagenes_mes <= 0:
        return sin_plan()
    hay_empresa, hay_marca = await lista_para_empezar(c)
    if not (hay_empresa and hay_marca):
        return RedirectResponse(RAPIDA, status_code=303)
    f = await request.form()
    b = inicial(await marca_de(c))
    b["semilla"] = int(time.time())
    objetivos, _ = await opciones(c)
    _, mensajes = await opciones(c, str(f.get("objetivo", "")))
    if error := borrador_rapido(b, f, objetivos, mensajes):
        return await pagina_rapida(c, error, b)
    with usar(c):
        hoy = tools.now().date()
    if date.fromisoformat(b["inicio"]) < hoy:
        return await pagina_rapida(c, "La campaña no puede empezar en un día que ya pasó.", b)
    if await campanas.ia_restantes(c) < 2:  # dos propuestas del día: el brief y los posts
        return await pagina_rapida(c, "Por hoy ya no quedan propuestas; mañana puedes pedir más.", b)
    datos = await a_campana(c, b)
    if falta := campanas.falta_en(datos):
        return await pagina_rapida(c, falta, b)
    with usar(c):
        todas = await campanas.todas_las(c)
        anteriores = await campanas.informe.todas(c, todas)
        fotos = len(campanas.fotos_marca(c, await marca_de(c)))
    camp = {**datos, "id": int(time.time() * 1000), "posts": [], "creada": time.time()}
    if not await limites.cabe_generacion(c.store):
        return await pagina_rapida(c, limites.TOPE_GENERACIONES, b)

    async def trabajo():
        with usar(c):
            await campanas.usar_ia(c)
            yield "Pensando la idea de tu campaña…"
            conceptos = await brief.conceptos(c, datos, todas)
            b["brief"] = {"conceptos": conceptos, "elegido": 0}  # la primera idea: la que la IA pone primero
            camp["brief"] = conceptos[0]
            camp["nombre"] = nombre_del_brief(b, camp)
            camp["asistente"] = {k: v for k, v in b.items() if k not in ("cid", "hechos", "adn")}
            await campanas.usar_ia(c)
            yield "Escribiendo los posts…"
            hecha = await campanas.proponer(c, camp, anteriores, None, fotos)
            await campanas.guardar(c, hecha)
        yield campanas.url_de(camp)

    return await campanas.trabajando(c, "Creando tu campaña con IA…", "Tarda alrededor de un minuto.", trabajo, RAPIDA,
                                     segundos=70, pasos=PASOS_RAPIDA)
