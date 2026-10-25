#!/usr/bin/env bash
# Funciones que comparten instalar_servidor.sh y actualizar.sh para dejar el .env y los datos del servidor
# en un estado seguro. Se cargan con `source`; esperan ENV_FILE, APP_DIR y VENV definidos.

set_env() {  # set_env CLAVE VALOR: reemplaza la línea o la agrega al final
    if grep -q "^$1=" "$ENV_FILE"; then
        sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    fi
}

valor_env() {  # valor_env CLAVE: el valor actual, sin comillas
    grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -E "s/^['\"]|['\"]$//g"
}

generar_clave() {
    "$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))'
}

asegurar_claves() {
    # La clave de ejemplo de Django quedó en el historial de Git: si el .env la usa (o una corta), se cambia.
    local clave
    clave="$(valor_env DJANGO_SECRET_KEY)"
    if [[ -z "$clave" || "$clave" == django-insecure-* || ${#clave} -lt 40 ]]; then
        set_env DJANGO_SECRET_KEY "$(generar_clave)"
        echo "Se generó una DJANGO_SECRET_KEY nueva: las sesiones abiertas en la web se cerrarán una vez."
    fi
    if [[ -z "$(valor_env JWT_SIGNING_KEY)" ]]; then
        set_env JWT_SIGNING_KEY "$(generar_clave)"
        echo "Se generó JWT_SIGNING_KEY: los usuarios de la app tendrán que iniciar sesión otra vez."
    fi
    if [[ -z "$(valor_env LECTURAS_API_KEY)" ]]; then
        set_env LECTURAS_API_KEY "$(generar_clave)"
        echo "Se generó LECTURAS_API_KEY. Configura la misma clave en el ESP32 (cabecera X-Api-Key):"
        echo "    grep LECTURAS_API_KEY $ENV_FILE"
    fi
    if [[ -z "$(valor_env RESPALDO_CLAVE)" ]]; then
        set_env RESPALDO_CLAVE "$("$VENV/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        echo "Se generó RESPALDO_CLAVE para cifrar los respaldos. Guarda una copia FUERA del servidor:"
        echo "sin ella no se puede restaurar.    grep RESPALDO_CLAVE $ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
}

ajustes_del_servidor() {
    # Solo el servidor vigila los temporizadores de la válvula (ver VALVE_WATCHDOG_ENABLED en settings.py).
    set_env VALVE_WATCHDOG_ENABLED 1
    # La ingesta MQTT vive en el servidor (DEF-17): ya no hace falta consumer.js en una laptop.
    set_env MQTT_INGESTA_ENABLED 1
}

instalar_respaldo_diario() {
    # Respaldo cifrado cada noche con systemd (PB064). Espera REPO_DIR definido.
    local usuario
    usuario="$(id -un)"
    for unidad in smartdrop-respaldo.service smartdrop-respaldo.timer; do
        sed -e "s|__USUARIO__|$usuario|g" -e "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/deploy/$unidad" | sudo tee "/etc/systemd/system/$unidad" >/dev/null
    done
    sudo systemctl daemon-reload
    sudo systemctl enable --now smartdrop-respaldo.timer
}

apartar_env_duplicado() {
    # settings.py carga también SmartDrop/.env y le da prioridad: uno olvidado ahí podría volver a encender
    # DEBUG o traer una clave vieja. En el servidor la configuración vive solo en el .env de la raíz.
    if [[ -f "$APP_DIR/.env" ]]; then
        local destino
        destino="$APP_DIR/.env.ignorado-$(date +%Y%m%d%H%M%S)"
        mv "$APP_DIR/.env" "$destino"
        chmod 600 "$destino"
        echo "AVISO: $APP_DIR/.env tenía prioridad sobre $ENV_FILE; se movió a $destino."
    fi
}

proteger_bases() {
    # Las bases SQLite guardan sesiones y hashes de contraseñas: solo el usuario del servicio las lee.
    shopt -s nullglob
    local bases=("$APP_DIR"/*.sqlite3)
    shopt -u nullglob
    if (( ${#bases[@]} )); then
        chmod 600 "${bases[@]}"
    fi
}
