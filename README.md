# SmartDrop

SmartDrop es una aplicación web en Django para monitorear tanque de agua, calidad, presión, consumo y recomendaciones. El **login** y el **registro** se conectan a **Supabase** (API REST): los usuarios se guardan y se consultan en la tabla `usuario`.

## Requisitos previos

- Python 3.10 o superior
- pip
- Git
- Una cuenta y un proyecto en [Supabase](https://supabase.com)

## Cómo clonar el repositorio

```powershell
git clone https://github.com/FS-Robert/SmartDrop.git
cd SmartDrop
```

## Estructura del proyecto

La carpeta del repositorio y la del proyecto Django se llaman igual (`SmartDrop`). No son copias duplicadas:

- **Raíz del repositorio:** `requirements.txt`, `.env.example`, `README.md`
- **Proyecto Django:** carpeta `SmartDrop/` (aquí está `manage.py`)
- **Configuración Django:** carpeta `SmartDrop/SmartDrop/` (`settings.py`, `urls.py`)

Los comandos de Django se ejecutan **dentro de la carpeta que contiene `manage.py`**.

## Crear y activar el entorno virtual (Windows)

Desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

Si `python` no funciona, prueba `py -3`.

En macOS o Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Instalar dependencias

Con el entorno virtual activado, desde la raíz del repositorio:

```powershell
pip install -r requirements.txt
```

## Configurar variables de entorno

1. Copia el archivo de ejemplo a la carpeta del proyecto Django (junto a `manage.py`):

```powershell
copy .env.example SmartDrop\.env
```

En macOS o Linux:

```bash
cp .env.example SmartDrop/.env
```

2. Abre `SmartDrop/.env` y completa los valores **reales** (no dejes los de ejemplo).

3. Genera una clave de Django:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Copia el resultado en `DJANGO_SECRET_KEY`.

Variables necesarias:

| Variable | Descripción |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clave secreta de Django. Genera una cadena aleatoria y no la compartas. |
| `DEBUG` | `True` para desarrollo local. |
| `ALLOWED_HOSTS` | Hosts permitidos, por ejemplo `localhost,127.0.0.1`. |
| `SUPABASE_URL` | URL del proyecto en Supabase (Project Settings → API). Es la URL pública del proyecto. |
| `SUPABASE_KEY` | Clave **secreta de servidor** de Supabase (secret / service_role). Solo debe vivir en `.env` en la máquina que corre Django. |

No subas el archivo `.env` a GitHub. `.env.example` sí puede subirse porque no contiene credenciales reales.

La aplicación lee `SmartDrop/.env`. Si también existe un `.env` en la raíz del repositorio, Django lo carga primero y `SmartDrop/.env` tiene prioridad. Basta con uno: usa `SmartDrop/.env`.

## Configurar Supabase

1. Crea un proyecto en el dashboard de Supabase.
2. En **Project Settings → API**, copia la URL del proyecto a `SUPABASE_URL`.
3. Copia la clave secreta de **servidor** (secret / service_role) a `SUPABASE_KEY`. Esa clave no debe usarse en una app móvil ni en código público. La clave pública (anon / publishable) no es suficiente si las tablas tienen RLS y no hay políticas que permitan leer/escribir `usuario`.
4. En **SQL Editor**, ejecuta el archivo `SmartDrop/supabase/schema.sql`. Eso crea las tablas `rol`, `usuario` y `retroalimentacion_consumo`, y los roles iniciales `user` y `admin`.

El backend usa la API REST de Supabase (`/rest/v1/...`). Login y registro **no** dependen de archivos `.pyc`.

Si activas Row Level Security (RLS) en esas tablas, la clave de servidor puede leer y escribir. Si usas solo la clave anon sin políticas, el registro y el login fallarán.

## Ejecutar el proyecto

Con el entorno virtual activado:

```powershell
cd SmartDrop
python manage.py migrate
python manage.py runserver
```

Abre en el navegador: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

La página principal redirige al login si no hay sesión.

## Login y registro

- Registro (web): [http://127.0.0.1:8000/register/](http://127.0.0.1:8000/register/)
- Login (web): [http://127.0.0.1:8000/login/](http://127.0.0.1:8000/login/)

El registro crea el usuario en Supabase (tabla `usuario`, contraseña hasheada) y el login valida esas credenciales contra Supabase.

También hay endpoints JSON para una app móvil (pasan por Django, no exponen la clave secreta):

- `POST /api/register/`
- `POST /api/login/`

Tras registrarte, inicia sesión con el mismo correo y contraseña. Luego puedes usar el dashboard y el resto de pantallas.

## Notas

- `db.sqlite3` es la base local de Django (sesiones y una copia local del usuario). Cada computadora genera la suya con `migrate`.
- `__pycache__` y `.pyc` se generan solos; no hacen falta para clonar ni ejecutar el proyecto.
- En otra computadora: clonar → entorno virtual → `pip install` → copiar `.env.example` a `SmartDrop\.env` → configurar Supabase y `DJANGO_SECRET_KEY` → `migrate` → `runserver`.
