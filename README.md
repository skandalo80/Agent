# IG Bot: agente de Instagram con Claude

Contesta los DMs de la cuenta de Instagram con un agente hecho con el **Claude Agent SDK**, con memoria por persona.

Dominio del webhook: `bot.skandar.online`. Repo: `skandalo80/Agent`.

## Cómo funciona

```
Instagram → Caddy (HTTPS) → POST /webhook (app.py, puerto 8001)
   1. valida la firma, guarda el DM como "pendiente" en SQLite y responde 200 al instante
   2. Dispatcher: una sola tarea por persona → espera 3 s por si escribe más,
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
| `igbot/tools.py` | Herramientas del agente y sus reglas: `recordar`, `olvidar`, `ver_disponibilidad`, `agendar_reunion`, `mis_reuniones`, `cancelar_reunion`, `enviar_correo` |
| `igbot/google.py` | Llamadas a Google Calendar y Gmail |
| `igbot/instagram.py` | Envía DMs (parte los de más de 1000 caracteres) y renueva el token cada semana |
| `prompts/system.md` | Personalidad e instrucciones del bot. Edítalo y reinicia para cambiar cómo habla |

**Memoria.** Cada persona tiene su historial completo, un resumen de lo viejo y "datos guardados" que el agente anota con `recordar`. En cada respuesta Claude ve: datos + resumen + los últimos `HISTORY_WINDOW` mensajes (20). Cuando sobran 10 mensajes viejos se condensan en el resumen, así el costo por respuesta no crece.

**Seguridad.** El agente no tiene ninguna herramienta de Claude Code (ni Bash ni archivos): solo las de `igbot/tools.py`, atadas a la persona que escribe. Las reglas están en el código, no en el prompt, así que un DM no puede saltárselas:
- Solo invita o escribe a un correo que la persona escribió en el chat (nadie puede usar el bot para mandar correos a terceros).
- Solo agenda dentro del horario de atención, con anticipación mínima, en huecos libres y con un máximo de reuniones por persona.
- Solo puede ver y cancelar las reuniones que agendó esa misma persona. De tu calendario solo ve libre/ocupado, nunca el contenido de tus eventos.
- Máximo de correos por persona al día.

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
