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

Copia la plantilla:

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
python manage.py findstatic App/css/dashboard.css
python manage.py findstatic App/js/sensor_realtime.js
```

Prueba también en el navegador:

```text
http://127.0.0.1:8000/static/App/css/dashboard.css
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

Usa:

```text
http://<HOST_DEL_EMULADOR>:8000/
ws://<HOST_DEL_EMULADOR>:8000/ws/sensors/
```

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

Configura Android con la IP encontrada:

```java
private static final String BASE_URL =
        "http://<IP_DE_LA_PC>:8000/";

public static String getRealtimeUrl() {
    return "ws://<IP_DE_LA_PC>:8000/ws/sensors/";
}
```

La IP puede cambiar según la red. No la guardes como valor universal dentro de la aplicación.

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

En Android usa:

```text
http://127.0.0.1:8000/
ws://127.0.0.1:8000/ws/sensors/
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
```

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
python manage.py findstatic App/css/dashboard.css
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
