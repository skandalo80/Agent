"""Las opciones de Tu marca: estilos visuales, paletas, cuánto color, letras, logo y recursos visuales.

Cada opción tiene lo que ve el cliente en el panel (nombre, descripción, etiquetas) y, si cambia la
imagen, cómo se le pide al generador (en inglés). Lo usan Tu marca (igbot/marca.py) y los prompts de
imagen (contenido.estilo_foto y contenido.contexto_marca).

Las tarjetas de Estilo visual y Recursos visuales llevan una imagen en
igbot/static/tarjetas/marca/<clave>.webp (o .png/.jpg); mientras no exista, se pinta su color de respaldo.
"""

ESTILOS = {
    # clave: (nombre, descripción, cómo se le pide al generador)
    "natural": ("Natural y auténtico",
                "Fotos espontáneas con luz de ventana, texturas reales y momentos cotidianos. Honesto y cercano.",
                "Candid, authentic photography: soft window light, real textures, everyday moments, earthy tones,"
                " nothing posed."),
    "minimal": ("Minimalista y premium",
                "Mucho espacio libre, pocos elementos y acabados cuidados. Sobrio, limpio y de alta calidad.",
                "Minimalist premium look: generous negative space, very few elements, plain clean background,"
                " soft diffused light, precise composition."),
    "lujo": ("Lujo y sofisticación",
             "Fondos oscuros, luz lateral y detalles dorados o metálicos. Exclusivo y refinado.",
             "Luxurious and refined: dark background, dramatic side lighting, subtle gold or metallic reflections,"
             " minimalist composition."),
    "calido": ("Cálido y cercano",
               "Luz de tarde, tonos tierra, madera y lino. Personas y gestos que hacen sentir bienvenido.",
               "Warm and welcoming: late-afternoon natural light, wood and linen textures, earthy colors, friendly"
               " human gestures."),
    "llamativo": ("Creativo y llamativo",
                  "Colores intensos, titulares grandes y composiciones con movimiento. Hecho para frenar el scroll.",
                  "Bold and eye-catching: saturated solid colors, strong contrast, hard shadows, dynamic composition"
                  " with movement."),
    "tecnologico": ("Tecnológico y futurista",
                    "Degradados profundos, brillos controlados y geometría precisa. Transmite innovación.",
                    "Technological and futuristic: deep blue gradients, controlled glows, precise geometry, clean"
                    " digital feel."),
    "editorial": ("Suave y editorial",
                  "Tonos pastel, formas orgánicas y composición de revista. Amable sin perder estructura.",
                  "Soft editorial magazine look: pastel tones, organic shapes, diffused light, structured layout."),
    "organico": ("Orgánico y botánico",
                 "Hojas, papel, cerámica y tonos verdes y beige. Sereno, natural y consciente.",
                 "Organic and botanical: leaves, paper, handmade ceramics, green and beige tones, calm natural light."),
    "retro": ("Retro y nostálgico",
              "Paletas vintage, grano de película y formas de otras décadas. Con carácter y memoria.",
              "Retro and nostalgic: vintage color palette, subtle film grain, props and shapes from past decades."),
    "artesanal": ("Artesanal y táctil",
                  "Papel, tela o barro e imperfecciones cuidadas. Se nota que está hecho a mano.",
                  "Handmade and tactile: close-ups of paper, fabric or clay textures, careful imperfections, soft"
                  " side light."),
    "urbano": ("Urbano y contemporáneo",
               "Calle, arquitectura, contraste fuerte y encuadres actuales. Directo y con actitud.",
               "Urban and contemporary: street and architecture, strong contrast and shadows, modern geometric"
               " framing."),
    "geometrico": ("Geométrico y ordenado",
                   "Retículas, bloques de color y líneas limpias. Transmite orden, claridad y método.",
                   "Geometric and orderly: grids, color blocks and clean lines, balanced structured composition."),
    "ilustrado": ("Ilustrado y narrativo",
                  "Ilustraciones, personajes y metáforas visuales para explicar ideas de forma memorable.",
                  "Illustrated and narrative: friendly flat illustration with characters and visual metaphors,"
                  " soft colors."),
    "fresco": ("Fresco y luminoso",
               "Fondos claros, luz de mediodía y colores vivos pero limpios. Optimista y con energía.",
               "Fresh and bright: high-key midday light, light backgrounds, vivid but clean colors, optimistic mood."),
    "corporativo": ("Profesional y corporativo",
                    "Espacios de trabajo, personas en acción y una paleta contenida. Serio, confiable y experto.",
                    "Professional and corporate: modern bright workspaces, people at work, restrained gray and blue"
                    " palette, trustworthy."),
}
ESTILO_INICIAL = "minimal"
MAX_ESTILOS = 3
# Los cuatro estilos de antes (una marca guardada con el formulario viejo tiene uno de estos).
VIEJOS_ESTILOS = {"limpio": "minimal", "calido": "calido", "premium": "lujo", "colorido": "llamativo"}
# Lo que solo ve el panel: etiquetas y el color de respaldo mientras no hay imagen.
TARJETA_ESTILO = {
    "natural": (("Real", "Espontáneo", "Cercano"), "#8a7a60"), "minimal": (("Limpio", "Sobrio", "Preciso"), "#6e6d68"),
    "lujo": (("Exclusivo", "Oscuro", "Refinado"), "#3a3128"), "calido": (("Acogedor", "Humano", "Amable"), "#9c6b45"),
    "llamativo": (("Impactante", "Dinámico", "Audaz"), "#b4532a"),
    "tecnologico": (("Digital", "Innovador", "Preciso"), "#24486b"),
    "editorial": (("Pastel", "Editorial", "Actual"), "#7d6a86"), "organico": (("Natural", "Sereno", "Consciente"), "#5d6e55"),
    "retro": (("Vintage", "Expresivo", "Memorable"), "#9a6a2a"), "artesanal": (("Textura", "Oficio", "Detalle"), "#857559"),
    "urbano": (("Calle", "Contraste", "Actual"), "#4a4a4f"), "geometrico": (("Orden", "Estructura", "Claridad"), "#3d5a80"),
    "ilustrado": (("Ilustración", "Didáctico", "Creativo"), "#a0705a"),
    "fresco": (("Luminoso", "Optimista", "Fresco"), "#3f8aa6"), "corporativo": (("Confiable", "Serio", "Experto"), "#55606c"),
}

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
