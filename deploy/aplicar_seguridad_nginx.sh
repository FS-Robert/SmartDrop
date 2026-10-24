#!/usr/bin/env bash
# Aplica al Nginx YA INSTALADO los cambios de seguridad de deploy/nginx-smartdrop.conf sin volver a generar el
# archivo (eso borraría el bloque HTTPS que agregó certbot): lo edita en su lugar, guarda una copia y, si
# `nginx -t` falla, la restaura. Se puede ejecutar varias veces: solo agrega lo que falta.
#   * rate limiting por IP (general y para login/registro), con limit_req_status una sola vez,
#   * X-Forwarded-For sobrescrito con la IP real (para que nadie pueda falsificarla),
#   * /media/ cerrado y /protegido/ interno (las evidencias se entregan con enlace firmado),
#   * server_tokens off.
# Uso (en el servidor):  bash ~/SmartDrop/deploy/aplicar_seguridad_nginx.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_DIR/SmartDrop"
SITIO="${SITIO:-/etc/nginx/sites-available/smartdrop}"
SUDO="${SUDO-sudo}"                       # SUDO= (vacío) para probar como root
NGINX_TEST="${NGINX_TEST:-nginx -t}"
COPIA="$SITIO.respaldo-$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$SITIO" ]; then
    echo "No existe $SITIO. ¿Corriste instalar_servidor.sh?" >&2
    exit 1
fi

$SUDO cp "$SITIO" "$COPIA"
echo "Copia de seguridad: $COPIA"

NUEVO="$(mktemp)"
trap 'rm -f "$NUEVO"' EXIT
python3 - "$SITIO" "$APP_DIR" > "$NUEVO" <<'PY'
import re
import sys

ruta, app_dir = sys.argv[1], sys.argv[2]
s = open(ruta, encoding='utf-8').read()

# 1. Zonas y opciones de limit_req (contexto http): solo las que falten, y limit_req_status una sola vez.
faltan = []
if 'zone=smartdrop_ip' not in s or not re.search(r'limit_req_zone\s+\S+\s+zone=smartdrop_ip', s):
    faltan.append('limit_req_zone $binary_remote_addr zone=smartdrop_ip:10m rate=50r/s;')
if '$sd_auth_limit_key' not in s:
    faltan += [
        'map $request_uri $sd_auth_limit_key {',
        '    default                       "";',
        '    ~^/(login|register)/          $binary_remote_addr;',
        '    ~^/api/(login|register)/      $binary_remote_addr;',
        '    ~^/auth/(login|registro)/     $binary_remote_addr;',
        '}',
    ]
if not re.search(r'limit_req_zone\s+\$sd_auth_limit_key', s):
    faltan.append('limit_req_zone $sd_auth_limit_key zone=sd_auth:10m rate=30r/m;')
directivas = ('limit_req_status 429;', 'limit_req_log_level warn;', 'server_tokens off;')
for directiva in directivas:
    nombre = directiva.split()[0]
    s = re.sub(rf'^[ \t]*{nombre}\s[^;]*;[^\n]*\n', '', s, flags=re.M)  # quita repetidas (y la de cada server)
encabezado = '# ── Seguridad (deploy/aplicar_seguridad_nginx.sh) ──\n'
s = s.replace(encabezado, '')
bloque = ('\n'.join(faltan) + '\n\n' if faltan else '') + encabezado + '\n'.join(directivas) + '\n\n'
ancla = re.search(r'^(map \$http_upgrade|upstream )', s, flags=re.M)
s = s[:ancla.start()] + bloque + s[ancla.start():] if ancla else bloque + s

# 2. /media/ cerrado y /protegido/ interno.
s = re.sub(r'location /media/ \{[^}]*\}', 'location /media/ {\n        return 404;\n    }', s)
if 'location /protegido/' not in s:
    protegido = (
        '    location /protegido/ {\n'
        '        internal;\n'
        f'        alias {app_dir}/media/;\n'
        '        add_header X-Content-Type-Options nosniff;\n'
        '    }\n\n'
    )
    s = re.sub(r'(\n[ \t]*location / \{)', '\n' + protegido.rstrip('\n') + r'\n\1', s, count=1)

# 3. Límites dentro de cada `location / {` y X-Forwarded-For con la IP real.
def limites(m):
    cuerpo = m.group(0)
    cuerpo = re.sub(r'\n[ \t]*limit_req zone=sd_general[^;]*;', '', cuerpo)
    for linea in ('limit_req zone=smartdrop_ip burst=100 nodelay;', 'limit_req zone=sd_auth burst=20 nodelay;'):
        zona = linea.split()[1]
        if zona not in cuerpo:
            cuerpo = cuerpo.replace('{', '{\n        ' + linea, 1)
    return cuerpo

s = re.sub(r'location / \{[^}]*\}', limites, s)
s = s.replace('proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
              'proxy_set_header X-Forwarded-For $remote_addr;')
sys.stdout.write(re.sub(r'\n{3,}', '\n\n', s))
PY

$SUDO cp "$NUEVO" "$SITIO"
if $SUDO $NGINX_TEST; then
    $SUDO systemctl reload nginx 2>/dev/null || $SUDO nginx -s reload 2>/dev/null || true
    echo "Listo: seguridad de Nginx aplicada."
else
    echo "nginx -t falló: se restaura la copia." >&2
    $SUDO cp "$COPIA" "$SITIO"
    exit 1
fi
