#!/usr/bin/env bash
# Publica en el servidor lo último que subiste a GitHub (git push).
# Uso (en el servidor):  bash ~/SmartDrop/deploy/actualizar.sh
# Conserva las bases SQLite, ml_models y media. Del .env solo agrega las claves de seguridad que falten.
# Los cambios de deploy/nginx-smartdrop.conf y deploy/smartdrop.service se aplican volviendo a ejecutar
# instalar_servidor.sh (se puede repetir sin romper nada).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_DIR/SmartDrop"
VENV="$REPO_DIR/.venv"
ENV_FILE="$REPO_DIR/.env"
export PATH="$HOME/.local/bin:$PATH"

cd "$REPO_DIR"
git pull --ff-only
uv pip install --python "$VENV/bin/python" -r requirements.txt

source "$REPO_DIR/deploy/seguridad_env.sh"
asegurar_claves
ajustes_del_servidor
apartar_env_duplicado

cd "$APP_DIR"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py migrate --database=timeseries --noinput
"$VENV/bin/python" manage.py collectstatic --noinput
# Conversaciones con Gotita de más de 20 días: se borran de verdad (antes solo al abrir el historial).
"$VENV/bin/python" manage.py limpiar_gotita
chmod -R o+rX "$APP_DIR/staticfiles"
proteger_bases

sudo systemctl restart smartdrop
instalar_respaldo_diario
sleep 3
systemctl --no-pager --lines=5 status smartdrop
