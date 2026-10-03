"""Consultas a Supabase compartidas por las vistas web y la API móvil.

Cada consulta remota cuesta ~0,7 s, así que se agrupan en una sola ronda
paralela y los datos casi estáticos (viviendas, sensores, tanques) se cachean.
"""
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

from . import supabase_client

VIVIENDAS_CACHE_SECONDS = 30
REFERENCE_CACHE_SECONDS = 60

OwnedData = namedtuple('OwnedData', 'viviendas sensores lecturas tanques consumo')
NO_DATA = OwnedData([], [], [], [], [])


def safe_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def run_parallel(*tasks):
    """Ejecuta funciones sin argumentos a la vez y devuelve sus resultados en orden."""
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(task) for task in tasks]
        return [future.result() for future in futures]


def owner_id(user):
    return getattr(user, 'supabase_id', None) or user.id_usuario


def _cached(key, seconds, loader):
    value = cache.get(key)
    if value is None:
        value = loader()
        cache.set(key, value, seconds)
    return value


def user_viviendas(user):
    owner = owner_id(user)
    return _cached(
        f'viviendas:{owner}',
        VIVIENDAS_CACHE_SECONDS,
        lambda: supabase_client.select(
            'vivienda',
            'id_vivienda,nic,direccion',
            {'id_usuario_propietario': f'eq.{owner}', 'limit': '1000'},
        ),
    )


def invalidate_user_viviendas(user):
    cache.delete_many([f'viviendas:{user.id_usuario}', f'viviendas:{owner_id(user)}'])


def sensors():
    return _cached(
        'sensores',
        REFERENCE_CACHE_SECONDS,
        lambda: supabase_client.select('sensor', '*', {'limit': '1000'}),
    )


def _tanks_or_empty():
    try:
        return _cached(
            'tanques',
            REFERENCE_CACHE_SECONDS,
            lambda: supabase_client.select(
                'tanque',
                'id_sensor_nivel,capacidad_maxima_litros,altura_total',
                {'limit': '1000'},
            ),
        )
    except Exception:
        return []


def _in_filter(viviendas):
    ids = [str(row['id_vivienda']) for row in viviendas if row.get('id_vivienda')]
    return f"in.({','.join(ids)})" if ids else None


def _consumption_rows(in_filter):
    return supabase_client.select(
        'consumo',
        '*',
        {'id_vivienda': in_filter, 'order': 'fecha.desc', 'limit': '1000'},
    )


def owned_data(user, consumption=False):
    """Viviendas del usuario y, en paralelo, sus lecturas, sensores, tanques y consumo."""
    viviendas = user_viviendas(user)
    in_filter = _in_filter(viviendas)
    if not in_filter:
        return OwnedData(viviendas, [], [], [], [])

    tasks = [
        lambda: supabase_client.select(
            'lectura',
            '*',
            {'id_vivienda': in_filter, 'order': 'fecha_registro.desc', 'limit': '2000'},
        ),
        sensors,
        _tanks_or_empty,
    ]
    if consumption:
        tasks.append(lambda: _consumption_rows(in_filter))
    lecturas, sensores, tanques, *consumo = run_parallel(*tasks)

    sensor_lookup = {str(sensor.get('id_sensor')): sensor for sensor in sensores}
    for lectura in lecturas:
        sensor = sensor_lookup.get(str(lectura.get('id_sensor')), {})
        lectura['tipo_sensor'] = (sensor.get('tipo_sensor') or '').lower()
        lectura['unidad_medida'] = sensor.get('unidad_medida') or ''
    return OwnedData(viviendas, sensores, lecturas, tanques, consumo[0] if consumo else [])


def owned_consumption(user):
    """Devuelve (viviendas, filas de consumo) del usuario."""
    viviendas = user_viviendas(user)
    in_filter = _in_filter(viviendas)
    return viviendas, (_consumption_rows(in_filter) if in_filter else [])
