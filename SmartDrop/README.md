# SmartDrop

Sistema de monitoreo y administración de agua compuesto por:

- Backend Django para el sitio web.
- API compartida para la aplicación Android.
- Supabase como almacenamiento remoto.
- MQTT para comunicación con dispositivos y válvulas.
- WebSockets para actualizaciones en tiempo real.

## Arquitectura

```text
Sitio web Django -----------+
                             |
Aplicación Android ----------+--> Backend Django --> Supabase
                             |          |
ESP32 / dispositivos --> MQTT           +--> WebSockets
                                        +--> Redis en producción
```

El sitio web y Android deben usar el mismo backend Django. Android no debe conectarse directamente a Supabase.

## Manuales

| Manual | Para quién | Dónde verlo |
|---|---|---|
| **Manual técnico** | Quien mantiene o amplía SmartDrop (equipo de desarrollo) | [`docs/MANUAL_TECNICO.md`](../docs/MANUAL_TECNICO.md) o en la web: menú **Ayuda → Manual técnico** (`/manual-tecnico/`, solo administradores) |
| Colección de Postman | Probar la API sin el panel (por ejemplo, abrir y cerrar la válvula) | [`docs/SmartDrop.postman_collection.json`](../docs/SmartDrop.postman_collection.json) o el botón **Colección de Postman** del manual técnico |
| Manual del administrador | Administradores | Web: **Ayuda → Manual del administrador** (`/manual-admin/`). App: menú lateral |
| Manual de usuario | Usuarios comunes | Web: **Cuenta → Manual de usuario** (`/manual/`). App: menú lateral |

### Manual técnico: qué trae

El manual técnico completo está en [`docs/MANUAL_TECNICO.md`](../docs/MANUAL_TECNICO.md). Sus partes:

1. [Arquitectura general](../docs/MANUAL_TECNICO.md#1-arquitectura-general): cómo se conectan la web, la app, el ESP32, MQTT y Supabase.
2. [Stack tecnológico](../docs/MANUAL_TECNICO.md#2-stack-tecnologico): lenguajes, librerías y servicios.
3. [Flujo de datos](../docs/MANUAL_TECNICO.md#3-flujo-de-datos): de los sensores al usuario, el comando de la electroválvula y Gotita.
4. [Modelo de datos](../docs/MANUAL_TECNICO.md#4-modelo-de-datos-diagrama-er): diagrama ER de Supabase y bases locales.
5. [API](../docs/MANUAL_TECNICO.md#5-api-documentation): rutas de cuenta, agua, lecturas, válvula, reportes, predicción y Gotita.
6. [Despliegue](../docs/MANUAL_TECNICO.md#6-deployment): dependencias, `.env`, migraciones, servidor y ESP32.
7. [Configuración del ESP32](../docs/MANUAL_TECNICO.md#7-configuracion-del-esp32).
8. [Solución de problemas](../docs/MANUAL_TECNICO.md#8-troubleshooting).
9. [Documentación del código fuente](../docs/MANUAL_TECNICO.md#9-documentacion-del-codigo-fuente).
10. [Seguridad](../docs/MANUAL_TECNICO.md#10-seguridad).
11. [Pruebas](../docs/MANUAL_TECNICO.md#11-pruebas).

Si cambias algo de la arquitectura, la API o el ESP32, actualiza también `docs/MANUAL_TECNICO.md`: la página
`/manual-tecnico/` lo lee directo de ese archivo, así que se actualiza sola.

## Guía rápida para el equipo

Pasos para que el proyecto funcione en tu laptop. **Las llaves nunca van en este repositorio**: Eliseo las pasa por
mensaje privado y cada quien las pega en su propio `.env`, que Git ignora.

### Ya lo tenía instalado (solo ponerse al día)

En la carpeta raíz del repositorio (donde está `requirements.txt`):

```powershell
git checkout main
git pull
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd SmartDrop
python manage.py migrate
python manage.py migrate --database=timeseries
python manage.py runserver
```

Abre `http://localhost:8000` y recarga con `Ctrl + F5`. Los dos `migrate` se pueden repetir sin problema; si
falta el segundo, la página *Predicción de suministro* falla con `no such column`.

### Primera vez

1. Clona el repositorio y crea el entorno virtual (ver [Instalación del backend](#instalación-del-backend)).
2. Copia la plantilla de variables **dentro de la carpeta `SmartDrop`** (donde está `manage.py`):

   ```powershell
   cd SmartDrop
   Copy-Item .env.example .env
   ```

3. Completa el `.env` (ver [Variables de entorno](#variables-de-entorno) y [Gotita](#gotita-la-asistente-con-ia)).
4. Crea las tablas y levanta la página:

   ```powershell
   python manage.py migrate
   python manage.py migrate --database=timeseries
   python manage.py runserver
   ```

5. Entra a `http://localhost:8000` con tu cuenta de Supabase. La primera vez puede tardar 1 o 2 minutos mientras
   copia las lecturas.

### Qué NO encender en tu laptop

| Variable | En tu `.env` | Por qué |
|---|---|---|
| `MQTT_INGESTA_ENABLED` | vacío o `0` | Solo una computadora recibe las lecturas del ESP32; si dos la encienden, cada lectura se guarda dos veces. |
| `VALVE_WATCHDOG_ENABLED` | `0` | Con el Supabase y el MQTT reales, cerraría válvulas de verdad. Solo el servidor lo enciende. |
| `DEBUG` | `True` solo en tu laptop | En el servidor siempre `False`. |

### Llaves: dónde va cada una

| Llave | Dónde va | Nunca en |
|---|---|---|
| Supabase, MQTT, Django, Anthropic (Gotita), ElevenLabs (voz de Gotita) | `SmartDrop/.env` de tu laptop o del servidor | Git, la app Android, capturas, chats de grupo |
| WiFi y MQTT del ESP32 | El firmware (`.ino`) que se sube a la placa | Este repositorio |
| Firma del APK release | `keystore.properties` del proyecto Android | Git |

La app Android **no lleva ninguna llave**: solo la dirección del servidor (ver el README del repositorio de la app).
Si una llave se comparte por error, avisa a Eliseo para cambiarla.

## Requisitos

### Backend

- Windows, macOS o Linux.
- Python 3.11 o superior.
- Git.
- Acceso a Supabase.
- Acceso al broker MQTT.
- Redis solo para producción o múltiples instancias del backend.

### Android

- Android Studio.
- JDK 17 o superior.
- Android SDK 35.
- Un dispositivo Android o un emulador.

## Instalación del backend

Clona o copia este proyecto y entra a su raíz:

```bash
git clone <URL_DEL_REPOSITORIO>
cd SmartDrop
```

Crea el entorno virtual:

### Windows PowerShell

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.venv\Scripts\Activate.ps1
```

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Instala las dependencias:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Variables de entorno

El `.env` va en la carpeta `SmartDrop`, junto a `manage.py` y `.env.example`. Copia la plantilla desde esa carpeta:

```powershell
Copy-Item .env.example .env
```

En Linux/macOS:

```bash
cp .env.example .env
```

Completa `.env` con valores propios:

```env
DJANGO_SECRET_KEY=<CLAVE_DJANGO_NUEVA>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

SUPABASE_URL=https://<PROYECTO>.supabase.co
SUPABASE_KEY=<CLAVE_PRIVADA_DEL_SERVIDOR>

MQTT_BROKER_HOST=<HOST_DEL_BROKER>
MQTT_BROKER_PORT=8883
MQTT_USERNAME=<USUARIO_MQTT>
MQTT_PASSWORD=<PASSWORD_MQTT>
MQTT_CA_CERT=

# Vacío en desarrollo. Obligatorio en producción con varios workers.
REDIS_URL=
```

No subas `.env` al repositorio. La clave de Supabase debe existir únicamente en el backend, nunca dentro de Android.

Genera una clave Django nueva con:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Gotita, la asistente con IA

Gotita es opcional: sin estas líneas la página funciona igual, solo que sin la gotita de la esquina. Para
activarla, agrega al final de `SmartDrop/.env` (los valores entre `< >` los pasa Eliseo en privado):

```env
ANTHROPIC_API_KEY=<llave de Anthropic>
ANTHROPIC_MODEL=claude-sonnet-5-5
ASISTENTE_ACTIVO=1

ELEVENLABS_API_KEY=<llave de ElevenLabs>
ELEVENLABS_VOICE_ID=<id de la voz>
ELEVENLABS_MODEL=eleven_flash_v2_5
ELEVENLABS_STT_MODEL=scribe_v2
```

- Las llaves de Anthropic y ElevenLabs viven **solo en el `.env` del backend**: nunca en la web, la app, Git ni capturas.
- Sin las líneas de ElevenLabs, Gotita responde igual pero con la voz y el dictado del navegador.
- Abre la página con `http://localhost:8000`, no con la IP: con la IP el navegador bloquea el micrófono.
- Los límites por usuario al día (`ASISTENTE_LIMITE_DIARIO`, `ASISTENTE_VOZ_POR_DIA`, `ASISTENTE_DICTADO_POR_DIA`)
  son opcionales y cuidan el saldo de las cuentas.

## Firmware del ESP32

El firmware (`.ino`) no está en este repositorio porque lleva el WiFi y las claves de MQTT. Quien lo suba a la
placa lo pide a Eliseo en privado y solo cambia el nombre y la clave del WiFi si la maqueta se usa en otra red.
El ESP32 se conecta al broker MQTT por internet, así que funciona desde cualquier red con WiFi.

## Verificación del backend

Ejecuta:

```bash
python manage.py check
```

Debe mostrar:

```text
System check identified no issues
```

Si la base local necesita migraciones:

```bash
python manage.py migrate
```

## Ejecutar el backend en desarrollo

Daphne sirve HTTP y WebSockets:

```bash
python -m daphne -b 0.0.0.0 -p 8000 SmartDrop.asgi:application
```

También puede usarse el ejecutable instalado:

```bash
daphne -b 0.0.0.0 -p 8000 SmartDrop.asgi:application
```

Sitio web:

```text
http://127.0.0.1:8000/
```

WebSocket:

```text
ws://127.0.0.1:8000/ws/sensors/
```

## Archivos estáticos

Si el sitio aparece sin estilos, verifica:

```bash
python manage.py findstatic App/css/smartdrop.css
python manage.py findstatic App/js/sensor_realtime.js
```

Prueba también en el navegador:

```text
http://127.0.0.1:8000/static/App/css/smartdrop.css
```

La respuesta debe ser HTTP `200` y tener tipo `text/css`.

## Aplicación Android

Abre en Android Studio la carpeta raíz del proyecto Android, no solamente `app`:

```text
<CARPETA_DEL_PROYECTO_ANDROID>/SmartDrop-App
```

Configura Gradle para usar JDK 17 o superior:

```text
Settings > Build, Execution, Deployment > Build Tools > Gradle > Gradle JDK
```

Después ejecuta:

```text
File > Sync Project with Gradle Files
```

El cliente Android utiliza Retrofit para HTTP y OkHttp para WebSockets.

## Conexión Android en desarrollo

### Emulador Android Studio

En `gradle.properties` del proyecto Android:

```properties
smartdropApiHost=10.0.2.2
```

`10.0.2.2` es la forma en que el emulador llega a tu computadora. La app arma sola
`http://10.0.2.2:8000/` y `ws://10.0.2.2:8000/ws/sensors/`.

### Teléfono físico por Wi-Fi

Conecta la computadora y el teléfono a la misma red. Obtén la IP local de la computadora:

Windows:

```powershell
ipconfig
```

Linux:

```bash
ip addr
```

macOS:

```bash
ifconfig
```

Configura Android con la IP encontrada en `gradle.properties` del proyecto Android (sin `http://` ni puerto; la
app agrega `:8000` en debug):

```properties
smartdropApiHost=<IP_DE_LA_PC>
```

Agrega también esa IP a `ALLOWED_HOSTS` del `.env` del backend. La IP puede cambiar según la red; no subas tu IP
a Git.

Permite el puerto 8000 en el firewall si es necesario:

```powershell
New-NetFirewallRule `
  -DisplayName "SmartDrop Django 8000" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 8000 `
  -Action Allow
```

Prueba desde el navegador del teléfono:

```text
http://<IP_DE_LA_PC>:8000/
```

### Teléfono físico por USB

Conecta el teléfono con depuración USB habilitada y usa:

```powershell
adb devices
adb reverse tcp:8000 tcp:8000
```

En `gradle.properties` del proyecto Android usa:

```properties
smartdropApiHost=127.0.0.1
```

El túnel USB debe recrearse después de desconectar el dispositivo.

## Compilar Android

Desde la raíz del proyecto Android:

```powershell
.\gradlew.bat `
  -Dorg.gradle.java.home="<RUTA_AL_JDK_17>" `
  :app:assembleDebug
```

El APK debug normalmente se genera en:

```text
app/build/outputs/apk/debug/app-debug.apk
```

En Android Studio también puede ejecutarse con:

```text
Build > Rebuild Project
Run
```

## Autenticación

La web utiliza sesiones Django mediante cookies.

Android utiliza JWT:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Ambos sistemas autentican contra el mismo backend y los usuarios provienen de la tabla `usuario` de Supabase.

Los permisos se aplican en el backend:

- Usuario normal: solo sus viviendas y sensores.
- Administrador: sensores y funciones administrativas autorizadas.
- Android no debe confiar únicamente en filtros del cliente.

## Preferencias del perfil e idioma

"Mi perfil" (web) y "Mi perfil" / "Configuración" (app) comparten las mismas preferencias, guardadas en el
backend (`PreferenciasUsuario`, por `id_usuario` de Supabase): un cambio en un lado se ve en el otro.

| Preferencia | Efecto |
|---|---|
| Alertas de nivel, Suministro, Calidad, Consumo elevado | Qué alertas se muestran en web y app y cuáles llegan como notificación push al teléfono |
| Modo oscuro | Tema de la web y de la app |
| Idioma español | Apagado = interfaz en inglés (web y app) |
| Reportes semanales | Cada 7 días registra un resumen del consumo (aparece en el perfil, el historial y como aviso en la app) |

Endpoints: `GET/POST /usuario/preferencias/` (web), `GET/PATCH auth/perfil/`, `GET/PATCH auth/preferencias/` y
`GET api/notificaciones/` (app, JWT).

La traducción usa un solo catálogo español→inglés, `App/i18n/en.json` (frases exactas y plantillas con huecos
`{0}`, `{1}`…), que sirve el backend a la web (`/i18n/en.js`) y a la app (`/api/i18n/en/`); la app trae además una
copia en `assets/i18n_en.json`. Al agregar textos nuevos a la interfaz, añade su traducción al catálogo y ejecuta:

```bash
python manage.py i18n_catalogo --app <ruta>/SmartDrop-App
```

El comando valida el catálogo (huecos iguales en ambos idiomas, sin traducciones encadenadas) y lo copia a la app.

## Tiempo real

El flujo de lecturas es:

```text
ESP32 -> API/MQTT -> Django -> Supabase
                         |
                         +-> Redis/Channels -> WebSocket -> Web y Android
```

El WebSocket del navegador debe mostrar:

```text
/ws/sensors/    101 Switching Protocols
```

El evento de lectura tiene esta forma general:

```json
{
  "event": "sensor_reading",
  "id_sensor": 1,
  "valor": 25.7,
  "fecha_registro": "2026-01-01T12:00:00Z"
}
```

## Prueba de una lectura

La ruta de ingestión acepta:

```http
POST /api/lecturas/
Content-Type: application/json
X-Api-Key: <LECTURAS_API_KEY del .env>
```

Sin la cabecera `X-Api-Key` correcta la lectura se rechaza con 401 (con `DEBUG=True` y sin
`LECTURAS_API_KEY` en el `.env` se aceptan, solo para desarrollo). El firmware del ESP32 debe enviar la
misma clave; `deploy/actualizar.sh` la genera en el servidor si falta.

Ejemplo con PowerShell:

```powershell
$body = @{
  id_sensor = 1
  fecha_registro = (Get-Date).ToUniversalTime().ToString("o")
  valor = 25.7
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/lecturas/" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Api-Key" = $env:LECTURAS_API_KEY } `
  -Body $body
```

La lectura debe guardarse en Supabase y actualizar las interfaces conectadas.

## Producción

En producción no uses:

```text
<HOST_LOCAL>
<HOST_DEL_EMULADOR>
IPs privadas de desarrollo
DEBUG=True
InMemoryChannelLayer
```

Usa un dominio público:

```text
Web: https://<DOMINIO>
API: https://<DOMINIO>/api/
WebSocket: wss://<DOMINIO>/ws/sensors/
```

Configuración recomendada:

```env
DEBUG=False
ALLOWED_HOSTS=<DOMINIO>
REDIS_URL=redis://<SERVIDOR_REDIS>:6379/0
```

Arquitectura recomendada:

```text
Internet
   |
HTTPS reverse proxy / load balancer
   |
Daphne/Uvicorn con varios workers
   |
Redis Channel Layer
   |
Supabase + MQTT
```

Redis es necesario cuando hay varios procesos o servidores, porque permite distribuir eventos WebSocket entre ellos.

## Seguridad

Antes de publicar:

- Genera una nueva `DJANGO_SECRET_KEY`.
- Regenera cualquier clave de Supabase o MQTT que haya sido compartida.
- No subas `.env` a Git.
- No pongas la clave privada de Supabase en Android.
- Usa HTTPS y WSS.
- Desactiva `DEBUG`.
- Configura backups de Supabase.
- Configura políticas RLS en Supabase.
- Revisa permisos por vivienda y sensor.
- Usa expiración y renovación de JWT.
- Limita las consultas históricas de gráficas.
- Configura logs y monitoreo.

## Solución de problemas

### El sitio aparece sin CSS

```bash
python manage.py findstatic App/css/smartdrop.css
```

Reinicia Daphne y recarga con `Ctrl+Shift+R`.

### Android no conecta

Comprueba:

- Daphne está ejecutándose en `0.0.0.0:8000`.
- El teléfono y la PC están en la misma red Wi-Fi.
- La URL Android usa la IP actual de la PC.
- El firewall permite TCP 8000.
- No se usa `localhost` desde un teléfono físico.
- Para USB se ejecutó `adb reverse tcp:8000 tcp:8000`.

### Error de credenciales

Revisa en la consola del backend si aparece:

```text
correo no encontrado
contraseña incorrecta
usuario inactivo
```

No registres contraseñas en logs.

### WebSocket no conecta

Comprueba:

- Daphne, no solo `runserver`, está ejecutándose.
- La ruta `/ws/sensors/` está disponible.
- El token JWT no expiró.
- En producción existe Redis.
- En el navegador aparece `101 Switching Protocols`.

## Pruebas

Backend:

```bash
python manage.py check
python manage.py test App
```

Android:

```powershell
.\gradlew.bat `
  -Dorg.gradle.java.home="<RUTA_AL_JDK_17>" `
  :app:compileDebugJavaWithJavac
```

Antes de publicar, prueba:

1. Login web.
2. Login Android.
3. Usuario con una vivienda.
4. Usuario sin vivienda.
5. Administrador.
6. Lectura nueva de un sensor.
7. Reconexión WebSocket.
8. Token JWT expirado.
9. Supabase temporalmente no disponible.
10. MQTT temporalmente no disponible.
11. Acceso de un usuario a otra vivienda.
12. Varios usuarios conectados simultáneamente.
