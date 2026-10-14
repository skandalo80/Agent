# IG Bot · Etapa 1: responder "hola" a cada DM

Un webhook pequeño en Python (FastAPI) que recibe los DMs de tu cuenta profesional de Instagram y contesta "hola". Sirve para probar que Instagram y tu instancia de Oracle están conectados. El agente con Claude viene después.

Cómo funciona: Instagram → `https://bot.skandar.online/webhook` (Caddy con HTTPS) → la app en el puerto 8001 → responde con la API de Instagram.

Dominio del webhook: `bot.skandar.online`. Repo: `skandalo80/Agent`.

---

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

Desde **otra** cuenta de Instagram, manda un DM a tu cuenta profesional. Debería llegar "hola".

Ver lo que pasa en vivo:
```bash
sudo journalctl -u igbot -f
```

### Si no responde

- **No aparece "Evento recibido" en los logs**: con la app en modo *Desarrollo*, Meta solo entrega mensajes de cuentas con rol en la app. Añade la cuenta que envía como **Instagram Tester** (Roles de la app → Roles) y acepta la invitación desde Instagram (Configuración → Apps y sitios web → Invitaciones de tester). También revisa en la app de Instagram de tu cuenta profesional: Configuración → Mensajes → *Permitir acceso a los mensajes* (herramientas conectadas).
- **"Evento recibido" sale pero hay "Error enviando mensaje"**: el error de Meta aparece justo en esa línea. Casi siempre es el token (caducado o copiado mal).
- **La verificación del webhook falla**: comprueba que `/health` abre con HTTPS y que el token de verificación coincide exactamente.
