#!/usr/bin/env bash
# Aplica al Nginx YA INSTALADO los cambios de seguridad de deploy/nginx-smartdrop.conf:
#   * rate limiting por IP (general y para login/registro),
#   * X-Forwarded-For sobrescrito con la IP real (para que nadie pueda falsificarla).
# No vuelve a generar el archivo (eso borraría el bloque HTTPS que agregó certbot): lo edita en su lugar,
# guarda una copia y, si `nginx -t` falla, la restaura.
# Uso (en el servidor):  bash ~/SmartDrop/deploy/aplicar_seguridad_nginx.sh
set -euo pipefail

SITIO=/etc/nginx/sites-available/smartdrop
COPIA="$SITIO.respaldo-$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$SITIO" ]; then
    echo "No existe $SITIO. ¿Corriste instalar_servidor.sh?" >&2
    exit 1
fi

sudo cp "$SITIO" "$COPIA"
echo "Copia de seguridad: $COPIA"

if ! grep -q 'zone=sd_general' "$SITIO"; then
    TMP=$(mktemp)
    cat > "$TMP" <<'ZONAS'
# ── Rate limiting por IP (primera barrera; Django aplica límites más finos por usuario y por cuenta) ──
limit_req_zone $binary_remote_addr zone=sd_general:10m rate=20r/s;
map $request_uri $sd_auth_limit_key {
    default                       "";
    ~^/(login|register)/          $binary_remote_addr;
    ~^/api/(login|register)/      $binary_remote_addr;
    ~^/auth/(login|registro)/     $binary_remote_addr;
}
limit_req_zone $sd_auth_limit_key zone=sd_auth:10m rate=10r/m;
limit_req_status 429;
limit_req_log_level warn;

ZONAS
    # Las zonas van antes del primer bloque `map $http_upgrade` (contexto http).
    sudo awk -v zonas="$TMP" '
        /^map \$http_upgrade/ && !hecho { while ((getline linea < zonas) > 0) print linea; hecho=1 }
        { print }
    ' "$SITIO" | sudo tee "$SITIO.nuevo" >/dev/null
    sudo mv "$SITIO.nuevo" "$SITIO"
    rm -f "$TMP"
    # Los límites se aplican dentro de `location / {` (una sola vez por bloque).
    sudo sed -i -E '/^\s*location \/ \{/a\        limit_req zone=sd_general burst=40 nodelay;\n        limit_req zone=sd_auth burst=10 nodelay;' "$SITIO"
fi

sudo sed -i 's/proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;/proxy_set_header X-Forwarded-For $remote_addr;/' "$SITIO"

if sudo nginx -t; then
    sudo systemctl reload nginx
    echo "Listo: rate limiting de Nginx activo."
else
    echo "nginx -t falló: se restaura la copia." >&2
    sudo cp "$COPIA" "$SITIO"
    sudo nginx -t && sudo systemctl reload nginx
    exit 1
fi
