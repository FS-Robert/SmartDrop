"""Respaldos automáticos de la base (PB064, escenario 3: disponibilidad y recuperación).

Un respaldo es un solo archivo comprimido y cifrado con:
- todas las tablas de Supabase (una lista JSON por tabla, leída página por página),
- las bases SQLite del servidor (sesiones, Gotita y el motor de predicción),
- un manifiesto con cuántas filas trae cada tabla y la huella SHA-256 de cada archivo.

Se guarda en la carpeta local de respaldos y se sube a un bucket privado de Supabase Storage, que está fuera del
servidor: si el servidor se pierde, el respaldo sigue ahí. El cifrado usa RESPALDO_CLAVE (Fernet, AES-128 + firma);
sin esa clave el archivo no se puede leer ni alterar sin que se note. Se conservan los últimos RESPALDO_DIAS.

`manage.py respaldar_base` lo crea (el temporizador de systemd lo corre cada noche) y
`manage.py restaurar_respaldo` lo revisa y lo devuelve a Supabase.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import connections

from . import supabase_client

logger = logging.getLogger(__name__)

PAGINA = 1000
PREFIJO = 'smartdrop-respaldo-'
EXTENSION = '.tar.gz.enc'
# Tablas que se respaldan si Supabase no deja leer la lista (por si cambia la API).
TABLAS_CONOCIDAS = (
    'rol', 'usuario', 'vivienda', 'tanque', 'sensor', 'lectura', 'historial_nivel_tanque', 'consumo',
    'retroalimentacion_consumo', 'valvula', 'log_valvula', 'fuga', 'incidente_calidad', 'alerta', 'notificacion',
    'reporte', 'reporte_adjunto', 'reporte_mensaje', 'prediccion_desabasto',
)


class RespaldoError(Exception):
    pass


# ── Ajustes ──────────────────────────────────────────────────────────────────

def carpeta():
    ruta = Path(getattr(settings, 'RESPALDO_DIR', '') or Path(settings.BASE_DIR) / 'respaldos')
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def bucket():
    return getattr(settings, 'RESPALDO_BUCKET', 'respaldos') or 'respaldos'


def dias_a_conservar():
    return int(getattr(settings, 'RESPALDO_DIAS', 14))


def _fernet():
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401

    clave = (getattr(settings, 'RESPALDO_CLAVE', '') or '').strip()
    if not clave:
        raise RespaldoError('Falta RESPALDO_CLAVE en el .env (genera una con: manage.py respaldar_base --nueva-clave).')
    try:
        return Fernet(clave.encode())
    except ValueError as exc:
        raise RespaldoError('RESPALDO_CLAVE no es válida (debe ser una clave Fernet de 44 caracteres).') from exc


def nueva_clave():
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


# ── Lectura de Supabase ──────────────────────────────────────────────────────

def _rest_base():
    base = supabase_client._supabase_url().rstrip('/')
    return base if base.endswith('/rest/v1') else f'{base}/rest/v1'


def _storage_base():
    base = supabase_client._supabase_url().rstrip('/')
    return base[:-len('/rest/v1')] + '/storage/v1' if base.endswith('/rest/v1') else f'{base}/storage/v1'


def esquema_supabase():
    """{tabla: [columnas de la llave primaria]} leyendo la descripción que publica PostgREST."""
    supabase_client._check_config()
    resp = supabase_client._request('GET', _rest_base() + '/', headers=supabase_client._headers())
    tablas = {}
    if resp.ok:
        definiciones = (resp.json() or {}).get('definitions') or {}
        for tabla, definicion in definiciones.items():
            llaves = [col for col, info in (definicion.get('properties') or {}).items()
                      if '<pk/>' in str(info.get('description') or '')]
            tablas[tabla] = llaves
    if not tablas:
        logger.warning('Supabase no devolvió la lista de tablas; se usan las conocidas.')
        tablas = {tabla: [] for tabla in TABLAS_CONOCIDAS}
    return dict(sorted(tablas.items()))


def leer_tabla(tabla, llaves=()):
    """Todas las filas de una tabla, de 1000 en 1000 y en orden estable por su llave primaria."""
    filas, desde = [], 0
    orden = ','.join(f'{c}.asc' for c in llaves) if llaves else None
    while True:
        params = {'limit': str(PAGINA), 'offset': str(desde)}
        if orden:
            params['order'] = orden
        pagina = supabase_client.select(tabla, '*', params)
        filas.extend(pagina)
        if len(pagina) < PAGINA:
            return filas
        desde += PAGINA


# ── Armar y abrir el archivo ─────────────────────────────────────────────────

def _agregar(tar, nombre, contenido, manifiesto):
    info = tarfile.TarInfo(nombre)
    info.size = len(contenido)
    info.mtime = int(datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(contenido))
    manifiesto['archivos'][nombre] = hashlib.sha256(contenido).hexdigest()


def _copia_sqlite(alias):
    """Copia consistente de una base SQLite aunque el servidor la esté usando (API de backup de SQLite)."""
    db = settings.DATABASES.get(alias) or {}
    if 'sqlite3' not in db.get('ENGINE', '') or not Path(db.get('NAME', '')).exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / 'copia.sqlite3'
        origen = sqlite3.connect(str(db['NAME']))
        copia = sqlite3.connect(str(destino))
        try:
            origen.backup(copia)
        finally:
            copia.close()
            origen.close()
        return destino.read_bytes()


def crear_respaldo(tablas=None, incluir_sqlite=True, ahora=None):
    """Crea el respaldo cifrado en la carpeta local. Devuelve (ruta, manifiesto)."""
    fernet = _fernet()
    ahora = ahora or datetime.now(dt_timezone.utc)
    esquema = esquema_supabase()
    if tablas:
        esquema = {t: esquema.get(t, []) for t in tablas}
    manifiesto = {'creado': ahora.isoformat(), 'version': 1, 'tablas': {}, 'llaves': {}, 'archivos': {}}
    crudo = io.BytesIO()
    with tarfile.open(fileobj=crudo, mode='w:gz') as tar:
        for tabla, llaves in esquema.items():
            try:
                filas = leer_tabla(tabla, llaves)
            except supabase_client.SupabaseError as exc:
                raise RespaldoError(f'No se pudo leer la tabla {tabla}: {exc.detalle}') from exc
            manifiesto['tablas'][tabla] = len(filas)
            manifiesto['llaves'][tabla] = llaves
            _agregar(tar, f'supabase/{tabla}.json', json.dumps(filas, ensure_ascii=False, default=str).encode(), manifiesto)
        if incluir_sqlite:
            for alias in connections:
                contenido = _copia_sqlite(alias)
                if contenido is not None:
                    _agregar(tar, f'sqlite/{alias}.sqlite3', contenido, manifiesto)
        _agregar(tar, 'manifiesto.json', json.dumps(manifiesto, ensure_ascii=False, indent=2).encode(), {'archivos': {}})
    ruta = carpeta() / f"{PREFIJO}{ahora.strftime('%Y%m%d-%H%M%S')}{EXTENSION}"
    ruta.write_bytes(fernet.encrypt(crudo.getvalue()))
    return ruta, manifiesto


def abrir_respaldo(ruta):
    """Descifra y revisa un respaldo. Devuelve (manifiesto, {tabla: filas}, {alias: bytes de SQLite})."""
    from cryptography.fernet import InvalidToken

    try:
        crudo = _fernet().decrypt(Path(ruta).read_bytes())
    except InvalidToken as exc:
        raise RespaldoError('El respaldo no se pudo descifrar: la clave no coincide o el archivo fue alterado.') from exc
    tablas, sqlite_dbs, contenidos = {}, {}, {}
    with tarfile.open(fileobj=io.BytesIO(crudo), mode='r:gz') as tar:
        for miembro in tar.getmembers():
            if miembro.isfile():
                contenidos[miembro.name] = tar.extractfile(miembro).read()
    if 'manifiesto.json' not in contenidos:
        raise RespaldoError('El respaldo no trae manifiesto.')
    manifiesto = json.loads(contenidos.pop('manifiesto.json'))
    for nombre, huella in manifiesto['archivos'].items():
        if hashlib.sha256(contenidos.get(nombre, b'')).hexdigest() != huella:
            raise RespaldoError(f'El archivo {nombre} del respaldo está dañado (la huella no coincide).')
    for nombre, contenido in contenidos.items():
        if nombre.startswith('supabase/'):
            tablas[nombre[len('supabase/'):-len('.json')]] = json.loads(contenido)
        elif nombre.startswith('sqlite/'):
            sqlite_dbs[nombre[len('sqlite/'):-len('.sqlite3')]] = contenido
    for tabla, cantidad in manifiesto['tablas'].items():
        if len(tablas.get(tabla, [])) != cantidad:
            raise RespaldoError(f'La tabla {tabla} no trae las {cantidad} filas que dice el manifiesto.')
    return manifiesto, tablas, sqlite_dbs


# ── Restaurar ────────────────────────────────────────────────────────────────

def restaurar_tabla(tabla, filas, llaves):
    """Devuelve las filas a Supabase: las que faltan se crean y las que existen se reemplazan (upsert)."""
    if not filas:
        return 0
    supabase_client._check_config()
    headers = {**supabase_client._headers(), 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    params = {'on_conflict': ','.join(llaves)} if llaves else None
    for inicio in range(0, len(filas), 500):
        resp = supabase_client._request('POST', supabase_client._base_url(tabla), headers=headers,
                                        params=params, json=filas[inicio:inicio + 500])
        supabase_client._handle_response(resp)
    return len(filas)


# Orden para no romper llaves foráneas: primero catálogos y dueños, después lo que depende de ellos.
ORDEN_RESTAURAR = (
    'rol', 'usuario', 'vivienda', 'tanque', 'sensor', 'valvula', 'consumo', 'retroalimentacion_consumo', 'lectura',
    'historial_nivel_tanque', 'log_valvula', 'fuga', 'incidente_calidad', 'alerta', 'notificacion', 'reporte',
    'reporte_adjunto', 'reporte_mensaje', 'prediccion_desabasto',
)


def orden_de_restauracion(tablas):
    conocidas = [t for t in ORDEN_RESTAURAR if t in tablas]
    return conocidas + sorted(t for t in tablas if t not in ORDEN_RESTAURAR)


# ── Fuera del servidor: Supabase Storage ─────────────────────────────────────

def _storage(method, ruta, **kwargs):
    supabase_client._check_config()
    headers = {k: v for k, v in supabase_client._headers().items() if k != 'Content-Type'}
    headers.update(kwargs.pop('headers', {}))
    return supabase_client._session.request(method, _storage_base() + ruta, headers=headers, timeout=120, **kwargs)


def subir(ruta):
    """Sube el respaldo al bucket privado (lo crea la primera vez)."""
    ruta = Path(ruta)
    resp = _storage('POST', '/bucket', json={'id': bucket(), 'name': bucket(), 'public': False})
    if not resp.ok and resp.status_code not in (400, 409):
        raise RespaldoError(f'No se pudo crear el bucket de respaldos ({resp.status_code}).')
    resp = _storage('POST', f'/object/{bucket()}/{ruta.name}', data=ruta.read_bytes(),
                    headers={'Content-Type': 'application/octet-stream', 'x-upsert': 'true'})
    if not resp.ok:
        raise RespaldoError(f'No se pudo subir el respaldo a Supabase Storage ({resp.status_code}).')
    return f'{bucket()}/{ruta.name}'


def listar_remotos():
    resp = _storage('POST', f'/object/list/{bucket()}', json={'prefix': '', 'limit': 1000,
                                                              'sortBy': {'column': 'name', 'order': 'desc'}})
    if not resp.ok:
        raise RespaldoError(f'No se pudo listar el bucket de respaldos ({resp.status_code}).')
    return sorted((o['name'] for o in resp.json() if o.get('name', '').startswith(PREFIJO)), reverse=True)


def descargar(nombre):
    resp = _storage('GET', f'/object/{bucket()}/{nombre}')
    if not resp.ok:
        raise RespaldoError(f'No se pudo descargar {nombre} ({resp.status_code}).')
    destino = carpeta() / nombre
    destino.write_bytes(resp.content)
    return destino


def _fecha_de(nombre):
    try:
        return datetime.strptime(nombre[len(PREFIJO):len(PREFIJO) + 15], '%Y%m%d-%H%M%S').replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def limpiar_viejos(ahora=None, remoto=True):
    """Borra los respaldos con más de RESPALDO_DIAS (siempre deja al menos el más reciente)."""
    limite = (ahora or datetime.now(dt_timezone.utc)) - timedelta(days=dias_a_conservar())
    locales = sorted(carpeta().glob(f'{PREFIJO}*{EXTENSION}'), reverse=True)
    borrados = []
    for ruta in locales[1:]:
        fecha = _fecha_de(ruta.name)
        if fecha and fecha < limite:
            ruta.unlink()
            borrados.append(ruta.name)
    if remoto:
        viejos = [n for n in listar_remotos()[1:] if (_fecha_de(n) or limite) < limite]
        if viejos:
            _storage('DELETE', f'/object/{bucket()}', json={'prefixes': viejos})
            borrados.extend(viejos)
    return borrados


def guardar_sqlite(sqlite_dbs, destino):
    """Escribe las bases SQLite del respaldo en una carpeta (para revisarlas o reemplazarlas a mano)."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    rutas = []
    for alias, contenido in sqlite_dbs.items():
        ruta = destino / f'{alias}.sqlite3'
        ruta.write_bytes(contenido)
        rutas.append(ruta)
    return rutas

