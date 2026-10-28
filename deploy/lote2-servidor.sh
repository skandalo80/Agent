# Lote 2 de la auditoría (servidor): actualizaciones automáticas, CSP para Cloudflare Web Analytics,
# prueba del respaldo por correo y revisión de puertos abiertos. Correr DESPUÉS del git pull:
#   cd ~/ig-bot && bash deploy/lote2-servidor.sh
echo '== 1. Actualizaciones automáticas de seguridad =='
sudo apt-get install -y -qq unattended-upgrades >/dev/null && printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null && echo 'OK: activadas'
echo '== 2. Caddy: dejar cargar Cloudflare Web Analytics en skandar.online =='
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.antes-analitica
sudo python3 - <<'EOF'
import re, sys
RUTA = "/etc/caddy/Caddyfile"
t = open(RUTA).read()
m = re.search(r"\(cabeceras-web\)\s*\{.*?Content-Security-Policy \"([^\"]*)\"", t, re.S)
if not m:
    sys.exit("No encontré la CSP de (cabeceras-web), no cambié nada. Mándale este mensaje a Claude.")
csp = m.group(1)
if "cloudflareinsights" in csp:
    sys.exit("Ya estaba lista, no cambié nada.")
nueva = csp.replace("script-src 'self'", "script-src 'self' https://static.cloudflareinsights.com", 1)
if "connect-src" in nueva:
    nueva = re.sub(r"connect-src ([^;]*)", r"connect-src \1 https://cloudflareinsights.com", nueva, 1)
else:
    nueva = nueva.replace("style-src", "connect-src 'self' https://cloudflareinsights.com; style-src", 1)
if nueva == csp or "static.cloudflareinsights.com" not in nueva:
    sys.exit("La CSP no tiene la forma que esperaba, no cambié nada. Mándale este mensaje a Claude.")
open(RUTA, "w").write(t[:m.start(1)] + nueva + t[m.end(1):])
print("CSP actualizada")
EOF
sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 && sudo systemctl reload caddy && echo 'OK: Caddy recargado' || { sudo cp /etc/caddy/Caddyfile.antes-analitica /etc/caddy/Caddyfile; echo 'Algo falló: dejé Caddy como estaba.'; }
echo '== 3. Respaldo: prueba de envío por correo (luego va solo cada domingo) =='
cd ~/ig-bot && .venv/bin/python scripts/respaldo.py --correo
systemctl is-enabled igbot-respaldo.timer
echo '== 4. Puertos abiertos hacia fuera (solo para revisar) =='
sudo ss -tlnH | awk '{print $4}' | grep -v -E '^(127\.|\[::1\])' | sort -u
echo 'LISTO. Pégale a Claude todo lo de arriba.'
