# SmartDrop

SmartDrop es una aplicación web de monitoreo de agua construida con Django. Muestra presión, calidad del agua, nivel de tanque, consumo, recomendaciones y estado del sistema. Los datos operativos y la autenticación se integran con Supabase mediante su API REST.

## Funcionalidades

- Dashboard de presión, calidad, tanque y consumo.
- Vistas de presión, calidad TDS, tanque, consumo, retroalimentación y recomendaciones.
- Perfil de usuario y sesiones de Django.
- Panel administrativo con lecturas, viviendas y sensores.
- Control de electroválvulas mediante MQTT y registro de acciones.
- API JSON para registro, login, lecturas IoT y estado de válvula.
- Búsqueda global en `/buscar/?q=...`.
  - Los usuarios comunes buscan secciones y contenido de la interfaz.
  - Los administradores también buscan usuarios, alertas y logs en Supabase.
  - El término se conserva mientras se navega y puede limpiarse con el botón X.

## Requisitos

- Python 3.10 o superior.
- Git y pip.
- Un proyecto de Supabase para los datos remotos.
- Un broker MQTT y sus credenciales si se utilizará el control de válvulas.

## Estructura

El repositorio contiene un directorio de proyecto Django anidado:

```text
SmartDrop/
├── README.md
├── requirements.txt
├── .env.example
└── SmartDrop/
    ├── manage.py
    ├── db.sqlite3                  # se genera localmente; no se versiona
    ├── App/
    │   ├── views.py
    │   ├── forms.py
    │   ├── models.py
    │   ├── backends.py
    │   ├── supabase_client.py
    │   ├── mqtt_service.py
    │   ├── Templates/App/
    │   ├── Static/App/
    │   └── templatetags/
    ├── SmartDrop/settings.py
    └── supabase/schema.sql
```

Los comandos Django deben ejecutarse desde `SmartDrop/`, la carpeta que contiene `manage.py`.

## Instalación local

Desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

En macOS o Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

También es posible instalar desde la carpeta Django:

```powershell
cd SmartDrop
pip install -r requirements.txt
```

Los dos archivos contienen la misma lista de dependencias directas para que la instalación funcione desde la raíz o desde la carpeta Django. Si agregas una dependencia, actualiza ambos archivos.

## Variables de entorno

Copia el ejemplo junto a `manage.py`:

```powershell
copy .env.example SmartDrop\.env
```

Configura `SmartDrop/.env`:

| Variable | Uso | Obligatoria |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Clave secreta de Django. | Sí |
| `DEBUG` | `True` para desarrollo local. | No, por defecto `True` |
| `ALLOWED_HOSTS` | Hosts separados por comas. | No |
| `SUPABASE_URL` | URL del proyecto Supabase. | Para login, registro y datos remotos |
| `SUPABASE_KEY` | Clave secreta de servidor para la API REST. | Para login, registro y datos remotos |
| `MQTT_BROKER_HOST` | Host del broker MQTT. | Solo para válvulas |
| `MQTT_BROKER_PORT` | Puerto MQTT/TLS. | No, por defecto `8883` |
| `MQTT_USERNAME` | Usuario MQTT. | Solo para válvulas |
| `MQTT_PASSWORD` | Contraseña MQTT. | Solo para válvulas |
| `MQTT_CA_CERT` | Ruta al certificado CA, si aplica. | No |

Genera una clave para Django con:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

No subas `.env` ni claves `SUPABASE_KEY` o MQTT a Git. `.env.example` no debe contener secretos reales.

## Supabase

1. Crea o selecciona un proyecto en Supabase.
2. Coloca la URL y la clave secreta de servidor en `SmartDrop/.env`.
3. Ejecuta `SmartDrop/supabase/schema.sql` desde el SQL Editor.

El archivo SQL es un esquema mínimo para roles, usuarios, viviendas, consumo y retroalimentación. Las vistas también consultan tablas operativas como `sensor`, `lectura`, `tanque`, `alerta`, `notificacion`, `valvula` y `log_valvula`; esas tablas deben existir en el esquema Supabase utilizado por el despliegue.

La clave de servidor debe permanecer únicamente en Django. No la uses en la aplicación móvil ni en el navegador.

## Autenticación

- El usuario local es `App.Usuario`, pero la fuente de verdad de credenciales es la tabla `usuario` de Supabase.
- El registro web y `/api/register/` generan hashes compatibles con la app móvil y Node.js: BCrypt `$2b$` con 12 rondas.
- El login usa `SupabaseAuthBackend` y valida `$2a$`/`$2b$` con Passlib BCrypt.
- Los usuarios antiguos con hashes Django PBKDF2 siguen funcionando mediante `django_check_password`.
- Django crea la sesión web después de que el backend valida la contraseña.

## Ejecutar

Desde la carpeta que contiene `manage.py`:

```powershell
cd SmartDrop
python manage.py migrate
python manage.py check
python manage.py runserver
```

Abre <http://127.0.0.1:8000/>.

## Rutas principales

| Ruta | Descripción |
| --- | --- |
| `/login/` | Login web |
| `/register/` | Registro web |
| `/buscar/?q=termino` | Búsqueda global |
| `/` | Dashboard |
| `/estado-sistema/` | Estado del sistema |
| `/presion/` | Presión del agua |
| `/calidad/` | Calidad y TDS |
| `/tanque/` | Nivel de tanque |
| `/consumo/` | Consumo |
| `/retroalimentacion/` | Retroalimentación e indicadores |
| `/recomendaciones/` | Recomendaciones de ahorro |
| `/usuario/` | Perfil |
| `/admin-panel/` | Panel administrativo |
| `/valvulas/` | Electroválvulas y logs |

## API

Todos estos endpoints requieren `POST` salvo que se indique lo contrario:

- `/api/register/`: recibe `email`, `nombre`, `apellido`, `password1` y `password2`.
- `/api/login/`: recibe `email` y `password`.
- `/api/lecturas/`: recibe exactamente `id_sensor`, `fecha_registro` ISO 8601 y `valor` numérico.
- `/api/valvula/estado/`: consulta el estado público de la válvula.

Las respuestas de registro y login devuelven un objeto `user`; el endpoint móvil no crea una sesión de navegador.

## Pruebas y mantenimiento

```powershell
cd SmartDrop
python manage.py check
python manage.py test App
```

Antes de desplegar, verifica que:

- `DEBUG=False` y `ALLOWED_HOSTS` estén configurados.
- Exista `DJANGO_SECRET_KEY` segura.
- Supabase tenga las tablas requeridas y políticas adecuadas.
- MQTT esté disponible si se habilita el control de válvulas.
- No se haya incluido `.env`, `db.sqlite3`, `__pycache__` ni archivos compilados en Git.

## Dependencias directas

El `requirements.txt` raíz contiene únicamente dependencias usadas directamente por el proyecto:

- Django para el servidor web.
- `requests` para la API REST de Supabase.
- `python-dotenv` para cargar `.env`.
- `paho-mqtt` para comandos de electroválvulas.
- `passlib` y `bcrypt` para hashes BCrypt compatibles con la app móvil.

Las dependencias transitivas de Django y Requests se instalan automáticamente y no necesitan estar repetidas en la lista del proyecto.
