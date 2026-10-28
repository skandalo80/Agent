"""Las opciones de Tu marca: estilos visuales, paletas, cuánto color, letras y logo.

Cada opción tiene lo que ve el cliente en el panel (nombre, descripción, etiquetas) y, si cambia la
imagen, cómo se le pide al generador (en inglés). Lo usan Tu marca (igbot/marca.py) y los prompts de
imagen (contenido.estilo_foto y contenido.contexto_marca).

Las tarjetas llevan una imagen en igbot/static/tarjetas/marca/: <estilo>.webp, color-<uso>.webp y
logo-<presencia>.webp (o .png/.jpg); mientras no exista, se pinta su color o su muestra de respaldo.
"""

ESTILOS = {
    # clave: (nombre, descripción, cómo se le pide al generador)
    "lujo": ("Lujo sobrio",
             "Materiales refinados, fotografías cuidadas y composiciones que transmiten exclusividad sin recurrir a"
             " adornos excesivos.",
             "Quiet luxury: refined materials (stone, brushed metal, fine fabrics, glass), carefully lit photography,"
             " restrained muted palette, generous space, precise elegant composition, exclusive but never flashy,"
             " no ornaments or gold glitter."),
    "minimalista": ("Minimalista",
                    "Mucho espacio libre, pocos colores y composiciones limpias. Cada elemento tiene una razón para"
                    " estar ahí.",
                    "Minimalist premium design: generous negative space, very few elements, a restrained two or three"
                    " color palette, clean typography, soft diffused light, precise balanced composition."),
    "editorial": ("Editorial",
                  "Fotografías protagonistas, tipografía cuidada y composiciones equilibradas que combinan imágenes y"
                  " mensajes con intención.",
                  "High-end editorial magazine look: a strong hero photograph, refined elegant typography, balanced"
                  " layout with clear hierarchy, natural sophisticated light, timeless and authoritative."),
    "artesanal": ("Artesanal",
                  "Texturas naturales, cerámica, papel y detalles imperfectos que transmiten trabajo manual y atención"
                  " al producto.",
                  "Artisanal handmade look: natural textures (ceramic, kraft paper, linen, wood), visible handcraft and"
                  " small imperfections, warm natural light, close attention to the product, authentic and human."),
    "organico": ("Orgánico contemporáneo",
                 "Curvas suaves, materiales orgánicos y tonos tierra se combinan en composiciones fluidas, serenas y"
                 " actuales.",
                 "Contemporary organic design: soft curves and flowing shapes, organic materials (clay, travertine,"
                 " plants, wood), earthy tones, calm fluid composition, soft daylight, serene and current."),
    "retro": ("Retro moderno",
              "Tipografías con carácter, paletas nostálgicas y formas vintage reinterpretadas con composiciones"
              " frescas.",
              "Modern retro: characterful display typography, nostalgic palettes (mustard, teal, burnt orange, cream),"
              " vintage shapes and subtle grain reinterpreted with a fresh clean layout, original and expressive."),
    "pop": ("Pop gráfico",
            "Colores intensos, formas geométricas y titulares llamativos crean publicaciones enérgicas, atrevidas y"
            " memorables.",
            "Graphic pop design: saturated bold colors, playful geometric shapes, big punchy headlines, flat graphic"
            " elements layered with the photo, energetic and daring, still one clear focal message."),
    "documental": ("Fotográfico documental",
                   "Escenas espontáneas, luz disponible y encuadres cotidianos que muestran el producto, las personas"
                   " y el detrás de cámaras.",
                   "Documentary photography: candid unposed moments, available natural light, everyday framing, real"
                   " people, products and behind-the-scenes, honest and close, no studio look and no graphic"
                   " overlays."),
    "brutalista": ("Brutalista",
                   "Colores planos, titulares contundentes, contrastes fuertes y formas geométricas que rompen con lo"
                   " convencional.",
                   "Brutalist graphic design: flat solid colors, massive bold headlines, harsh contrast, raw geometric"
                   " shapes and thick lines, no gradients or soft shadows, deliberately direct and unpolished."),
    "claymorfismo": ("Claymorfismo",
                     "Formas redondeadas, superficies mates y objetos con volumen que aportan un aspecto lúdico y"
                     " acogedor.",
                     "Claymorphism 3D style: rounded puffy shapes, matte clay-like surfaces, soft inner and outer"
                     " shadows, pastel-friendly colors, objects that look modeled in plasticine, friendly and tactile."),
    "cristalino": ("Cristalino",
                   "Capas translúcidas, superficies de cristal esmerilado y reflejos sutiles que interactúan con los"
                   " colores del fondo.",
                   "Glassmorphism and liquid glass: translucent frosted-glass panels, soft reflections and refractions,"
                   " background colors blurred through the elements, soft light, layered depth, light and refined."),
    "neofuturista": ("Neofuturista",
                     "Geometría precisa, líneas técnicas y composiciones innovadoras que combinan formas modernas con"
                     " detalles gráficos avanzados.",
                     "Neo-futuristic look: precise geometry, thin technical lines and luminous details, innovative"
                     " composition, sleek high-tech surfaces, small advanced graphic details, sophisticated and"
                     " innovative mood."),
}
ESTILO_INICIAL = "minimalista"
MAX_ESTILOS = 3
ROLES = ["Estilo principal", "Estilo secundario", "Estilo de apoyo"]  # lo que aporta cada lugar
# Estilos de versiones anteriores de Tu marca (los cuatro del primer formulario, los 15 del asistente por
# pasos y los 9 de antes del catálogo de 12) y el estilo de hoy que más se les parece.
VIEJOS_ESTILOS = {
    "limpio": "minimalista", "premium": "lujo", "colorido": "pop",
    "natural": "organico", "minimal": "minimalista", "calido": "artesanal",
    "llamativo": "pop", "tecnologico": "neofuturista", "urbano": "brutalista", "geometrico": "pop",
    "ilustrado": "claymorfismo", "fresco": "minimalista", "corporativo": "minimalista",
    "maximalista": "pop", "bento": "minimalista", "espacial": "neofuturista",
}
# Lo que solo ve el panel: la frase corta, la sensación y el color de respaldo mientras no hay imagen.
TARJETA_ESTILO = {
    "lujo": ("Elegancia que no necesita presumir.", ("Exclusivo", "Refinado", "Distinguido"), "#3b3631"),
    "minimalista": ("Menos elementos, más protagonismo.", ("Elegante", "Ordenado", "Atemporal"), "#6e6d68"),
    "editorial": ("Cada publicación parece una página de revista.", ("Profesional", "Elegante", "Atemporal"),
                  "#5a4e44"),
    "artesanal": ("La belleza de lo auténtico.", ("Cálido", "Auténtico", "Humano"), "#7a5a3f"),
    "organico": ("Formas naturales con una mirada moderna.", ("Natural", "Sereno", "Actual"), "#6b6a52"),
    "retro": ("Un guiño al pasado con personalidad actual.", ("Nostálgico", "Original", "Expresivo"), "#9a6a2f"),
    "pop": ("Ideas que saltan a la vista.", ("Enérgico", "Atrevido", "Memorable"), "#b4532a"),
    "documental": ("Momentos reales, sin posar para la cámara.", ("Cercano", "Honesto", "Espontáneo"), "#4f4a42"),
    "brutalista": ("Diseños atrevidos que no pasan desapercibidos.", ("Rebelde", "Directo", "Impactante"), "#8a3b2a"),
    "claymorfismo": ("Diseños suaves que parecen moldeados a mano.", ("Cercano", "Divertido", "Acogedor"), "#a07a9a"),
    "cristalino": ("Transparencias que crean profundidad.", ("Moderno", "Ligero", "Refinado"), "#5b7c99"),
    "neofuturista": ("Una mirada tecnológica al diseño del futuro.", ("Tecnológico", "Sofisticado", "Innovador"),
                     "#1f2f4f"),
}
# «No estoy seguro, recomiéndame uno»: los tres que mejor van con cada giro de Perfil de empresa.
RECOMENDADOS = {
    "Restaurante o cafetería": ["documental", "artesanal", "editorial"],
    "Alimentos y bebidas": ["artesanal", "pop", "documental"],
    "Tienda de ropa y accesorios": ["editorial", "minimalista", "retro"],
    "Joyería": ["lujo", "minimalista", "editorial"],
    "Belleza y estética": ["editorial", "organico", "lujo"],
    "Salud y bienestar": ["organico", "minimalista", "documental"],
    "Consultorio médico o dental": ["minimalista", "cristalino", "documental"],
    "Gimnasio o deporte": ["brutalista", "pop", "documental"],
    "Educación y cursos": ["claymorfismo", "pop", "minimalista"],
    "Inmobiliaria": ["lujo", "editorial", "minimalista"],
    "Diseño de interiores": ["organico", "editorial", "lujo"],
    "Arquitectura y construcción": ["minimalista", "brutalista", "editorial"],
    "Hogar y decoración": ["organico", "artesanal", "editorial"],
    "Servicios profesionales": ["minimalista", "editorial", "documental"],
    "Tecnología y software": ["neofuturista", "cristalino", "minimalista"],
    "Agencia o marketing": ["pop", "brutalista", "retro"],
    "Eventos y bodas": ["editorial", "lujo", "documental"],
    "Fotografía y video": ["documental", "editorial", "minimalista"],
    "Hotelería y turismo": ["lujo", "editorial", "organico"],
    "Mascotas": ["claymorfismo", "pop", "documental"],
    "Automotriz": ["neofuturista", "brutalista", "lujo"],
    "Tienda en línea": ["pop", "minimalista", "editorial"],
}
RECOMENDADOS_INICIAL = ["minimalista", "editorial", "documental"]  # sin giro (o uno escrito a mano)

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
    # clave: (nombre, cómo se ve, cómo se le pide al generador)
    "sutil": ("Sutil",
              "Predominan los colores propios del entorno, los materiales y los elementos fotografiados. Los colores"
              " de marca aparecen en pequeños detalles o acentos.",
              "The scene's own colors (environment, materials, photographed objects) dominate; brand colors appear"
              " only in small details or accents."),
    "equilibrado": ("Equilibrado",
                    "Los colores corporativos tienen una presencia reconocible, pero se integran con el entorno y los"
                    " elementos de la imagen sin dominarla por completo.",
                    "Brand colors have a recognizable presence but blend with the environment and the natural colors"
                    " of the scene, without fully dominating the image."),
    "protagonista": ("Protagonista",
                     "Tienen una presencia destacada en los productos, el fondo, los elementos gráficos o la"
                     " composición. El entorno puede ser natural o artificial; lo importante es que la identidad"
                     " visual resulte evidente.",
                     "Brand colors define the image: a strong presence in the products, the background, the graphic"
                     " elements or the composition, so the visual identity is obvious (natural or artificial setting)."),
    "natural": ("Natural / Sin protagonismo de marca",
                "Se utilizan principalmente los colores reales del producto, el entorno y los materiales. Los colores"
                " de marca pueden aparecer muy poco o no aparecer.",
                "Prioritize the scene and its own real colors (product, environment, materials); brand colors may"
                " appear very little or not at all. Do not force the brand palette."),
}
# Lo que solo ve el panel: la frase corta.
USO_TARJETA = {
    "sutil": "Los colores de marca acompañan, pero no dominan.",
    "equilibrado": "Los colores de marca conviven con los colores naturales.",
    "protagonista": "Los colores de marca definen la imagen.",
    "natural": "La imagen prioriza la escena y sus colores propios.",
}
VIEJOS_USOS = {"oscuro": "natural", "contraste": "natural"}  # opciones de versiones anteriores
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

# Tipografía recomendada según el estilo principal: (titulares, textos, por qué encaja).
COMBINACIONES = {
    "lujo": ("Cormorant Garamond", "Inter", "Una combinación refinada y discreta: titulares con elegancia clásica y"
             " textos limpios que no compiten con la imagen."),
    "minimalista": ("Raleway", "Work Sans", "Una combinación ligera y ordenada que deja respirar tus diseños y da"
                    " protagonismo al contenido."),
    "editorial": ("Playfair Display", "Inter", "Una combinación elegante de revista: titulares con carácter y textos"
                  " limpios que transmiten autoridad."),
    "artesanal": ("Lora", "Work Sans", "Una combinación cálida y humana que acompaña las texturas y el trabajo hecho a"
                  " mano."),
    "organico": ("Nunito", "Lora", "Una combinación suave y serena: titulares redondeados y textos con un toque"
                 " natural."),
    "retro": ("Oswald", "Lora", "Una combinación con carácter nostálgico: titulares condensados de cartel y textos"
              " clásicos, con aire actual."),
    "pop": ("Poppins", "DM Sans", "Una combinación con energía y carácter que aguanta colores intensos y"
            " composiciones atrevidas."),
    "documental": ("Work Sans", "Inter", "Una combinación sencilla y honesta que deja hablar a las fotografías."),
    "brutalista": ("Oswald", "Inter", "Una combinación contundente y directa: titulares que se imponen y textos claros"
                   " que se leen sin esfuerzo."),
    "claymorfismo": ("Nunito", "Inter", "Una combinación cercana, moderna y legible que acompaña tus diseños sin quitar"
                     " protagonismo al contenido."),
    "cristalino": ("DM Sans", "Inter", "Una combinación moderna y suave que acompaña las transparencias y los reflejos"
                   " sin recargar el diseño."),
    "neofuturista": ("Space Grotesk", "Inter", "Una combinación precisa y tecnológica que refuerza la sensación de"
                     " innovación sin perder legibilidad."),
}
COMBINACION_INICIAL = ("Montserrat", "Inter", "Una combinación clara, moderna y segura que funciona para casi cualquier"
                       " negocio.")
MUESTRA_TITULAR = "Tu negocio, más cerca de tus clientes."
MUESTRA_TEXTO = "Descubre cómo podemos ayudarte con una experiencia pensada para ti."

LOGO_PRESENCIA = {
    # clave: (nombre, frase corta, para qué sirve)
    "sin": ("Sin logotipo", "Contenido limpio y natural, sin añadir el logotipo de forma visible.",
            "Ideal para publicaciones espontáneas, consejos, contenido educativo, frases o imágenes donde el producto"
            " debe ser el protagonista."),
    "discreta": ("Firma discreta", "Logotipo pequeño en una esquina, sin robar protagonismo.",
                 "Para mantener una presencia de marca sutil."),
    "visible": ("Firma visible", "El logotipo se reconoce fácilmente y forma parte del diseño.",
                "Para publicaciones comerciales y contenido pensado para reforzar el reconocimiento de marca."),
    "clave": ("Solo cuando aporta valor", "El logotipo aparece cuando tiene sentido para el contenido.",
              "Por ejemplo, en promociones, lanzamientos, anuncios o productos con su propia identidad visual."),
    "siempre": ("Siempre presente", "Todas las publicaciones incluyen el logotipo en una posición reconocible.",
                "Para negocios que priorizan el reconocimiento constante de marca."),
}
LOGO_INICIAL = "discreta"
