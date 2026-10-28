"""Genera el blog y las páginas por giro de skandar.online.

Blog: a partir de blog/*.md.

Cada artículo es un .md con cabecera "clave: valor" (titulo, descripcion,
categoria, portada, fecha, lectura, cta), una línea "---" y el cuerpo en un
markdown sencillo: párrafos, "## " subtítulos, "- " listas, "> " recuadro,
**negritas** y [enlaces](/ruta).

Páginas por giro ("Asistente virtual para restaurantes"): a partir de giros/*.md.
La cabecera lleva titulo, descripcion, nombre, h1, bajada, cuenta, demo, idea e
idea_nota; el cuerpo se parte en secciones "## chat" (líneas "- cliente: ..." y
"- agente: ..."), "## preguntas", "## contenido" (líneas "- Título: texto") y
"## faq" (líneas "- ¿Pregunta? | Respuesta").

Páginas sueltas ("Qué es Skandar"): a partir de paginas/*.md, con cabecera
titulo, descripcion y antetitulo y el mismo markdown del blog; salen en web/<slug>.html.

Escribe web/blog/, web/para/, las páginas sueltas, web/sitemap.xml y web/llms.txt.
Uso: python scripts/generar_blog.py
"""

import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = RAIZ / "blog"
GIROS = RAIZ / "giros"
PAGINAS = RAIZ / "paginas"
WEB = RAIZ / "web"
SITIO = "https://skandar.online"
CSS_VERSION = "2026-10-28e"

# Orden en el índice: el primero es el destacado.
ORDEN = [
    "contestar-mensajes-instagram-24-horas",
    "que-publicar-en-instagram",
    "asistente-virtual-pasar-a-persona",
    "calendario-de-contenido-instagram",
    "comenta-y-recibe-por-dm",
    "community-manager-con-inteligencia-artificial",
    "skandar-vs-manychat",
    "alternativas-a-manychat",
]

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

CTAS = {
    "asistente": ("Tu asistente contesta mientras tú trabajas.",
                  "Asistente virtual desde US$29 al mes más IVA. Sin instalación.",
                  "https://ig.me/m/skandar.online?text=Me%20interesa%20el%20Asistente%20virtual",
                  "Quiero el Asistente virtual"),
    "community": ("Tu contenido, con lo que pasa en tu cuenta.",
                  "Community Manager desde US$79 al mes más IVA. Incluye el Asistente virtual.",
                  "https://ig.me/m/skandar.online?text=Me%20interesa%20el%20Community%20Manager",
                  "Quiero el Community Manager"),
}


def fecha_larga(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def slugify(texto):
    t = texto.lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def en_linea(texto):
    t = html.escape(texto, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', t)


def cuerpo_html(md):
    """Devuelve (html, [(id, subtítulo)])."""
    partes, indice, lista = [], [], []

    def cerrar_lista():
        if lista:
            partes.append("<ul>" + "".join(f"<li>{x}</li>" for x in lista) + "</ul>")
            lista.clear()

    for bloque in md.strip().split("\n"):
        linea = bloque.strip()
        if linea.startswith("- "):
            lista.append(en_linea(linea[2:]))
            continue
        cerrar_lista()
        if not linea:
            continue
        if linea.startswith("## "):
            sub = linea[3:]
            ident = slugify(sub)
            indice.append((ident, sub))
            partes.append(f'<h2 id="{ident}">{en_linea(sub)}</h2>')
        elif linea.startswith("> "):
            partes.append(f'<div class="recuadro"><p>{en_linea(linea[2:])}</p></div>')
        else:
            partes.append(f"<p>{en_linea(linea)}</p>")
    cerrar_lista()
    return "\n".join(partes), indice


def leer(slug):
    texto = (FUENTES / f"{slug}.md").read_text(encoding="utf-8")
    cabecera, cuerpo = texto.split("\n---\n", 1)
    datos = dict(l.split(": ", 1) for l in cabecera.strip().split("\n"))
    datos["slug"] = slug
    datos["cuerpo"] = cuerpo
    return datos


def cabeza(titulo, descripcion, url, tipo="website", extra=""):
    t, d = html.escape(titulo), html.escape(descripcion)
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="description" content="{d}">
<meta name="theme-color" content="#18191C">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{tipo}">
<meta property="og:locale" content="es_MX">
<meta property="og:site_name" content="Skandar">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:image" content="{SITIO}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
{extra}<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;500;600;700;800;900&display=swap">
<link rel="stylesheet" href="/estilos.css?v={CSS_VERSION}">
</head>
<body>
<header>
  <div class="contenedor">
    <a class="marca" href="/" aria-label="Skandar, inicio"><img src="/logo-skandar.svg" alt="Skandar" width="110" height="30"></a>
    <nav class="nav-links">
      <a href="/#contenido">Contenido</a>
      <a href="/#como-funciona">Cómo funciona</a>
      <a href="/#planes">Planes</a>
      <a href="/blog/">Blog</a>
    </nav>
    <div class="nav-acciones">
      <a class="entrar" href="https://console.skandar.online/panel">Entrar</a>
      <a class="boton chico" href="https://ig.me/m/skandar.online">Escríbenos</a>
    </div>
  </div>
</header>

<main>
"""


PIE = """</main>

<footer>
  <div class="contenedor">
    <a class="marca" href="/" aria-label="Skandar, inicio"><img src="/logo-skandar.svg" alt="Skandar" width="81" height="22"></a>
    <span>© 2026 Skandar · México</span>
    <div class="pie-enlaces">
      <a href="/blog/">Blog</a>
      <a href="/para/">Por giro</a>
      <a href="/que-es-skandar">Qué es Skandar</a>
      <a href="/privacidad">Aviso de privacidad</a>
      <a href="mailto:hola@skandar.online">Contacto</a>
      <a href="https://console.skandar.online/panel">Entrar</a>
    </div>
  </div>
</footer>
<script src="/sitio.js?v=2026-10-28b"></script>
</body>
</html>
"""


def ld(objeto):
    return ('<script type="application/ld+json">\n'
            + json.dumps(objeto, ensure_ascii=False, indent=2)
            + "\n</script>\n")


def tarjeta(a, destacado=False):
    clase = "tarjeta articulo-tarjeta" + (" articulo-destacado" if destacado else "")
    return f"""        <a class="{clase}" href="/blog/{a['slug']}" data-categoria="{html.escape(a['categoria'])}">
          <div class="portadilla"><span>{html.escape(a['portada'])}</span></div>
          <div class="cuerpo">
            <span class="categoria">{html.escape(a['categoria'])}</span>
            <h3>{html.escape(a['titulo'])}</h3>
            <p>{html.escape(a['descripcion'])}</p>
            <span class="datos">{fecha_larga(a['fecha'])} · {a['lectura']} min de lectura</span>
          </div>
        </a>
"""


def pagina_articulo(a, todos):
    url = f"{SITIO}/blog/{a['slug']}"
    cuerpo, indice = cuerpo_html(a["cuerpo"])
    esquema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "BlogPosting",
                "headline": a["titulo"],
                "description": a["descripcion"],
                "image": f"{SITIO}/og.png",
                "datePublished": a["fecha"],
                "dateModified": a.get("modificado", a["fecha"]),
                "inLanguage": "es-MX",
                "mainEntityOfPage": url,
                "author": {"@type": "Organization", "name": "Skandar", "url": f"{SITIO}/"},
                "publisher": {"@type": "Organization", "name": "Skandar",
                              "logo": {"@type": "ImageObject", "url": f"{SITIO}/apple-touch-icon.png"}},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{SITIO}/"},
                    {"@type": "ListItem", "position": 2, "name": "Blog", "item": f"{SITIO}/blog/"},
                    {"@type": "ListItem", "position": 3, "name": a["titulo"], "item": url},
                ],
            },
        ],
    }
    extra = (f'<meta property="article:published_time" content="{a["fecha"]}">\n'
             f'<meta property="article:section" content="{html.escape(a["categoria"])}">\n' + ld(esquema))
    titulo_cta, texto_cta, enlace_cta, boton_cta = CTAS[a["cta"]]
    indice_html = "".join(f'<li><a href="#{i}">{html.escape(s)}</a></li>' for i, s in indice)
    misma = [o for o in todos if o["slug"] != a["slug"] and o["categoria"] == a["categoria"]]
    otras = [o for o in todos if o["slug"] != a["slug"] and o["categoria"] != a["categoria"]]
    relacionados = "".join(tarjeta(o) for o in (misma + otras)[:3])
    return cabeza(f"{a['titulo']} · Skandar", a["descripcion"], url, "article", extra) + f"""  <section class="oscuro articulo-cabecera">
    <div class="contenedor">
      <p class="migas"><a href="/">Inicio</a> / <a href="/blog/">Blog</a> / {html.escape(a['categoria'])}</p>
      <p class="antetitulo">{html.escape(a['categoria'])}</p>
      <h1>{html.escape(a['titulo'])}</h1>
      <p class="bajada">{html.escape(a['descripcion'])}</p>
      <p class="datos">Por el equipo de Skandar · {fecha_larga(a['fecha'])} · {a['lectura']} min de lectura</p>
    </div>
  </section>

  <section class="claro articulo">
    <div class="contenedor angosto">
      <aside class="tarjeta indice" aria-label="En este artículo"><strong>En este artículo</strong><ol>{indice_html}</ol></aside>
{cuerpo}
      <div class="cta-articulo oscuro">
        <h3>{titulo_cta}</h3>
        <p>{texto_cta}</p>
        <div class="acciones izquierda-acciones"><a class="boton" href="{enlace_cta}">{boton_cta}</a><a class="boton contorno" href="/#planes">Ver planes</a></div>
      </div>
    </div>
  </section>

  <section class="banda relacionados">
    <div class="contenedor">
      <p class="antetitulo">Sigue leyendo</p>
      <h2>Artículos relacionados<span class="punto">.</span></h2>
      <div class="articulos">
{relacionados}      </div>
    </div>
  </section>
""" + PIE


def pagina_indice(todos):
    url = f"{SITIO}/blog/"
    descripcion = ("Guías prácticas para contestar mejor los mensajes de Instagram de tu negocio "
                   "y publicar con lo que ya sabes de tus clientes.")
    esquema = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": "Blog de Skandar",
        "url": url,
        "description": descripcion,
        "inLanguage": "es-MX",
        "publisher": {"@type": "Organization", "name": "Skandar", "url": f"{SITIO}/"},
        "blogPost": [{"@type": "BlogPosting", "headline": a["titulo"], "url": f"{SITIO}/blog/{a['slug']}",
                      "datePublished": a["fecha"]} for a in todos],
    }
    categorias = list(dict.fromkeys(a["categoria"] for a in todos))
    filtros = '<button type="button" class="pastilla activa" data-filtro="">Todo</button>' + "".join(
        f'<button type="button" class="pastilla" data-filtro="{html.escape(c)}">{html.escape(c)}</button>'
        for c in categorias)
    tarjetas = "".join(tarjeta(a, i == 0) for i, a in enumerate(todos))
    return cabeza("Blog: Instagram para tu negocio · Skandar", descripcion, url,
                  extra=ld(esquema)) + f"""  <section class="oscuro blog-portada">
    <div class="contenedor">
      <p class="antetitulo">Blog</p>
      <h1>Instagram para tu negocio<span class="punto">.</span></h1>
      <p class="bajada">{descripcion}</p>
      <div class="filtros">{filtros}</div>
    </div>
  </section>

  <section class="claro">
    <div class="contenedor">
      <div class="articulos">
{tarjetas}      </div>
    </div>
  </section>

  <section class="oscuro centro cierre">
    <div class="contenedor">
      <h2>Ponlo en práctica<span class="punto">.</span></h2>
      <p class="bajada">Conecta tu Instagram y deja que la plataforma conteste tus mensajes y proponga tu contenido.</p>
      <div class="acciones">
        <a class="boton" href="https://ig.me/m/skandar.online">Escribir a @skandar.online</a>
        <a class="boton contorno" href="/#planes">Ver planes</a>
      </div>
    </div>
  </section>
""" + PIE


# Páginas por giro. Los precios y lo que incluye cada plan son los mismos de la portada.
PLANES = f"""  <section class="banda centro" id="planes">
    <div class="contenedor">
      <p class="antetitulo">Planes</p>
      <h2>Dos planes. Una sola plataforma.</h2>
      <p class="bajada">Sin costo de instalación. Conectas tu Instagram y lo configuras desde tu panel.</p>
      <div class="planes">
        <article class="plan">
          <h3>Asistente virtual</h3>
          <p class="resumen">Contesta tus mensajes.</p>
          <p class="precio">US$29 <small>+ IVA al mes</small></p>
          <p class="instalacion">Sin instalación · 1,000 respuestas al mes</p>
          <ul>
            <li>Respuesta a mensajes directos 24/7</li>
            <li>Tu información, tus precios y tus preguntas frecuentes</li>
            <li>Memoria por cliente</li>
            <li>Se pausa cuando tú contestas</li>
          </ul>
          <a class="boton contorno" href="{CTAS['asistente'][2]}">{CTAS['asistente'][3]}</a>
        </article>
        <article class="plan destacado">
          <span class="popular">Más completo</span>
          <h3>Community Manager</h3>
          <p class="resumen">Además crea tu contenido con lo que pasa en tu cuenta.</p>
          <p class="precio">US$79 <small>+ IVA al mes</small></p>
          <p class="instalacion">Sin instalación · 2,500 respuestas y 12 posts al mes</p>
          <ul>
            <li>Todo lo del Asistente virtual</li>
            <li>Campañas con calendario automático</li>
            <li>Ideas a partir de tus métricas, DMs y comentarios</li>
            <li>Imágenes y textos con tu marca</li>
            <li>Publicación programada</li>
          </ul>
          <a class="boton" href="{CTAS['community'][2]}">{CTAS['community'][3]}</a>
        </article>
      </div>
      <p class="nota">Se paga mes a mes y puedes cancelar cuando quieras. Precios en dólares estadounidenses, más IVA.</p>
    </div>
  </section>
"""

# Preguntas que aplican a cualquier giro; van después de las propias de cada página.
FAQ_COMUN = [
    ("¿Necesito instalar algo?",
     "No. Conectas tu Instagram en unos minutos y todo lo demás lo haces desde tu panel, en el navegador."),
    ("¿Qué pasa si un cliente quiere hablar con una persona?",
     "Tu asistente se pausa en esa conversación y te avisa. También se pausa cuando tú contestas a mano, para no interrumpirte."),
    ("¿Puedo cancelar cuando quiera?",
     "Sí. Se paga mes a mes, sin plazo forzoso y sin costo de instalación."),
]


def leer_giro(slug):
    texto = (GIROS / f"{slug}.md").read_text(encoding="utf-8")
    cabecera, cuerpo = texto.split("\n---\n", 1)
    g = dict(l.split(": ", 1) for l in cabecera.strip().split("\n"))
    g["slug"] = slug
    secciones, actual = {}, None
    for linea in cuerpo.strip().split("\n"):
        linea = linea.strip()
        if linea.startswith("## "):
            actual = secciones.setdefault(linea[3:].strip(), [])
        elif linea.startswith("- ") and actual is not None:
            actual.append(linea[2:])
    g["chat"] = [l.split(": ", 1) for l in secciones["chat"]]
    g["preguntas"] = secciones["preguntas"]
    g["contenido"] = [l.split(": ", 1) for l in secciones["contenido"]]
    g["faq"] = [tuple(x.strip() for x in l.split(" | ", 1)) for l in secciones["faq"]] + FAQ_COMUN
    return g


def pagina_giro(g, todos):
    url = f"{SITIO}/para/{g['slug']}"
    demo = html.escape(g["demo"])
    enlace_demo = "https://ig.me/m/skandar.online?text=" + quote(g["demo"])
    esquema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Service",
                "name": g["titulo"],
                "serviceType": "Inteligencia artificial para Instagram",
                "description": g["descripcion"],
                "audience": {"@type": "BusinessAudience", "name": g["nombre"]},
                "areaServed": "MX",
                "provider": {"@type": "Organization", "name": "Skandar", "url": f"{SITIO}/"},
                "offers": [
                    {"@type": "Offer", "name": "Asistente virtual", "price": "29", "priceCurrency": "USD",
                     "url": f"{url}#planes"},
                    {"@type": "Offer", "name": "Community Manager", "price": "79", "priceCurrency": "USD",
                     "url": f"{url}#planes"},
                ],
            },
            {
                "@type": "FAQPage",
                "mainEntity": [{"@type": "Question", "name": p,
                                "acceptedAnswer": {"@type": "Answer", "text": r}} for p, r in g["faq"]],
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{SITIO}/"},
                    {"@type": "ListItem", "position": 2, "name": "Por giro", "item": f"{SITIO}/para/"},
                    {"@type": "ListItem", "position": 3, "name": g["nombre"], "item": url},
                ],
            },
        ],
    }
    iniciales = "".join(w[0] for w in g["cuenta"].replace("_", " ").split()[:2]).upper()
    burbujas = "".join(f'            <div class="burbuja {quien}">{html.escape(texto)}</div>\n'
                       for quien, texto in g["chat"])
    preguntas = "".join(f'        <div class="funcion"><span class="num">{i:02d}</span><h3>{html.escape(p)}</h3></div>\n'
                        for i, p in enumerate(g["preguntas"], 1))
    ideas = "".join(f"          <li><span><strong>{html.escape(t)}.</strong> {html.escape(x[:1].upper() + x[1:])}</span></li>\n"
                    for t, x in g["contenido"])
    faq = "".join(f"        <details><summary>{html.escape(p)}</summary><p>{html.escape(r)}</p></details>\n"
                  for p, r in g["faq"])
    otros = "".join(f'<a class="pastilla" href="/para/{o["slug"]}">{html.escape(o["nombre"])}</a>'
                    for o in todos if o["slug"] != g["slug"])
    return cabeza(f"{g['titulo']} · Skandar", g["descripcion"], url, extra=ld(esquema)) + f"""  <section class="oscuro portada">
    <div class="contenedor portada-rejilla">
      <div>
        <p class="migas"><a href="/">Inicio</a> / <a href="/para/">Por giro</a> / {html.escape(g['nombre'])}</p>
        <p class="antetitulo">{html.escape(g['titulo'])}</p>
        <h1>{html.escape(g['h1'])}<span class="punto">.</span></h1>
        <p class="bajada">{html.escape(g['bajada'])}</p>
        <div class="acciones">
          <a class="boton" href="{enlace_demo}">Pedir una demo por Instagram</a>
          <a class="boton contorno" href="#planes">Ver planes</a>
        </div>
        <p class="micro">Sin costo de instalación · Lo configuras desde tu panel · Responde a cualquier hora</p>
      </div>
      <div class="visual" aria-hidden="true">
        <div class="halo"></div>
        <div class="telefono">
          <div class="telefono-cabecera"><div class="avatar">{iniciales}</div><div>{html.escape(g['cuenta'])}<div class="en-linea">● Asistente activo</div></div></div>
          <div class="chat">
{burbujas}          </div>
        </div>
        <div class="tarjeta-idea">
          <span class="etiqueta">IDEA DE CONTENIDO</span>
          <div class="arte-post">{html.escape(g['idea']).replace("&lt;br&gt;", "<br>")}</div>
          <strong>Una pregunta real, un post para publicar.</strong><br>
          <small>{html.escape(g['idea_nota'])}</small>
        </div>
      </div>
    </div>
  </section>

  <section class="claro centro">
    <div class="contenedor">
      <p class="antetitulo">Asistente virtual</p>
      <h2>Lo que te preguntan todos los días.</h2>
      <p class="bajada">Tu asistente contesta estas preguntas con tu información, tus precios y tu tono. Cuando algo necesita a una persona, se pausa y te avisa.</p>
      <div class="tarjetas">
{preguntas}      </div>
    </div>
  </section>

  <section class="banda">
    <div class="contenedor dividido">
      <div>
        <p class="antetitulo">Community Manager</p>
        <h2>Tu contenido sale de tus propios mensajes.</h2>
        <p class="bajada">La plataforma revisa lo que te preguntan por DM, lo que comentan y lo que mejor te funciona, y te propone qué publicar. Tú apruebas cada post antes de que salga.</p>
      </div>
      <div class="ejemplo">
        <p class="mini">Ideas que te puede proponer</p>
        <ul class="marcas">
{ideas}        </ul>
      </div>
    </div>
  </section>

{PLANES}
  <section class="claro centro" id="preguntas">
    <div class="contenedor">
      <p class="antetitulo">Preguntas frecuentes</p>
      <h2>Lo esencial, sin complicaciones.</h2>
      <div class="preguntas">
{faq}      </div>
    </div>
  </section>

  <section class="oscuro centro cierre">
    <div class="contenedor">
      <h2>Velo funcionar con tu negocio<span class="punto">.</span></h2>
      <p class="bajada">Escríbenos por Instagram y te contestamos como lo haría tu propio asistente.</p>
      <div class="acciones">
        <a class="boton" href="{enlace_demo}">Escribir «{demo}»</a>
        <a class="boton contorno" href="/conectar">Conectar mi Instagram</a>
      </div>
      <p class="antetitulo otros-giros-titulo">Otros giros</p>
      <div class="pastillas otros-giros">{otros}</div>
    </div>
  </section>
""" + PIE


def pagina_giros(todos):
    url = f"{SITIO}/para/"
    descripcion = ("Asistente virtual y Community Manager con inteligencia artificial para el Instagram "
                   "de restaurantes, tiendas, salones, consultorios y más.")
    tarjetas = "".join(f"""        <a class="tarjeta" href="/para/{g['slug']}">
          <h3>{html.escape(g['nombre'])}</h3>
          <p>{html.escape(g['descripcion'])}</p>
        </a>
""" for g in todos)
    return cabeza("Asistente virtual para Instagram por giro · Skandar", descripcion, url) + f"""  <section class="oscuro blog-portada">
    <div class="contenedor">
      <p class="antetitulo">Por giro</p>
      <h1>Hecho para tu negocio<span class="punto">.</span></h1>
      <p class="bajada">{descripcion}</p>
    </div>
  </section>

  <section class="claro">
    <div class="contenedor">
      <div class="tarjetas">
{tarjetas}      </div>
    </div>
  </section>
""" + PIE


def leer_pagina(slug):
    texto = (PAGINAS / f"{slug}.md").read_text(encoding="utf-8")
    cabecera, cuerpo = texto.split("\n---\n", 1)
    p = dict(l.split(": ", 1) for l in cabecera.strip().split("\n"))
    p["slug"] = slug
    p["cuerpo"] = cuerpo
    return p


def pagina_suelta(p):
    url = f"{SITIO}/{p['slug']}"
    cuerpo, _ = cuerpo_html(p["cuerpo"])
    esquema = {
        "@context": "https://schema.org",
        "@type": "AboutPage",
        "name": p["titulo"],
        "url": url,
        "description": p["descripcion"],
        "inLanguage": "es-MX",
        "mainEntity": {
            "@type": "Organization",
            "name": "Skandar",
            "url": f"{SITIO}/",
            "logo": f"{SITIO}/apple-touch-icon.png",
            "email": "hola@skandar.online",
            "description": p["descripcion"],
            "areaServed": "MX",
            "sameAs": ["https://www.instagram.com/skandar.online/"],
        },
    }
    return cabeza(f"{p['titulo']} · Skandar", p["descripcion"], url, extra=ld(esquema)) + f"""  <section class="oscuro articulo-cabecera">
    <div class="contenedor">
      <p class="migas"><a href="/">Inicio</a> / {html.escape(p['titulo'])}</p>
      <p class="antetitulo">{html.escape(p['antetitulo'])}</p>
      <h1>{html.escape(p['titulo'])}<span class="punto">.</span></h1>
      <p class="bajada">{html.escape(p['descripcion'])}</p>
    </div>
  </section>

  <section class="claro articulo">
    <div class="contenedor angosto">
{cuerpo}
      <div class="cta-articulo oscuro">
        <h3>Velo funcionar con tu negocio.</h3>
        <p>Escríbenos por Instagram y te contestamos como lo haría tu propio asistente.</p>
        <div class="acciones izquierda-acciones"><a class="boton" href="https://ig.me/m/skandar.online">Escribir a @skandar.online</a><a class="boton contorno" href="/#planes">Ver planes</a></div>
      </div>
    </div>
  </section>
""" + PIE


def llms_txt(todos, giros, paginas):
    """Resumen para modelos de lenguaje (https://llmstxt.org): qué es Skandar y sus páginas."""
    que_es = next(p for p in paginas if p["slug"] == "que-es-skandar")
    filas = [
        "# Skandar",
        "",
        f"> {que_es['descripcion']}",
        "",
        "- Hecho en México, en español. Funciona solo con Instagram, con la API oficial de Meta.",
        "- Asistente virtual: US$29 al mes más IVA, 1,000 respuestas al mes.",
        "- Community Manager: US$79 al mes más IVA, incluye el Asistente virtual, 2,500 respuestas y 12 publicaciones al mes.",
        "- Sin costo de instalación ni plazo forzoso. Contacto: hola@skandar.online, Instagram @skandar.online.",
        "",
        "## Sobre Skandar",
        "",
        f"- [Inicio]({SITIO}/): qué hace la plataforma, planes y preguntas frecuentes.",
    ]
    filas += [f"- [{p['titulo']}]({SITIO}/{p['slug']}): {p['descripcion']}" for p in paginas]
    filas += ["", "## Por giro", ""]
    filas += [f"- [{g['titulo']}]({SITIO}/para/{g['slug']}): {g['descripcion']}" for g in giros]
    filas += ["", "## Blog", ""]
    filas += [f"- [{a['titulo']}]({SITIO}/blog/{a['slug']}): {a['descripcion']}" for a in todos]
    return "\n".join(filas) + "\n"


def sitemap(todos, giros, paginas):
    urls = [("/", "2026-10-26"), ("/privacidad", "2026-10-19"),
            ("/blog/", max(a.get("modificado", a["fecha"]) for a in todos))]
    urls += [(f"/blog/{a['slug']}", a.get("modificado", a["fecha"])) for a in todos]
    urls += [(f"/{p['slug']}", "2026-10-28") for p in paginas]
    if giros:
        urls.append(("/para/", max(g["fecha"] for g in giros)))
        urls += [(f"/para/{g['slug']}", g["fecha"]) for g in giros]
    filas = "".join(f"  <url><loc>{SITIO}{u}</loc><lastmod>{f}</lastmod></url>\n" for u, f in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + filas + "</urlset>\n")


def main():
    nuevos = sorted(p.stem for p in FUENTES.glob("*.md") if p.stem not in ORDEN)
    todos = [leer(s) for s in ORDEN + nuevos]
    salida = WEB / "blog"
    salida.mkdir(exist_ok=True)
    (salida / "index.html").write_text(pagina_indice(todos), encoding="utf-8")
    for a in todos:
        (salida / f"{a['slug']}.html").write_text(pagina_articulo(a, todos), encoding="utf-8")
    giros = [leer_giro(p.stem) for p in sorted(GIROS.glob("*.md"))]
    para = WEB / "para"
    para.mkdir(exist_ok=True)
    (para / "index.html").write_text(pagina_giros(giros), encoding="utf-8")
    for g in giros:
        (para / f"{g['slug']}.html").write_text(pagina_giro(g, giros), encoding="utf-8")
    paginas = [leer_pagina(p.stem) for p in sorted(PAGINAS.glob("*.md"))]
    for p in paginas:
        (WEB / f"{p['slug']}.html").write_text(pagina_suelta(p), encoding="utf-8")
    (WEB / "sitemap.xml").write_text(sitemap(todos, giros, paginas), encoding="utf-8")
    (WEB / "llms.txt").write_text(llms_txt(todos, giros, paginas), encoding="utf-8")
    print(f"{len(todos)} artículos en {salida} y {len(giros)} giros en {para}")


if __name__ == "__main__":
    main()
