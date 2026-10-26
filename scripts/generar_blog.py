"""Genera el blog estático de skandar.online a partir de blog/*.md.

Cada artículo es un .md con cabecera "clave: valor" (titulo, descripcion,
categoria, portada, fecha, lectura, cta), una línea "---" y el cuerpo en un
markdown sencillo: párrafos, "## " subtítulos, "- " listas, "> " recuadro,
**negritas** y [enlaces](/ruta).

Escribe web/blog/index.html, web/blog/<slug>.html y web/sitemap.xml.
Uso: python scripts/generar_blog.py
"""

import html
import json
import re
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = RAIZ / "blog"
WEB = RAIZ / "web"
SITIO = "https://skandar.online"
CSS_VERSION = "2026-10-26d"

# Orden en el índice: el primero es el destacado.
ORDEN = [
    "contestar-mensajes-instagram-24-horas",
    "que-publicar-en-instagram",
    "asistente-virtual-pasar-a-persona",
    "calendario-de-contenido-instagram",
    "comenta-y-recibe-por-dm",
    "community-manager-con-inteligencia-artificial",
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
<meta name="theme-color" content="#36203E">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{tipo}">
<meta property="og:locale" content="es_MX">
<meta property="og:site_name" content="Agentes IA by Skandar">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:image" content="{SITIO}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
{extra}<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap">
<link rel="stylesheet" href="/estilos.css?v={CSS_VERSION}">
</head>
<body>
<header>
  <div class="contenedor">
    <a class="marca" href="/"><span class="sello">S</span><span>Agentes IA <span class="nombre-largo">by Skandar</span></span></a>
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
    <a class="marca" href="/"><span class="sello">S</span>Agentes IA by Skandar</a>
    <span>© 2026 Agentes IA by Skandar · México</span>
    <div class="pie-enlaces">
      <a href="/blog/">Blog</a>
      <a href="/privacidad">Aviso de privacidad</a>
      <a href="mailto:hola@skandar.online">Contacto</a>
      <a href="https://console.skandar.online/panel">Entrar</a>
    </div>
  </div>
</footer>
<script src="/sitio.js"></script>
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
                "author": {"@type": "Organization", "name": "Agentes IA by Skandar", "url": f"{SITIO}/"},
                "publisher": {"@type": "Organization", "name": "Agentes IA by Skandar",
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
    return cabeza(f"{a['titulo']} · Agentes IA by Skandar", a["descripcion"], url, "article", extra) + f"""  <section class="oscuro articulo-cabecera">
    <div class="contenedor">
      <p class="migas"><a href="/">Inicio</a> / <a href="/blog/">Blog</a> / {html.escape(a['categoria'])}</p>
      <p class="antetitulo">{html.escape(a['categoria'])}</p>
      <h1>{html.escape(a['titulo'])}</h1>
      <p class="bajada">{html.escape(a['descripcion'])}</p>
      <p class="datos">Por el equipo de Agentes IA by Skandar · {fecha_larga(a['fecha'])} · {a['lectura']} min de lectura</p>
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
        "name": "Blog de Agentes IA by Skandar",
        "url": url,
        "description": descripcion,
        "inLanguage": "es-MX",
        "publisher": {"@type": "Organization", "name": "Agentes IA by Skandar", "url": f"{SITIO}/"},
        "blogPost": [{"@type": "BlogPosting", "headline": a["titulo"], "url": f"{SITIO}/blog/{a['slug']}",
                      "datePublished": a["fecha"]} for a in todos],
    }
    categorias = list(dict.fromkeys(a["categoria"] for a in todos))
    filtros = '<button type="button" class="pastilla activa" data-filtro="">Todo</button>' + "".join(
        f'<button type="button" class="pastilla" data-filtro="{html.escape(c)}">{html.escape(c)}</button>'
        for c in categorias)
    tarjetas = "".join(tarjeta(a, i == 0) for i, a in enumerate(todos))
    return cabeza("Blog: Instagram para tu negocio · Agentes IA by Skandar", descripcion, url,
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


def sitemap(todos):
    urls = [("/", "2026-10-26"), ("/privacidad", "2026-10-19"),
            ("/blog/", max(a.get("modificado", a["fecha"]) for a in todos))]
    urls += [(f"/blog/{a['slug']}", a.get("modificado", a["fecha"])) for a in todos]
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
    (WEB / "sitemap.xml").write_text(sitemap(todos), encoding="utf-8")
    print(f"{len(todos)} artículos en {salida}")


if __name__ == "__main__":
    main()
