#!/usr/bin/env bash
# Publica en el servidor lo último que subiste a GitHub (git push).
# Uso (en el servidor):  bash ~/SmartDrop/deploy/actualizar.sh
# No toca .env, las bases SQLite, ml_models ni media: los datos del servidor se conservan.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_DIR/SmartDrop"
VENV="$REPO_DIR/.venv"
export PATH="$HOME/.local/bin:$PATH"

cd "$REPO_DIR"
git pull --ff-only
uv pip install --python "$VENV/bin/python" -r requirements.txt

cd "$APP_DIR"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py migrate --database=timeseries --noinput
"$VENV/bin/python" manage.py collectstatic --noinput
chmod -R o+rX "$APP_DIR/staticfiles"

sudo systemctl restart smartdrop
sleep 3
systemctl --no-pager --lines=5 status smartdrop
