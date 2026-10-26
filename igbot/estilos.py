"""Las opciones de Tu marca: estilos visuales, paletas, cuánto color, letras, logo y recursos visuales.

Cada opción tiene lo que ve el cliente en el panel (nombre, descripción, etiquetas) y, si cambia la
imagen, cómo se le pide al generador (en inglés). Lo usan Tu marca (igbot/marca.py) y los prompts de
imagen (contenido.estilo_foto y contenido.contexto_marca).

Las tarjetas de Estilo visual y Recursos visuales llevan una imagen en
igbot/static/tarjetas/marca/<clave>.webp (o .png/.jpg); mientras no exista, se pinta su color de respaldo.
"""

ESTILOS = {
    # clave: (nombre, descripción, cómo se le pide al generador)
    "brutalista": ("Brutalista",
                   "Colores planos, titulares contundentes, contrastes fuertes y formas geométricas. Ideal para marcas"
                   " con personalidad que quieren romper con lo convencional.",
                   "Brutalist graphic design: flat solid colors, massive bold headlines, harsh contrast, raw geometric"
                   " shapes and thick lines, no gradients or soft shadows, deliberately direct and unpolished."),
    "minimalista": ("Minimalista",
                    "Mucho espacio libre, pocos colores, tipografía limpia y composiciones cuidadas. Cada elemento"
                    " tiene una razón para estar ahí.",
                    "Minimalist premium design: generous negative space, very few elements, a restrained two or three"
                    " color palette, clean typography, soft diffused light, precise balanced composition."),
    "neofuturista": ("Neofuturista",
                     "Fondos oscuros, detalles luminosos, geometría precisa y pequeños acentos de neón. Ideal para"
                     " marcas tecnológicas, de innovación e inteligencia artificial.",
                     "Neo-futuristic look: dark background, luminous details and thin glowing lines, precise geometry,"
                     " small neon accents, sleek high-tech surfaces, sophisticated and innovative mood."),
    "cristalino": ("Cristalino",
                   "Paneles translúcidos, superficies de cristal esmerilado y luces suaves que crean profundidad. Los"
                   " colores del fondo se ven a través de los elementos.",
                   "Glassmorphism and liquid glass: translucent frosted-glass panels, soft reflections and refractions,"
                   " background colors blurred through the elements, soft light, layered depth, light and refined."),
    "maximalista": ("Maximalista",
                    "Titulares grandes, colores intensos, composiciones atrevidas, capas y elementos gráficos que"
                    " llaman la atención sin perder el mensaje principal.",
                    "Maximalist design: big headlines, saturated bold colors, daring layered composition with many"
                    " graphic elements, patterns and stickers, energetic, while keeping one clear focal message."),
    "claymorfismo": ("Claymorfismo",
                     "Formas redondeadas, superficies mates, sombras suaves y elementos que parecen moldeados en"
                     " plastilina. Una estética amable y táctil.",
                     "Claymorphism 3D style: rounded puffy shapes, matte clay-like surfaces, soft inner and outer"
                     " shadows, pastel-friendly colors, objects that look modeled in plasticine, friendly and tactile."),
    "editorial": ("Editorial",
                  "Fotografías protagonistas, tipografía cuidada, titulares elegantes y composiciones equilibradas,"
                  " como una revista de alta gama.",
                  "High-end editorial magazine look: a strong hero photograph, refined elegant typography, balanced"
                  " layout with clear hierarchy, natural sophisticated light, timeless and authoritative."),
    "bento": ("Bento Grid",
              "Bloques de distintos tamaños organizan fotografías, titulares, cifras y mensajes en una composición"
              " modular, fácil de recorrer.",
              "Bento grid layout: a modular composition of rounded blocks of different sizes holding a photo, a"
              " headline, a key figure and short messages, tidy spacing, organized and easy to scan."),
    "espacial": ("Espacial",
                 "Tarjetas y objetos separados del fondo, sombras amplias, capas y sensación tridimensional. Los"
                 " elementos parecen flotar dentro de una escena digital.",
                 "Spatial 3D design: cards and objects floating above the background, wide soft shadows, several depth"
                 " layers, a sense of three-dimensional space, immersive digital scene, sophisticated."),
}
ESTILO_INICIAL = "minimalista"
MAX_ESTILOS = 3
ROLES = ["Estilo principal", "Estilo secundario", "Estilo de apoyo"]  # lo que aporta cada lugar
# Estilos de versiones anteriores de Tu marca (los cuatro del primer formulario y los 15 del asistente
# por pasos) y el estilo nuevo que más se les parece.
VIEJOS_ESTILOS = {
    "limpio": "minimalista", "premium": "editorial", "colorido": "maximalista",
    "natural": "editorial", "minimal": "minimalista", "lujo": "editorial", "calido": "editorial",
    "llamativo": "maximalista", "tecnologico": "neofuturista", "organico": "minimalista", "retro": "maximalista",
    "artesanal": "editorial", "urbano": "brutalista", "geometrico": "bento", "ilustrado": "claymorfismo",
    "fresco": "minimalista", "corporativo": "bento",
}
# Lo que solo ve el panel: la frase corta, la sensación y el color de respaldo mientras no hay imagen.
TARJETA_ESTILO = {
    "brutalista": ("Diseños atrevidos que no pasan desapercibidos.", ("Rebelde", "Directo", "Impactante"), "#8a3b2a"),
    "minimalista": ("Menos elementos, más protagonismo.", ("Elegante", "Ordenado", "Premium"), "#6e6d68"),
    "neofuturista": ("Una estética tecnológica y vanguardista.", ("Tecnológico", "Sofisticado", "Innovador"),
                     "#1f2f4f"),
    "cristalino": ("Transparencias, reflejos y un acabado moderno.", ("Moderno", "Ligero", "Refinado"), "#5b7c99"),
    "maximalista": ("Más color, más energía, más personalidad.", ("Expresivo", "Creativo", "Enérgico"), "#b4532a"),
    "claymorfismo": ("Diseños suaves que parecen tener volumen.", ("Cercano", "Divertido", "Acogedor"), "#a07a9a"),
    "editorial": ("Como las páginas de una revista de alta gama.", ("Profesional", "Elegante", "Atemporal"),
                  "#5a4e44"),
    "bento": ("Cada elemento tiene su espacio.", ("Organizado", "Moderno", "Informativo"), "#3d5a80"),
    "espacial": ("Elementos que flotan y crean profundidad.", ("Inmersivo", "Futurista", "Sofisticado"), "#433d63"),
}
# «No estoy seguro, recomiéndame uno»: los tres que mejor van con cada giro de Perfil de empresa.
RECOMENDADOS = {
    "Restaurante o cafetería": ["editorial", "maximalista", "minimalista"],
    "Alimentos y bebidas": ["maximalista", "editorial", "claymorfismo"],
    "Tienda de ropa y accesorios": ["editorial", "minimalista", "brutalista"],
    "Joyería": ["minimalista", "editorial", "cristalino"],
    "Belleza y estética": ["editorial", "cristalino", "minimalista"],
    "Salud y bienestar": ["minimalista", "cristalino", "editorial"],
    "Consultorio médico o dental": ["minimalista", "bento", "cristalino"],
    "Gimnasio o deporte": ["brutalista", "maximalista", "neofuturista"],
    "Educación y cursos": ["bento", "claymorfismo", "maximalista"],
    "Inmobiliaria": ["editorial", "minimalista", "bento"],
    "Diseño de interiores": ["editorial", "minimalista", "espacial"],
    "Arquitectura y construcción": ["minimalista", "editorial", "brutalista"],
    "Hogar y decoración": ["editorial", "minimalista", "claymorfismo"],
    "Servicios profesionales": ["minimalista", "bento", "editorial"],
    "Tecnología y software": ["neofuturista", "cristalino", "bento"],
    "Agencia o marketing": ["maximalista", "brutalista", "bento"],
    "Eventos y bodas": ["editorial", "minimalista", "cristalino"],
    "Fotografía y video": ["editorial", "minimalista", "brutalista"],
    "Hotelería y turismo": ["editorial", "espacial", "minimalista"],
    "Mascotas": ["claymorfismo", "maximalista", "editorial"],
    "Automotriz": ["neofuturista", "brutalista", "espacial"],
    "Tienda en línea": ["bento", "maximalista", "minimalista"],
}
RECOMENDADOS_INICIAL = ["minimalista", "editorial", "bento"]  # sin giro (o uno escrito a mano)

# Los cuatro colores: (campo, nombre, para qué se usa). El secundario solo orienta al generador de imágenes;
# las plantillas usan principal, acento y fondo.
COLORES = [
    ("principal", "Principal", "Títulos y lo que más se repite."),
    ("secundario", "Secundario", "Bloques y fondos de apoyo."),
    ("acento", "Acento", "Precios, detalles y llamadas a la acción. Úsalo poco."),
    ("fondo", "Fondo", "La base de tus diseños, clara u oscura."),
]
PALETAS = {
    # clave: (nombre, descripción, (principal, secundario, acento, fondo))
    "carbon": ("Carbón y terracota", "Moderna, cálida y versátil.", ("#29292D", "#C2410C", "#D8B58A", "#F5F0E8")),
    "bosque": ("Bosque y arena", "Natural, tranquila y honesta.", ("#2F4A3A", "#9DB59A", "#B07A55", "#EFE9DC")),
    "azul": ("Azul confianza", "Profesional, clara y confiable.", ("#16324F", "#3B82C4", "#9ED8CF", "#F5F8FB")),
    "dorado": ("Negro y dorado", "Elegante, sobria y atemporal.", ("#151515", "#6E665C", "#C9A961", "#F4F0E6")),
    "energia": ("Energía creativa", "Expresiva, alegre y optimista.", ("#4C2A85", "#F07A3D", "#F2C94C", "#FFF7EC")),
    "tecno": ("Tecnológica", "Digital, precisa y actual.", ("#0E1116", "#2563EB", "#22D3EE", "#F3F5F8")),
    "rosa": ("Rosa contemporáneo", "Cercana, elegante y distinta.", ("#5A2A3A", "#C27588", "#EBC3CC", "#FCF5F4")),
    "mono": ("Monocromo", "Limpia, sobria y fácil de combinar.", ("#1C1C1E", "#5E5E62", "#A1A1A6", "#F5F5F7")),
    "tierra": ("Tierra y café", "Artesanal, cálida y cercana.", ("#4A3426", "#B5653E", "#D9BC9A", "#F7F1EA")),
    "verde": ("Verde bienestar", "Salud, equilibrio y calma.", ("#1F3D33", "#6E9E7D", "#CFE3D6", "#F6F9F6")),
    "fresca": ("Fresca y vibrante", "Energética, luminosa y actual.", ("#1F3B70", "#5FA8E0", "#F2C14E", "#FBFAF6")),
    "violeta": ("Violeta creativo", "Original, expresiva y moderna.", ("#2E1A47", "#7C5CD6", "#E8C6E6", "#FAF7FD")),
}
PALETA_INICIAL = "carbon"
USO_COLOR = {
    # clave: (nombre, descripción, cómo se le pide al generador)
    "sutil": ("Sutil", "Fondos neutros y tu color solo en detalles. Elegante y fácil de leer.",
              "Keep the scene mostly neutral; brand colors only in small details."),
    "equilibrado": ("Equilibrado", "Tu color en títulos y bloques, sobre fondos claros. Reconocible sin cansar.",
                    "Use the brand colors in a balanced way over light backgrounds: noticeable, never overwhelming."),
    "protagonista": ("Protagonista", "Fondos con tu color y contraste fuerte. Destaca en el feed.",
                     "Make the brand colors the protagonists: colored backgrounds and bold contrast."),
    "oscuro": ("Oscuro", "Fondos oscuros con tu color como brillo. Sofisticado y nocturno.",
               "Dark, low-key scene with the brand colors as highlights and glows."),
}
USO_INICIAL = "equilibrado"

LETRAS = {
    # fuente (de imagenes.FUENTES): (grosor de los títulos, nombre, descripción, antetítulo y título de muestra)
    "Inter": (600, "Moderna y limpia", "Clara, actual y fácil de leer en cualquier tamaño. Funciona para casi todo.",
              "Una idea clara", "Tu negocio, un paso adelante."),
    "Playfair Display": (600, "Elegante y editorial",
                         "Contraste fino y aire de revista. Ideal para moda, belleza y gastronomía.",
                         "Colección especial", "Pequeños detalles, grandes experiencias."),
    "Nunito": (700, "Amigable y cercana", "Trazos redondos que se sienten conversacionales sin parecer infantiles.",
               "Qué gusto verte", "Tenemos algo nuevo para ti."),
    "Oswald": (700, "Fuerte e impactante", "Condensada y con peso. Para promociones que se leen de un vistazo.",
               "Atención", "OFERTA ESPECIAL"),
    "Space Grotesk": (600, "Tecnológica y precisa",
                      "Detalles geométricos y sensación digital. Para software, datos y servicios técnicos.",
                      "Sistema / 01", "Datos que mueven ideas."),
    "Lora": (600, "Clásica y confiable", "Serif legible de proporciones tradicionales. Transmite trayectoria y seriedad.",
             "Desde siempre", "Confianza en cada detalle."),
    "Poppins": (700, "Redondeada y optimista",
                "Geométrica y simpática, con energía positiva. Para comunidades y marcas jóvenes.",
                "Buenas noticias", "Ideas frescas para compartir."),
    "Raleway": (400, "Ligera y minimalista", "Trazo fino y mucho aire. Calma y orden para marcas que hablan bajito.",
                "Menos, mejor", "Espacio para lo importante."),
    "Cormorant Garamond": (600, "Lujo discreto", "Serif refinada de trazos delicados. Calidad que se percibe sin exagerar.",
                           "Edición limitada", "Calidad que se percibe."),
    "Montserrat": (700, "Geométrica y segura", "Firme, urbana y muy legible. Para marcas directas y con presencia.",
                   "Nuevo", "Hecho para destacar."),
    "Work Sans": (600, "Sobria y funcional", "Neutra y trabajadora: el mensaje es el protagonista.",
                  "Paso a paso", "Claro, directo y útil."),
    "DM Sans": (600, "Contemporánea y versátil", "Moderna con un toque suave. Se adapta a tonos formales e informales.",
                "Comunidad", "Ideas que conectan."),
}

LOGO_PRESENCIA = {
    # clave: (nombre, descripción)
    "discreta": ("Firma discreta", "Pequeño en una esquina. El contenido es el protagonista."),
    "visible": ("Firma visible", "Se reconoce a la primera sin competir con la imagen."),
    "clave": ("Solo cuando suma", "En promociones, lanzamientos y anuncios; no en todo."),
    "siempre": ("Siempre presente", "En todas tus publicaciones, para reforzar el recuerdo."),
}
LOGO_INICIAL = "discreta"

RECURSOS = {
    # clave: (nombre, descripción, etiquetas, color de respaldo, cómo se le pide al generador)
    "personas": ("Personas reales", "Clientes, equipo y manos trabajando, con gestos naturales y sin poses de catálogo.",
                 ("Retratos", "Emoción"), "#8c7258", "real people (customers, team, hands at work) with natural gestures,"
                 " never stock-photo poses"),
    "productos": ("Tus productos", "Tu producto como protagonista: primeros planos, materiales, empaques y acabados.",
                  ("Producto", "Detalle"), "#7a7266", "the products as protagonists: close-ups, materials, packaging"
                  " and finishes"),
    "espacio": ("Tu espacio", "Tu local, oficina o estudio: la luz, la decoración y el ambiente de tu lugar.",
                ("Interiores", "Ambiente"), "#5f6b66", "the business's own space: its premises, light and atmosphere"),
    "proceso": ("Detrás de cámaras", "Preparación, procesos y herramientas. Muestra el oficio y da transparencia.",
                ("Proceso", "Oficio"), "#7d6c4f", "behind the scenes: preparation, processes and tools"),
    "uso": ("En la vida real", "Tu producto o servicio en el día a día de tus clientes.",
            ("Estilo de vida", "Contexto"), "#7a6680", "the product or service in customers' everyday life"),
    "antes": ("Antes y después", "Cambios y resultados comparados. Solo resultados reales, sin promesas exageradas.",
              ("Resultado", "Prueba"), "#5b7566", "before-and-after comparisons, realistic and never exaggerated"),
    "opiniones": ("Opiniones de clientes", "Reseñas y frases reales de tus clientes con un diseño claro. Nunca inventadas.",
                  ("Confianza", "Reseñas"), "#8a7656", "genuinely happy customers (no invented reviews, stars or text)"),
    "datos": ("Datos e infografías", "Cifras, pasos y comparativas para explicar algo de un vistazo.",
              ("Educativo", "Claridad"), "#4f6a85", "clean visual explanations: simple diagrams and step-by-step visuals"),
    "ideas": ("Ideas en imagen", "Metáforas y conceptos visuales para lo que no se puede fotografiar.",
              ("Concepto", "Metáfora"), "#4b5a80", "conceptual images and visual metaphors"),
    "comida": ("Comida y bebida", "Platillos, ingredientes y preparación con aspecto apetitoso.",
               ("Gastronomía", "Sabor"), "#9a6447", "appetizing food and drinks, ingredients and preparation"),
    "texturas": ("Texturas y fondos", "Papel, madera, piedra o degradados que dan profundidad sin distraer.",
                 ("Fondos", "Materiales"), "#7c7464", "textures and backgrounds such as paper, wood, stone or gradients"),
    "personaje": ("Personaje de marca", "Una mascota o personaje recurrente que da continuidad a tu comunicación.",
                  ("Personaje", "Recurrente"), "#9a6a68", "the brand's recurring character or mascot"),
}
