# IG Bot: agente de Instagram con Claude

Contesta los DMs de la cuenta de Instagram con un agente hecho con el **Claude Agent SDK**, con memoria por persona.

Dominio del webhook: `bot.skandar.online`. Repo: `skandalo80/Agent`.

## Cómo funciona

```
Instagram → Caddy (HTTPS) → POST /webhook (app.py, puerto 8001)
   1. valida la firma, guarda el DM como "pendiente" en SQLite y responde 200 al instante
   2. Dispatcher: una sola tarea por persona → espera 5 s por si escribe más,
      junta los mensajes seguidos y los contesta en orden
   3. Agente: datos guardados + resumen + últimos 20 mensajes → Claude → respuesta
   4. Envía la respuesta por la API de Instagram y marca los DMs como contestados
```

| Archivo | Qué hace |
|---|---|
| `app.py` | Webhook, arranque y apagado |
| `igbot/dispatcher.py` | Cola: orden por persona, agrupar mensajes seguidos, límite de conversaciones a la vez, reintentos |
| `igbot/store.py` | Toda la memoria en SQLite (`data/bot.db`). Es el único archivo a cambiar si algún día se pasa a Postgres |
| `igbot/agent.py` | Arma el contexto y llama a Claude |
| `igbot/tools.py` | Herramientas del agente y sus reglas: `recordar`, `olvidar`, `ver_disponibilidad`, `agendar_reunion`, `mis_reuniones`, `cancelar_reunion`, `enviar_correo`, `cotizar`, `enviar_cotizacion`, `pasar_a_humano`, y en la tienda `ver_productos`, `ver_existencias`, `cotizar_pedido` |
| `igbot/tienda.py` | Tienda de Nortex: lee catálogo y existencias de Google Sheets y cotiza pedidos por volumen |
| `igbot/catalog.py` | Lee el catálogo, calcula las cotizaciones y arma el PDF |
| `igbot/pdf_marca.py` | Diseño de los PDFs de cotización (Inter, negro y grafito, un acento en `negocio.color_acento`); las fuentes están en `fonts/` |
| `negocio/catalogo.yaml` | Paquetes, precios y condiciones. Edítalo y reinicia para cambiar precios |
| `igbot/google.py` | Llamadas a Google Calendar, Gmail y Sheets (solo lectura) |
| `igbot/instagram.py` | Envía DMs (parte los de más de 1000 caracteres) y renueva el token cada semana |
| `clientes/<nombre>/` | Prompt, reglas y `.env` de otra cuenta de Instagram (hoy: `nortex`) |
| `prompts/system.md` | Personalidad e instrucciones del bot (hoy: asesor de ventas). Edítalo y reinicia para cambiar cómo habla. El catálogo se le agrega solo |

**Memoria.** Cada persona tiene su historial completo, un resumen de lo viejo y "datos guardados" que el agente anota con `recordar`. En cada respuesta Claude ve: datos + resumen + los últimos `HISTORY_WINDOW` mensajes (20). Cuando sobran 10 mensajes viejos se condensan en el resumen, así el costo por respuesta no crece.

**Seguridad.** El agente no tiene ninguna herramienta de Claude Code (ni Bash ni archivos): solo las de `igbot/tools.py`, atadas a la persona que escribe. Las reglas están en el código, no en el prompt, así que un DM no puede saltárselas:
- Solo invita o escribe a un correo que la persona escribió en el chat (nadie puede usar el bot para mandar correos a terceros).
- Solo agenda dentro del horario de atención, con anticipación mínima, en huecos libres y con un máximo de reuniones por persona.
- Solo puede ver y cancelar las reuniones que agendó esa misma persona. De tu calendario solo ve libre/ocupado, nunca el contenido de tus eventos.
- Máximo de correos por persona al día (las cotizaciones cuentan como correo).
- Los precios los calcula el código desde `negocio/catalogo.yaml`; Claude solo elige paquete y opciones. Los PDFs se sirven con un nombre aleatorio que no se puede adivinar.

**Si se reinicia.** Los DMs sin contestar quedan como pendientes en la base y se contestan al arrancar. Si Meta reenvía un DM, se ignora (se reconoce por su id).

**Escalar.** Se ajusta con `MAX_CONCURRENT` (conversaciones a la vez). Corre con un solo proceso de uvicorn: no subas `--workers`, la cola vive dentro del proceso. Para varios servidores habría que pasar la cola a Redis y la memoria a Postgres.

## Actualizar el servidor a la etapa 2

```bash
cd ~/ig-bot && git pull
.venv/bin/pip install -r requirements.txt
nano .env   # añade ANTHROPIC_API_KEY=... (ver .env.example)
sudo systemctl restart igbot
sudo journalctl -u igbot -f
```

Manda un DM desde la cuenta de prueba; en los logs aparece `Claude: N turnos, US$...` con el costo de cada respuesta.

Respaldo diario de la memoria (opcional, `crontab -e`):
```
0 4 * * * sqlite3 ~/ig-bot/data/bot.db ".backup ~/ig-bot/data/bot-$(date +\%a).db"
```
(necesita `sudo apt install sqlite3`; guarda una copia por día de la semana)

Pruebas locales: `pip install pytest && python -m pytest -q`

## Ventas: cotizaciones y pasar a humano

**Cotizaciones.** El agente pregunta por el negocio del cliente, recomienda un paquete y da precios con `cotizar`. Si el cliente quiere la cotización formal, el agente le pregunta si la quiere por aquí o por correo (el correo no es obligatorio) y `enviar_cotizacion` genera un PDF con folio (`COT-0001`, `COT-0002`...) y lo manda por DM, por correo con copia a `MAIL_CC`, o por ambos. Los PDFs quedan en `data/cotizaciones/` y el registro en la tabla `quotes`. Necesita Google configurado (sección siguiente) y `PUBLIC_URL` en `.env` para el DM.

**Pasar a humano.** Si el cliente pide hablar con alguien, quiere descuento o algo fuera del catálogo, el agente usa `pasar_a_humano`: te llega un correo (a `OWNER_EMAIL`, por defecto tu Gmail) con el motivo y los últimos mensajes, y el bot se calla en esa conversación durante `HUMAN_PAUSE_HOURS` (12 h).

**Si contestas tú desde Instagram**, el bot lo detecta (Meta le avisa de cada DM que sale de la cuenta) y también se calla en esa conversación durante `HUMAN_PAUSE_HOURS`. Lo que escribiste queda en su memoria, así que al volver sigue desde ahí.

**Límites contra abuso.** El bot solo habla del negocio: si le piden tareas, código, traducciones o cualquier cosa ajena, contesta en una frase que eso no lo ve y regresa al tema (reglas en `prompts/comun.md`, junto con el estilo de escritura). Además, cada DM se corta a `MAX_MESSAGE_CHARS` (1000), cada persona recibe como mucho `MAX_REPLIES_PER_USER_PER_DAY` respuestas al día (30; al llegar se le avisa una vez y el bot se calla hasta mañana) y la cuenta gasta como mucho `DAILY_BUDGET_USD` al día en Claude (US$5; al pasarlo deja de contestar hasta mañana y te avisa por correo). Los administradores no tienen tope.

**Actualizar el servidor:**
```bash
cd ~/ig-bot && git pull
.venv/bin/pip install -r requirements.txt
echo "PUBLIC_URL=https://bot.skandar.online" >> .env
sudo systemctl restart igbot
```

## Varias cuentas de Instagram

Un solo proceso (`igbot`, puerto 8001) atiende todas las cuentas sobre el mismo código. La principal (@skandar.online, el vendedor) usa el `.env`; cada cliente tiene su carpeta `clientes/<nombre>/` (prompt `system.md`, `catalogo.yaml` opcional y `.env` con su token) y sus datos en `data/<nombre>/`. Las cuentas de clientes que se atienden se listan en el `.env` principal:

```
CUENTAS=cafelunar,otra_cuenta
```

Meta manda los avisos de todas las cuentas al mismo webhook (`bot.skandar.online/webhook`). Cada aviso trae el id de su cuenta y el bot lo atiende con la configuración de esa cuenta: su base, su token, su prompt y sus límites (ver `igbot/cuenta.py`). Cada cuenta tiene su cola; `MAX_CONCURRENT` (4) es el tope de respuestas a la vez entre todas y `MAX_CONCURRENT_PER_ACCOUNT` (3) el de una sola cuenta. La renovación de tokens, las publicaciones programadas y el reporte semanal son un bucle por tarea que pasa por todas las cuentas.

Para dar de alta un cliente que ya se conectó en skandar.online/conectar:
```bash
cd ~/ig-bot && .venv/bin/python scripts/alta_cliente.py cafe.lunar cafelunar
nano clientes/cafelunar/system.md       # el prompt del negocio
nano .env                                # agrega cafelunar a CUENTAS
sudo systemctl restart igbot
sudo journalctl -u igbot -f              # dice "Cuenta de Instagram: @cafe.lunar"
```
En esa cuenta revisa también: Configuración → Mensajes → *Permitir acceso a los mensajes*.

Si un cliente aún corre como proceso aparte (`igbot@<nombre>`, de antes de este cambio): agrégalo a `CUENTAS`, quita `FORWARD_WEBHOOKS` del `.env` principal, `sudo systemctl disable --now igbot@<nombre>` y `sudo systemctl restart igbot`. Nunca dejes la misma cuenta en `CUENTAS` y en `igbot@<nombre>` a la vez: contestaría dos veces. El modo de un proceso por cuenta (`CLIENTE=<nombre>`, `deploy/igbot@.service`) sigue funcionando.

**Modelo por cuenta.** Cada cuenta puede usar otro modelo con `CLAUDE_MODEL` en su `clientes/<nombre>/.env`. Para un cliente del paquete Básico (solo contesta con la información del negocio) usa el más barato: `CLAUDE_MODEL=claude-haiku-5-5`.

**Catálogo y existencias de Nortex (Google Sheets).** Los productos, precios por pieza y existencias se leen de las hojas `SHEET_CATALOG` y `SHEET_INVENTORY` (en `clientes/nortex/.env`, ya vienen en el ejemplo) y se guardan 5 minutos en memoria, así que un cambio en la hoja se nota en unos minutos sin reiniciar. Las reglas que no están en las hojas (rangos por volumen, bordado y estampado, IVA, entregas y condiciones) están en `clientes/nortex/reglas.yaml`. El agente tiene `ver_productos`, `ver_existencias` y `cotizar_pedido`; las cuentas las hace `igbot/tienda.py`, contando las piezas por tipo de prenda. Con `enviar_cotizacion_pedido` manda la cotización formal en PDF (con folio y vigencia) por DM, al correo que la persona escribió (con copia a `MAIL_CC`) o por ambos, según lo que ella elija; el PDF lo sirve el proceso principal en `PUBLIC_URL/cotizaciones/nortex/...`.

Para que el bot pueda leer las hojas (una sola vez):
1. En Google Cloud, en el mismo proyecto de la agenda: *APIs y servicios* → *Biblioteca* → **Google Sheets API** → *Habilitar*. En *Pantalla de consentimiento* → *Acceso a datos* agrega el permiso `.../auth/spreadsheets.readonly`.
2. En tu computadora vuelve a correr `python3 scripts/google_auth.py ruta/a/client_secret.json` (ahora pide también ver tus hojas) y reemplaza `GOOGLE_REFRESH_TOKEN` en `~/ig-bot/.env` con el nuevo. Las hojas deben ser de esa misma cuenta de Google o estar compartidas con ella.
3. `sudo systemctl restart igbot`.

## Conectar clientes desde la web (skandar.online/conectar)

Mientras la app de Meta no tenga App Review, solo se conectan las cuentas invitadas como **Instagram Tester**. La página `/conectar` ordena el proceso:
1. El cliente deja su @, nombre y correo. Te llega un DM (si le escribiste al bot en las últimas 24 h) y un correo a `OWNER_EMAIL`.
2. Tú lo invitas a mano: panel de Meta → Roles de la app → **Instagram Testers** → su @. Le avisas y él acepta en instagram.com → Configuración → Apps y sitios web → Invitaciones de prueba.
3. Pulsa **Conectar tu Instagram**: inicia sesión, autoriza y el bot guarda su token de 60 días en la tabla `conexiones` de `data/bot.db`. Te vuelve a avisar.
4. Su cuenta se da de alta sola, **en pausa**, como paquete Básico: `clientes/<su_usuario>/` con su `.env` (token, id, `CLAUDE_MODEL=claude-haiku-5-5`, `GOOGLE_TOOLS=0` y `OWNER_EMAIL` = el correo del formulario) y un `system.md` inicial. No hace falta reiniciar ni tocar `CUENTAS`: al arrancar, el bot carga también las cuentas dadas de alta así.
5. El cliente cae en su **panel** (`bot.skandar.online/panel`): llena a qué se dedica, horarios, precios, preguntas frecuentes, políticas y qué hacer si piden una persona; elige el tono (amigable, profesional o formal) y pulsa *Generar instrucciones*; las revisa, *Guardar* y *Encender*. Para volver al panel entra otra vez con Instagram (la sesión dura 30 días; Instagram vuelve a pedir el permiso cada vez que se entra). Lo que escribe va antes de `prompts/comun.md`, así que no puede quitar las reglas comunes.

Las cuentas de `CUENTAS` (dadas de alta con `scripts/alta_cliente.py`) también pueden entrar al panel: ven su `system.md` actual y lo pueden cambiar. Para ellas la pausa del panel es opcional (si nunca la tocan, contestan).

`GOOGLE_TOOLS=0` en el `.env` de una cuenta le quita agendar en tu calendario, mandar correos y cotizar con tu Google (sigue pudiendo pasar a una persona, que avisa por correo a `OWNER_EMAIL`). Pónselo a cualquier cliente que no deba usar tu Google.

Una sola vez: en el `.env` principal pon `IG_APP_ID` (y `IG_OAUTH_SECRET` si el "Instagram app secret" no es el mismo que `IG_APP_SECRET`), y en el panel de Meta, en *API de Instagram con inicio de sesión de Instagram* → *Configurar inicio de sesión para empresas*, agrega la URL de redirección `https://bot.skandar.online/conectar/callback`.

## Comentarios: palabra clave → DM

Cuando alguien comenta una publicación con una palabra clave (p. ej. PRECIO), el bot le manda un DM privado con el mensaje que tú definas, y opcionalmente responde en público. Las reglas viven en una hoja de Google Sheets (`SHEET_COMMENTS` en el `.env` de la cuenta) con tres columnas, en este orden:

| Link de la publicación | Palabra clave | Mensaje por DM |
|---|---|---|
| https://www.instagram.com/p/ABC123/ | PRECIO | Hola! Nuestros precios... |
| https://www.instagram.com/p/ABC123/ | catálogo, catalogo | Aquí está el catálogo... |

- Solo reaccionan las publicaciones que estén en la hoja. Para varias palabras en una publicación, una fila por cada una (o varias palabras separadas por comas en la misma celda).
- No importan mayúsculas, acentos ni plurales. Si el comentario no trae la palabra exacta, Claude decide si se refiere a ella ("¿cuánto cuestan?" → PRECIO).
- Si un comentario pide varias cosas, se manda un solo DM con todos los mensajes (Instagram permite un DM por comentario, dentro de 7 días).
- Una cuarta columna opcional es una respuesta pública al comentario.
- **Filtro de spam** (opcional, `HIDE_SPAM=1` en el `.env` de la cuenta): en todas las publicaciones, Claude revisa cada comentario que no trae una palabra clave y oculta el spam (publicidad ajena, "sígueme", estafas, links sospechosos, contenido sexual, insultos). Las quejas y críticas nunca se ocultan. Oculto no es borrado: lo sigue viendo quien lo escribió y tú lo puedes mostrar desde Instagram. Funciona aunque no haya hoja de reglas.
- Los cambios en la hoja se notan en unos 5 minutos, sin reiniciar. Los comentarios quedan en la tabla `comments` (para el reporte semanal).

Para activarlo en una cuenta:
1. En la app de Meta → *API de Instagram con inicio de sesión de Instagram*: en **Webhooks** suscribe el campo `comments`, y revisa que la app tenga el permiso `instagram_business_manage_comments` (si no lo tenía, genera de nuevo el token de esa cuenta y reemplázalo en su `.env`).
2. Crea la hoja (de tu misma cuenta de Google) y pon su link en `SHEET_COMMENTS` del `.env` de la cuenta.
3. Reinicia (`sudo systemctl restart igbot@nortex`). Al arrancar, el bot suscribe la cuenta a mensajes y comentarios.

## Botones de inicio

Las preguntas que Instagram muestra cuando alguien abre un chat nuevo con la cuenta (máximo 4, de hasta 80 caracteres) viven en `negocio/botones.yaml` (vendedor) y `clientes/<cuenta>/botones.yaml`. Al reiniciar, el bot se las manda a Instagram si cambiaron. Cuando alguien toca una, el bot la contesta como cualquier mensaje.

Para que lleguen los toques, en la app de Meta → *Webhooks* suscribe también el campo `messaging_postbacks`.

## Administradores: publicar por DM y reactivar chats

Si tu id de Instagram está en `ADMIN_IG_IDS` (en el `.env` de la cuenta), cuando le escribes al bot no te atiende el vendedor, sino un asistente interno:

- **Publicar:** mándale una o varias fotos (varias = carrusel) y el texto. Te regresa las fotos como vista previa y el texto tal cual saldrá, y publica solo cuando le confirmas en otro mensaje. Puedes decir cuándo ("mañana a las 10"): queda programada y te avisa por DM cuando sale. También puedes pedirle lo programado o cancelarlo. Por ahora solo fotos, no videos.
- **Reactivar chats:** "¿qué conversaciones están en pausa?" y "reactiva a @usuario". El bot vuelve a contestarle desde su siguiente mensaje.
- **Probar como cliente:** escribe `modo cliente` para hablar con el bot como cualquier cliente y `modo admin` para volver.

Las fotos se descargan al llegar (los links de Instagram caducan), se convierten a JPG y, si son más verticales que 4:5 o más horizontales que 1.91:1, se recortan al centro porque Instagram no las acepta. Se sirven en `PUBLIC_URL/media/<cuenta>/...` para que Instagram las descargue; el proceso principal las sirve para todas las cuentas, así que no hay que tocar Caddy.

Para activarlo en una cuenta:
1. En la app de Meta, agrega el permiso `instagram_business_content_publish`, genera de nuevo el token de esa cuenta y reemplázalo en su `.env`.
2. Escríbele cualquier cosa al bot desde tu Instagram y busca tu id en el log (`journalctl -u igbot@nortex | grep "DM de"` para Nortex, `journalctl -u igbot | grep "DM de"` para el vendedor). Ponlo en `ADMIN_IG_IDS` del `.env` de la cuenta (el id cambia en cada cuenta del negocio).
3. Revisa que el `.env` principal tenga `PUBLIC_URL`, y reinicia.

## Imágenes para contenido (Gemini)

`igbot/imagenes.py` arma piezas para el feed: Gemini pinta la imagen sin letras
y el servidor le pone título, subtítulo, precio y firma con una de 4 plantillas
(`editorial`, `minimal`, `marco`, `banda`) con la tipografía (Inter, Montserrat, Poppins,
Playfair Display o Lora, en `fonts/`, todas OFL) y los colores de la marca. Cambiar
el texto no genera otra imagen. Cada pieza queda en la tabla `media`, lista para
publicarse con el mismo flujo de las publicaciones de los administradores.

En `.env`: `GEMINI_API_KEY` (aistudio.google.com/apikey, cuenta de prepago) y,
por cuenta, `IMAGES_PER_MONTH` (Community 40, Pro 80; 0 = sin contenido). Para
probar en el servidor:

Con `IMAGES_PER_MONTH` > 0 el panel del cliente muestra **Contenido**
(`igbot/contenido.py`, `/panel/contenido`): su kit de marca (colores, tipografía,
estilo de fotos, fotos de producto), crear un post a partir de un objetivo
(Claude escribe con `CONTENT_MODEL`, Gemini pinta), editarlo, aprobarlo y el
calendario. Los posts aprobados los publica el bucle de publicaciones y su palabra
clave contesta comentarios igual que la hoja de `SHEET_COMMENTS`. Pasado el cupo,
el cliente confirma la imagen extra (`EXTRA_IMAGE_USD`) y queda anotada en
`events` ('imagen', tipo 'extra') para cobrarla. El CSP del bot necesita
`img-src 'self'` (ver `deploy/Caddyfile`).

```bash
.venv/bin/python scripts/probar_imagen.py --foto producto.jpg     # sin gastar
.venv/bin/python scripts/probar_imagen.py --idea "Cartera de piel sobre granito"  # 1 imagen
```

## Reporte semanal

Cada lunes a las 9:00 (hora del negocio) llega un resumen de los últimos 7 días: personas que escribieron (y cuántas nuevas), respuestas del bot, cotizaciones (cuántas personas, monto total y lo más cotizado), conversaciones pasadas a una persona o contestadas a mano, comentarios con palabra clave, publicaciones, y un resumen de Claude de lo que más preguntan y qué oportunidades hay.

- Por correo a `REPORT_EMAIL` (en el `.env` de la cuenta; necesita Google configurado) y, en versión corta, por DM a los administradores. Instagram solo deja mandar DMs dentro de las 24 h desde el último mensaje del administrador; si no, solo llega el correo.
- Un administrador lo puede pedir cuando quiera: "mándame el reporte".
- Cambia el día y la hora con `REPORT_DAY` (1=lunes) y `REPORT_HOUR`.

## Agenda y correo con tu Google

Opcional: sin esto el bot sigue contestando, solo que sin agendar ni mandar correos. Se hace una sola vez.

**1. Proyecto en Google Cloud** (gratis): entra a [console.cloud.google.com](https://console.cloud.google.com) con tu Gmail y crea un proyecto, por ejemplo `ig-bot`.

**2. Activar las APIs:** menú → *APIs y servicios* → *Biblioteca*. Busca y habilita **Google Calendar API** y **Gmail API**.

**3. Pantalla de consentimiento:** menú → *Google Auth Platform* (o *Pantalla de consentimiento de OAuth*).
- Tipo de usuario: **Externo**. Nombre de la app: `ig-bot`; correo de asistencia: el tuyo.
- En *Público*, agrega tu Gmail como usuario de prueba y luego pulsa **Publicar app** (estado *En producción*). Si se queda en *Prueba*, Google corta el permiso cada 7 días.

**4. Credencial:** *Google Auth Platform* → *Clientes* → *Crear cliente* → tipo **App de escritorio**. Descarga el JSON y guárdalo como `client_secret.json` en tu computadora (no lo subas a GitHub ni lo pegues en el chat).

**5. Dar el permiso** (en tu computadora, porque abre el navegador):
```bash
git clone https://github.com/skandalo80/Agent.git && cd Agent
python3 -m pip install google-auth-oauthlib
python3 scripts/google_auth.py ~/Downloads/client_secret.json
```
Elige tu cuenta. Google va a decir *"Google no verificó esta app"*: es normal porque la app es tuya. Pulsa *Configuración avanzada* → *Ir a ig-bot*, y acepta los permisos (calendario y enviar correos). Al terminar, la terminal imprime tres líneas `GOOGLE_...`.

**6. En el servidor:** pega esas tres líneas en `~/ig-bot/.env`, y además:
```
MAIL_FROM=tu-correo@gmail.com
MAIL_FROM_NAME=Nombre que verán tus clientes
```
Revisa también horario, zona y límites (están en `.env.example`) y reinicia con `sudo systemctl restart igbot`.

**Probar:** desde la cuenta de prueba escribe algo como "quiero agendar una llamada mañana" y dale tu correo cuando lo pida. Te debe llegar la invitación con link de Meet.

---

# Instalación desde cero (etapa 1)

## 1. DNS (en el panel de tu dominio)

Crea un registro **A**: nombre `bot`, valor = la **IP pública** de tu instancia de Oracle. Comprueba desde tu PC con `ping bot.skandar.online` que responde esa IP (puede tardar unos minutos).

## 2. Abrir puertos en Oracle Cloud

Oracle bloquea el tráfico en **dos sitios**, hay que abrir ambos:

1. **Consola de Oracle** → Networking → Virtual Cloud Networks → tu VCN → Security Lists → Default → *Add Ingress Rules*: origen `0.0.0.0/0`, TCP, puertos `80` y `443`.
2. **Dentro de la instancia** (las imágenes de Ubuntu de Oracle traen iptables cerrado):
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```

## 3. Instalar la app en la instancia (por SSH)

```bash
sudo apt update && sudo apt install -y python3-venv git
cd ~ && git clone https://github.com/skandalo80/Agent.git ig-bot && cd ig-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
```

Edita `.env` con `nano .env`. De momento solo inventa `IG_VERIFY_TOKEN` (una frase larga cualquiera); el resto lo rellenas en el paso 5. **No pegues tokens en el chat ni los subas a GitHub.**

Arranca la app como servicio (si tu usuario no es `ubuntu`, ajusta las rutas en `deploy/igbot.service`):

```bash
sudo cp deploy/igbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now igbot
```

## 4. Caddy + HTTPS

La instancia usa Caddy, que saca y renueva el certificado HTTPS solo. Añade el bloque de `deploy/Caddyfile` a la config y recarga:

```bash
cat deploy/Caddyfile | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Comprueba: `curl https://bot.skandar.online/health` debe mostrar `{"ok":true}`. Si el servidor dice `Could not resolve host` pero `dig +short bot.skandar.online @1.1.1.1` sí da la IP, es caché de DNS local: usa `curl --resolve bot.skandar.online:443:IP_DE_LA_INSTANCIA https://bot.skandar.online/health`.

El bot escucha en el puerto 8001 porque el 8000 ya lo usaba otra app en esta instancia.

## 5. App de Meta (developers.facebook.com)

1. **Mis apps → Crear app**. Caso de uso: *Gestionar mensajes y contenido en Instagram*. Tipo: Business.
2. En el panel: **Instagram → Configuración de la API con inicio de sesión de Instagram**.
3. **Generar tokens de acceso**: añade tu cuenta profesional de Instagram e inicia sesión. Copia el token y ponlo en `.env` como `IG_ACCESS_TOKEN`.
4. En esa misma página copia la **Clave secreta de la app de Instagram** (no la de Facebook) y ponla en `.env` como `IG_APP_SECRET`.
5. Reinicia la app: `sudo systemctl restart igbot`.
6. **Configurar webhooks**:
   - URL de devolución de llamada: `https://bot.skandar.online/webhook`
   - Token de verificación: el mismo `IG_VERIFY_TOKEN` de tu `.env`
   - Pulsa *Verificar y guardar*, y después **suscribe el campo `messages`**.
7. En la fila de tu cuenta de Instagram, activa la **suscripción a webhooks** si aparece el interruptor.

## 6. Probar

Desde **otra** cuenta de Instagram, manda un DM a tu cuenta profesional. Debería contestar el agente.

Ver lo que pasa en vivo:
```bash
sudo journalctl -u igbot -f
```

### Si no responde

- **No aparece nada en los logs al mandar un DM**: con la app en modo *Desarrollo*, Meta solo entrega mensajes de cuentas con rol en la app. Añade la cuenta que envía como **Instagram Tester** (Roles de la app → Roles) y acepta la invitación desde Instagram (Configuración → Apps y sitios web → Invitaciones de tester). También revisa en la app de Instagram de tu cuenta profesional: Configuración → Mensajes → *Permitir acceso a los mensajes* (herramientas conectadas).
- **Aparece "Fallo contestando a ..."**: el error viene debajo. Si es de Instagram (401/400) casi siempre es el token; si es de Claude, revisa `ANTHROPIC_API_KEY`.
- **La verificación del webhook falla**: comprueba que `/health` abre con HTTPS y que el token de verificación coincide exactamente.

## Página web (skandar.online)

La página del negocio está en `web/` (HTML estático, sin build): `index.html` y `privacidad.html` (aviso de privacidad completo, sirve para Meta). Caddy la sirve desde `/var/www/skandar.online`, porque el usuario `caddy` no puede leer tu carpeta personal.

1. DNS: registros A de `skandar.online` (`@`) y `www` apuntando a la IP de la instancia.
2. Revisa que nadie use ya esos nombres: `grep -n "skandar.online" /etc/caddy/Caddyfile` (solo debe salir `bot.skandar.online`).
3. Copia los archivos y añade el bloque:

```bash
sudo mkdir -p /var/www/skandar.online
sudo cp ~/ig-bot/web/* /var/www/skandar.online/
cat ~/ig-bot/deploy/Caddyfile-web | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Para actualizarla después: `git pull` y repetir el `cp`. No hace falta recargar Caddy.

La página incluye `404.html`, `robots.txt`, `sitemap.xml`, `og.png` (la vista previa al compartir el link por Instagram o WhatsApp) y `sitio.js`. No hay scripts ni estilos dentro del HTML porque la CSP de Caddy solo permite archivos del propio dominio; si agregas uno, ponlo en un archivo aparte. Si cambias la página, actualiza la fecha en `sitemap.xml`.

### Cabeceras de seguridad

`deploy/Caddyfile` (bot) y `deploy/Caddyfile-web` (página) traen HSTS, CSP, anti-iframe y nosniff; la página además manda su 404 propia. Si ya tenías los bloques viejos en `/etc/caddy/Caddyfile`, reemplázalos (no toques los de otras apps):

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
sudo nano /etc/caddy/Caddyfile   # borra los bloques bot.skandar.online, skandar.online y www.skandar.online
cat ~/ig-bot/deploy/Caddyfile ~/ig-bot/deploy/Caddyfile-web | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy
curl -sI https://skandar.online | grep -i strict   # debe salir Strict-Transport-Security
```

Si `validate` falla, regresa con `sudo cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile`.

## Respaldos

`scripts/respaldo.py` copia cada base de `data/` a `~/respaldos/AAAA-MM-DD/` (14 días). Se activa una vez:

```bash
sudo cp ~/ig-bot/deploy/igbot-respaldo.service ~/ig-bot/deploy/igbot-respaldo.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now igbot-respaldo.timer
sudo systemctl start igbot-respaldo && ls ~/respaldos   # prueba ahora mismo
```

Esto protege contra borrados o una base dañada, pero vive en el mismo disco. Para cubrir que se pierda la instancia, haz de vez en cuando un respaldo del volumen de arranque en Oracle Cloud (Storage → Boot volumes → Create backup; la capa gratuita incluye 5).

Para restaurar: `sudo systemctl stop igbot`, copia el `.db` del día a `data/` y `sudo systemctl start igbot`.
