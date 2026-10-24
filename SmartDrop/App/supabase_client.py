import re
import logging

import requests
from requests import RequestException
from requests.adapters import HTTPAdapter
from django.conf import settings
from passlib.hash import bcrypt

logger = logging.getLogger(__name__)

# Una sola sesión para todos los hilos: abrir TCP+TLS hacia Supabase cuesta ~2 s,
# una consulta con la conexión ya abierta ~0,7 s.
_session = requests.Session()
_session.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=16))


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
    """Fallo al hablar con Supabase.

    `str(exc)` se puede mostrar al usuario: es un mensaje genérico o uno escrito a propósito (por ejemplo
    "Este correo ya está registrado"). El detalle técnico (mensaje de PostgREST y URL, que revela el proyecto
    y el esquema) queda solo en `detalle` y en los logs.
    """

    MENSAJE_GENERICO = 'No se pudo completar la operación. Inténtalo de nuevo en unos minutos.'

    def __init__(self, message: str | None = None, status_code: int | None = None, detalle: str | None = None):
        super().__init__(message or self.MENSAJE_GENERICO)
        self.status_code = status_code
        self.detalle = detalle or message


def _base_url(table: str) -> str:
    """Return the full REST endpoint for a table.

    Avoid duplicating `/rest/v1` if `SUPABASE_URL` already contains it.
    """
    base = _supabase_url().rstrip('/')
    # If the provided SUPABASE_URL already points to the REST endpoint,
    # don't append another `/rest/v1` segment.
    _validar_tabla(table)
    if base.endswith('/rest/v1'):
        return f"{base}/{table}"
    return f"{base}/rest/v1/{table}"


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
    url = getattr(resp.request, 'url', None)
    try:
        payload = resp.json()
        detail = payload.get('message') or payload.get('hint') or payload.get('error') or resp.text
    except ValueError:
        pass
    detalle = str(detail)
    if url:
        detalle = f"{detalle} (url: {url})"
    logger.warning('Supabase respondió %s: %s', resp.status_code, detalle)
    raise SupabaseError(status_code=resp.status_code, detalle=detalle)


# ── Protección contra inyección ──────────────────────────────────────────────
# Supabase se consulta por su API REST (PostgREST), que siempre arma consultas parametrizadas: no hay SQL
# escrito a mano en este proyecto. El riesgo real es la *inyección de filtros*: que un dato del usuario
# termine dentro de la sintaxis de un filtro (por ejemplo en `in.(...)` u `or=(...)`) y cambie su lógica.
# Por eso: los nombres de tabla y de parámetro se validan contra una lista blanca de formatos, los valores
# no pueden traer caracteres de control, y las listas se arman con `in_list` / `ids_in`, que validan o
# entrecomillan cada elemento.

_IDENTIFICADOR = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
_PARAM = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}(->>?[A-Za-z0-9_]{1,63})*$')
_PARAMS_RESERVADOS = {'select', 'order', 'limit', 'offset', 'or', 'and', 'not.or', 'not.and', 'on_conflict', 'columns'}
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


class FiltroInvalido(SupabaseError, ValueError):
    """Un parámetro de consulta no tiene un formato permitido (posible intento de inyección).

    Es un SupabaseError: las vistas que ya atrapan SupabaseError responden con su mensaje de error en vez de
    un 500, y el motivo técnico queda solo en `detalle` (no se le muestra al usuario).
    """

    def __init__(self, detalle):
        super().__init__(status_code=400, detalle=detalle)


def _validar_tabla(table: str) -> str:
    if not isinstance(table, str) or not _IDENTIFICADOR.match(table):
        raise FiltroInvalido(f'Nombre de tabla no permitido: {table!r}')
    return table


def _validar_params(params: dict | None) -> dict | None:
    if not params:
        return params
    limpios = {}
    for clave, valor in params.items():
        if not isinstance(clave, str) or not (clave in _PARAMS_RESERVADOS or _PARAM.match(clave)):
            raise FiltroInvalido(f'Parámetro de consulta no permitido: {clave!r}')
        if valor is None:
            continue  # requests ya omitía los parámetros None; se conserva ese comportamiento
        if isinstance(valor, bool) or not isinstance(valor, (str, int, float)):
            raise FiltroInvalido(f'Valor no permitido para {clave!r}')
        texto = str(valor)
        # 8000: un filtro in.(...) con cientos de ids (por ejemplo las alertas de una persona) cabe holgado.
        if _CONTROL.search(texto) or len(texto) > 8000:
            raise FiltroInvalido(f'Valor no permitido para {clave!r}')
        if clave in ('limit', 'offset') and not texto.isdigit():
            raise FiltroInvalido(f'{clave} debe ser un número entero')
        limpios[clave] = texto
    return limpios


def ids_in(values) -> str:
    """Filtro `in.(1,2,3)` solo con enteros positivos. Lanza FiltroInvalido si alguno no lo es."""
    ids = []
    for value in values:
        texto = str(value).strip()
        if not texto.isascii() or not texto.isdigit():
            raise FiltroInvalido(f'Identificador no numérico: {value!r}')
        ids.append(str(int(texto)))
    if not ids:
        raise FiltroInvalido('La lista de identificadores está vacía')
    return f"in.({','.join(ids)})"


def in_list(values) -> str:
    """Filtro `in.("a","b")` con cada texto entre comillas y escapado según las reglas de PostgREST."""
    partes = []
    for value in values:
        texto = str(value)
        if _CONTROL.search(texto):
            raise FiltroInvalido('Valor con caracteres de control')
        partes.append('"' + texto.replace('\\', '\\\\').replace('"', '\\"') + '"')
    if not partes:
        raise FiltroInvalido('La lista está vacía')
    return f"in.({','.join(partes)})"


def eq_id(value) -> str:
    """Filtro `eq.<id>` solo si el id es un entero positivo (los ids que llegan por URL o formulario)."""
    texto = str(value).strip()
    if not texto.isascii() or not texto.isdigit():
        raise FiltroInvalido(f'Identificador no numérico: {value!r}')
    return f'eq.{int(texto)}'


def _request(method: str, url: str, **kwargs):
    if 'params' in kwargs:
        kwargs['params'] = _validar_params(kwargs['params'])
    try:
        return _session.request(method, url, timeout=10, **kwargs)
    except RequestException as exc:
        detalle = f'No se pudo conectar con Supabase ({exc.__class__.__name__}). Verifica SUPABASE_URL, DNS y conexión.'
        logger.warning(detalle)
        raise SupabaseError(detalle=detalle) from exc


def insert(table: str, data: dict, return_representation: bool = True) -> dict | None:
    """Inserta una fila en una tabla de Supabase."""
    _check_config()
    headers = _headers()
    if return_representation:
        headers['Prefer'] = 'return=representation'

    resp = _request('POST', _base_url(table), headers=headers, json=data)
    result = _handle_response(resp)
    if isinstance(result, list) and result:
        return result[0]
    return result if isinstance(result, dict) else None


def insert_many(table: str, rows: list[dict]) -> None:
    """Inserta varias filas en una sola petición (sin devolver representación)."""
    if not rows:
        return
    _check_config()
    resp = _request('POST', _base_url(table), headers=_headers(), json=rows)
    _handle_response(resp)


def delete(table: str, params: dict) -> None:
    """Borra las filas que cumplan los filtros; exige al menos un filtro para evitar vaciar la tabla."""
    if not params:
        raise ValueError('delete() requiere al menos un filtro.')
    _check_config()
    resp = _request('DELETE', _base_url(table), headers=_headers(), params=params)
    _handle_response(resp)


def select(table: str, select: str = '*', params: dict | None = None) -> list:
    """Generic select. `params` are extra query params appended to the request."""
    _check_config()
    url = _base_url(table)
    query = {'select': select}
    if params:
        query.update(params)
    resp = _request('GET', url, headers=_headers(), params=query)
    data = _handle_response(resp)
    return data if isinstance(data, list) else []


def update(
    table: str,
    data: dict,
    params: dict | None = None,
    return_representation: bool = False,
) -> dict | None:
    """Actualiza filas de una tabla de Supabase que cumplan los filtros dados."""
    _check_config()
    headers = _headers()
    if return_representation:
        headers['Prefer'] = 'return=representation'

    resp = _request(
        'PATCH',
        _base_url(table),
        headers=headers,
        params=params,
        json=data,
    )
    result = _handle_response(resp)
    if isinstance(result, list) and result:
        return result[0]
    return result if isinstance(result, dict) else None


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
    if id_rol is None:
        raise SupabaseError(detalle="No existe el rol 'user' en la tabla rol de Supabase.")
    payload = {
        'nombre': nombre,
        'apellido': apellido,
        'correo': email,
        'contrasena': bcrypt.using(rounds=12, ident='2b').hash(password),
        'estado_usuario': True,
        'id_rol': id_rol,
    }

    row = insert('usuario', payload)
    if not row:
        raise SupabaseError(detalle='Supabase no devolvió el usuario creado.')
    return row
