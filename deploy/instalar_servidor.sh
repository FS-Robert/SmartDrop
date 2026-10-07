#!/usr/bin/env bash
# Instala SmartDrop en un servidor Ubuntu (Oracle Cloud Always Free, ARM).
# Uso (en el servidor):  bash ~/SmartDrop/deploy/instalar_servidor.sh <dominio> [correo]
# Se puede volver a ejecutar sin romper nada: cada paso revisa si ya está hecho.
set -euo pipefail

DOMINIO="${1:?Falta el dominio. Ejemplo: bash instalar_servidor.sh smartdrop-ugb.duckdns.org tu@correo.com}"
CORREO="${2:-}"                           # opcional: Let's Encrypt avisa ahí si el certificado no se renueva

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_DIR/SmartDrop"            # donde está manage.py
VENV="$REPO_DIR/.venv"
ENV_FILE="$REPO_DIR/.env"
USUARIO="$(id -un)"
PY_VERSION="3.13"                         # la misma que usas en tu PC

paso() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [[ ! -f "$ENV_FILE" ]]; then
    echo "No existe $ENV_FILE. Súbelo primero con deploy/desplegar.ps1 (o copia tu .env)." >&2
    exit 1
fi

paso "Paquetes del sistema (nginx, certbot)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx git sqlite3 curl iptables-persistent libgomp1  # libgomp1: OpenMP que exige LightGBM

paso "Abrir puertos 80 y 443 en el firewall interno de Ubuntu (Oracle lo trae cerrado)"
for puerto in 80 443; do
    if ! sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$puerto" -j ACCEPT 2>/dev/null; then
        sudo iptables -I INPUT 1 -p tcp -m state --state NEW --dport "$puerto" -j ACCEPT
    fi
done
sudo netfilter-persistent save

paso "Python $PY_VERSION con uv"
if ! command -v uv >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/uv" ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install "$PY_VERSION"
[[ -d "$VENV" ]] || uv venv --python "$PY_VERSION" "$VENV"
uv pip install --python "$VENV/bin/python" -r "$REPO_DIR/requirements.txt"

paso "Ajustar .env para producción"
set_env() {  # set_env CLAVE VALOR: reemplaza la línea o la agrega al final
    if grep -q "^$1=" "$ENV_FILE"; then
        sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    fi
}
sed -i 's/\r$//' "$ENV_FILE"              # quitar finales de línea de Windows
set_env DEBUG False
set_env ALLOWED_HOSTS "$DOMINIO,localhost,127.0.0.1"
set_env CSRF_TRUSTED_ORIGINS "https://$DOMINIO"
set_env REDIS_URL ""                      # un solo proceso: el canal en memoria basta
chmod 600 "$ENV_FILE"

paso "Migraciones y archivos estáticos"
cd "$APP_DIR"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py migrate --database=timeseries --noinput
"$VENV/bin/python" manage.py collectstatic --noinput
mkdir -p "$APP_DIR/media"
# Nginx (usuario www-data) necesita poder entrar a las carpetas para servir static/ y media/.
chmod o+x "$HOME" "$REPO_DIR" "$APP_DIR"
chmod -R o+rX "$APP_DIR/staticfiles" "$APP_DIR/media"

paso "Servicio systemd (arranca solo y se reinicia si falla)"
sed -e "s|__USUARIO__|$USUARIO|g" -e "s|__REPO_DIR__|$REPO_DIR|g" \
    "$REPO_DIR/deploy/smartdrop.service" | sudo tee /etc/systemd/system/smartdrop.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable smartdrop
sudo systemctl restart smartdrop

paso "Nginx"
sed -e "s|__DOMINIO__|$DOMINIO|g" -e "s|__APP_DIR__|$APP_DIR|g" \
    "$REPO_DIR/deploy/nginx-smartdrop.conf" | sudo tee /etc/nginx/sites-available/smartdrop >/dev/null
sudo ln -sf /etc/nginx/sites-available/smartdrop /etc/nginx/sites-enabled/smartdrop
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

paso "Certificado HTTPS gratis (Let's Encrypt)"
IP_PUBLICA="$(curl -s https://api.ipify.org || true)"
IP_DOMINIO="$(getent ahostsv4 "$DOMINIO" | awk 'NR==1{print $1}' || true)"
if [[ -n "$IP_PUBLICA" && "$IP_PUBLICA" != "$IP_DOMINIO" ]]; then
    echo "AVISO: $DOMINIO apunta a '${IP_DOMINIO:-nada}' pero este servidor es $IP_PUBLICA."
    echo "Corrige la IP en DuckDNS (o en tu DNS), espera 1-2 minutos y vuelve a ejecutar este script."
    exit 1
fi
if [[ -n "$CORREO" ]]; then CUENTA=(-m "$CORREO" --no-eff-email); else CUENTA=(--register-unsafely-without-email); fi
sudo certbot --nginx -d "$DOMINIO" "${CUENTA[@]}" --agree-tos --redirect --non-interactive
# HTTP/2: varias peticiones en una sola conexión (carga más rápida de CSS/JS/imágenes).
sudo sed -i -E 's/listen (\S*443) ssl( ipv6only=on)?;/listen \1 ssl http2\2;/' /etc/nginx/sites-available/smartdrop
sudo nginx -t
sudo systemctl reload nginx

paso "Comprobación"
sleep 3
systemctl --no-pager --lines=5 status smartdrop || true
curl -s -o /dev/null -w "https://$DOMINIO/login/ -> HTTP %{http_code}\n" "https://$DOMINIO/login/" || true
printf '\n\033[1;32mListo: https://%s\033[0m\n' "$DOMINIO"
echo "Logs en vivo:  sudo journalctl -u smartdrop -f"
