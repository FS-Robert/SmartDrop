import requests
from django.conf import settings
from django.contrib.auth.hashers import make_password


def _supabase_url() -> str:
    return getattr(settings, 'SUPABASE_URL', '') or ''


def _supabase_key() -> str:
    return getattr(settings, 'SUPABASE_KEY', '') or ''


def _headers() -> dict:
    key = _supabase_key()
    return {
        'apikey': key,
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }


class SupabaseError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _base_url(table: str) -> str:
    return f"{_supabase_url().rstrip('/')}/rest/v1/{table}"


def _check_config():
    if not _supabase_url() or not _supabase_key():
        raise RuntimeError(
            'Supabase no está configurado. Define SUPABASE_URL y SUPABASE_KEY en SmartDrop/.env'
        )


def _handle_response(resp: requests.Response) -> dict | list | None:
    if resp.ok:
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    detail = resp.text
    try:
        payload = resp.json()
        detail = payload.get('message') or payload.get('hint') or payload.get('error') or resp.text
    except ValueError:
        pass
    raise SupabaseError(str(detail), status_code=resp.status_code)


def insert(table: str, data: dict, return_representation: bool = True) -> dict | None:
    """Inserta una fila en una tabla de Supabase."""
    _check_config()
    headers = _headers()
    if return_representation:
        headers['Prefer'] = 'return=representation'

    resp = requests.post(_base_url(table), headers=headers, json=data, timeout=10)
    result = _handle_response(resp)
    if isinstance(result, list) and result:
        return result[0]
    return result if isinstance(result, dict) else None


def fetch_latest(table: str, select: str = '*') -> dict | None:
    """Fetch the latest row from a table ordered by `fecha_registro`.

    Returns a dict or None if no rows.
    """
    _check_config()
    url = _base_url(table)
    params = {
        'select': select,
        'order': 'fecha_registro.desc',
        'limit': 1,
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=10)
    data = _handle_response(resp)
    return data[0] if isinstance(data, list) and data else None


def select(table: str, select: str = '*', params: dict | None = None) -> list:
    """Generic select. `params` are extra query params appended to the request."""
    _check_config()
    url = _base_url(table)
    query = {'select': select}
    if params:
        query.update(params)
    resp = requests.get(url, headers=_headers(), params=query, timeout=10)
    data = _handle_response(resp)
    return data if isinstance(data, list) else []


def get_rol_id(nombre_rol: str = 'user') -> int | None:
    """Obtiene el id_rol desde Supabase."""
    rows = select('rol', 'id_rol', {'nombre_rol': f'eq.{nombre_rol}', 'limit': '1'})
    return rows[0]['id_rol'] if rows else None


def get_user_by_email(email: str) -> dict | None:
    """Busca un usuario por correo en la tabla `usuario` de Supabase."""
    rows = select('usuario', '*', {'correo': f'eq.{email}', 'limit': '1'})
    return rows[0] if rows else None


def email_exists(email: str) -> bool:
    return get_user_by_email(email) is not None


def create_usuario(nombre: str, apellido: str, email: str, password: str) -> dict:
    """Registra un usuario en Supabase (tabla `usuario`)."""
    if email_exists(email):
        raise SupabaseError('Este correo ya está registrado', status_code=409)

    id_rol = get_rol_id('user')
    payload = {
        'nombre': nombre,
        'apellido': apellido,
        'correo': email,
        'contrasena': make_password(password),
        'estado_usuario': True,
    }
    if id_rol is not None:
        payload['id_rol'] = id_rol

    row = insert('usuario', payload)
    if not row:
        raise SupabaseError('No se pudo crear el usuario en Supabase')
    return row
