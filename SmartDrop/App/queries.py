"""Consultas a Supabase compartidas por las vistas web y la API móvil.

Cada consulta remota cuesta ~0,7 s, así que se agrupan en una sola ronda
paralela y los datos casi estáticos (viviendas, sensores, tanques) se cachean.
"""
import time
from collections import defaultdict, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from datetime import time as dt_time

from django.core.cache import cache
from django.utils import timezone

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


class LinkViviendaError(Exception):
    """Fallo de validación al vincular; `status` es el código HTTP equivalente."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def link_vivienda(user, account, holder):
    """Vincula la vivienda con NIC `account` al usuario si el titular coincide.

    Lógica compartida por la web y la app móvil. Devuelve la vivienda vinculada.
    """
    account = str(account or '').strip()
    holder = str(holder or '').strip()
    if not account or not holder:
        raise LinkViviendaError('Completa todos los campos.', 400)

    rows = supabase_client.select('vivienda', '*', {'nic': f'eq.{account}', 'limit': '1'})
    if not rows:
        raise LinkViviendaError('No encontramos ese número de cuenta.', 404)
    vivienda = rows[0]
    if str(vivienda.get('nombre_completo_titular', '')).strip().lower() != holder.lower():
        raise LinkViviendaError('El nombre no coincide con el titular.', 400)
    owner = owner_id(user)
    if vivienda.get('id_usuario_propietario') not in (None, owner):
        raise LinkViviendaError('Esta vivienda ya está vinculada.', 409)

    supabase_client.update(
        'vivienda',
        {'id_usuario_propietario': owner},
        {'id_vivienda': f"eq.{vivienda['id_vivienda']}"},
    )
    invalidate_user_viviendas(user)
    return vivienda


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


def owned_consumption(user, since_date=None, until_date=None):
    """Devuelve (viviendas, filas de consumo) del usuario.

    Con since_date los días sin consumo registrado se completan con el estimado desde el flujo.
    """
    viviendas = user_viviendas(user)
    in_filter = _in_filter(viviendas)
    rows = _consumption_rows(in_filter) if in_filter else []
    if since_date and in_filter:
        rows = with_flow_consumption(viviendas, rows, since_date, until_date)
    return viviendas, rows


# ── Consumo calculado desde el sensor de flujo ───────────────────────────────
# La tabla `consumo` no siempre se llena; cuando un día no tiene datos allí se
# estima integrando las lecturas del sensor de flujo (L/min) de ese día.
FLOW_PAGE_SIZE = 1000
FLOW_PAGES_PER_BATCH = 8
FLOW_MAX_PAGES_PER_DAY = 40
FLOW_MAX_GAP_SECONDS = 60
FLOW_TODAY_CACHE_SECONDS = 60
FLOW_PAST_DAY_CACHE_SECONDS = 24 * 3600
FLOW_PARTIAL_DAY_CACHE_SECONDS = 600
# Tiempo máximo (en segundos) que una petición dedica a calcular días pasados; lo que
# no alcance queda en caché para la siguiente carga, así un mes largo se completa por tandas.
FLOW_TIME_BUDGET_SECONDS = 4


def consumption_date(raw_date):
    row_date = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
    if row_date.tzinfo:
        row_date = timezone.localtime(row_date)
    return row_date.date()


def _flow_sensor_ids():
    return [
        str(sensor['id_sensor'])
        for sensor in sensors()
        if sensor.get('id_sensor') is not None
        and any(name in str(sensor.get('tipo_sensor') or '').lower() for name in ('flujo', 'flow'))
    ]


def _fetch_flow_readings(in_filter, sensor_ids, since, until):
    """Lecturas de flujo entre `since` y `until` (más recientes primero).

    Devuelve (filas, truncado): truncado es True si se alcanzó el tope de páginas.
    """
    base = {
        'id_vivienda': in_filter,
        'id_sensor': f"in.({','.join(sensor_ids)})",
        'and': f'(fecha_registro.gte.{since.isoformat()},fecha_registro.lt.{until.isoformat()})',
        'order': 'fecha_registro.desc',
        'limit': str(FLOW_PAGE_SIZE),
    }

    def page_loader(page):
        return lambda: supabase_client.select(
            'lectura', 'id_vivienda,id_sensor,valor,fecha_registro',
            {**base, 'offset': str(page * FLOW_PAGE_SIZE)},
        )

    rows = []
    page = 0
    while page < FLOW_MAX_PAGES_PER_DAY:
        pages = range(page, min(page + FLOW_PAGES_PER_BATCH, FLOW_MAX_PAGES_PER_DAY))
        results = run_parallel(*[page_loader(number) for number in pages])
        for result in results:
            rows.extend(result)
        page += len(pages)
        if any(len(result) < FLOW_PAGE_SIZE for result in results):
            return rows, False
    return rows, True


def _liters_by_day(rows):
    """Litros por fecha local: cada lectura (L/min) rige hasta la siguiente, con tope de
    `FLOW_MAX_GAP_SECONDS` para no contar periodos en que el sensor estuvo sin reportar."""
    series = defaultdict(list)
    for row in rows:
        try:
            moment = datetime.fromisoformat(str(row['fecha_registro']).replace('Z', '+00:00'))
            value = float(row['valor'])
        except (KeyError, TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt_timezone.utc)
        series[(row.get('id_vivienda'), row.get('id_sensor'))].append((moment, value))

    totals = defaultdict(float)
    for points in series.values():
        points.sort(key=lambda point: point[0])
        for (start, value), (end, _) in zip(points, points[1:]):
            seconds = min((end - start).total_seconds(), FLOW_MAX_GAP_SECONDS)
            totals[timezone.localtime(start).date()] += max(value, 0) * seconds / 60
    return {day: round(liters, 2) for day, liters in totals.items()}


def _local_midnight(day):
    return timezone.make_aware(datetime.combine(day, dt_time.min))


def _flow_liters_for_day(in_filter, sensor_ids, day):
    """(litros, truncado) del día local `day`."""
    start = _local_midnight(day)
    end = min(_local_midnight(day + timedelta(days=1)), timezone.now())
    if end <= start:
        return 0.0, False
    rows, truncated = _fetch_flow_readings(
        in_filter, sensor_ids, start.astimezone(dt_timezone.utc), end.astimezone(dt_timezone.utc),
    )
    return _liters_by_day(rows).get(day, 0.0), truncated


def flow_consumption_by_day(viviendas, since_date, until_date=None):
    """{fecha: litros} estimados desde el sensor de flujo entre ambas fechas (inclusive).

    Cada día se calcula y se guarda en caché por separado (los pasados un día entero), de
    modo que solo se consultan los días que faltan, de los más recientes a los más antiguos.
    """
    in_filter = _in_filter(viviendas)
    today = timezone.localdate()
    until_date = min(until_date or today, today)
    if not in_filter or until_date < since_date:
        return {}

    result = {}
    missing = []
    for offset in range((until_date - since_date).days, -1, -1):
        day = since_date + timedelta(days=offset)
        cached = cache.get(f'flowday:{in_filter}:{day}')
        if cached is None:
            missing.append(day)
        else:
            result[day] = cached
    if not missing:
        return result

    sensor_ids = _flow_sensor_ids()
    if not sensor_ids:
        return result
    deadline = time.monotonic() + FLOW_TIME_BUDGET_SECONDS
    for day in missing:
        if day != today and time.monotonic() > deadline:
            break
        liters, truncated = _flow_liters_for_day(in_filter, sensor_ids, day)
        result[day] = liters
        if day >= today:
            ttl = FLOW_TODAY_CACHE_SECONDS
        else:
            ttl = FLOW_PARTIAL_DAY_CACHE_SECONDS if truncated else FLOW_PAST_DAY_CACHE_SECONDS
        cache.set(f'flowday:{in_filter}:{day}', liters, ttl)
    return result


def with_flow_consumption(viviendas, rows, since_date, until_date=None):
    """Agrega a `rows` filas diarias calculadas desde el flujo para los días sin consumo registrado.

    Si el flujo no está disponible devuelve `rows` sin cambios.
    """
    try:
        flow_by_day = flow_consumption_by_day(viviendas, since_date, until_date)
    except Exception:
        return rows

    recorded = defaultdict(float)
    for row in rows:
        try:
            recorded[consumption_date(row['fecha'])] += float(row.get('consumo_total') or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

    estimated = [
        {
            'id_vivienda': viviendas[0].get('id_vivienda') if viviendas else None,
            'fecha': _local_midnight(day).isoformat(),
            'consumo_total': liters,
            'periodo': 'dia',
            'origen': 'flujo',
        }
        for day, liters in sorted(flow_by_day.items(), reverse=True)
        if liters > 0 and recorded.get(day, 0) <= 0
    ]
    return estimated + list(rows)


def consumption_on(rows, day):
    """Suma el consumo de las filas cuya fecha local es `day`."""
    total = 0.0
    for row in rows:
        try:
            if consumption_date(row['fecha']) == day:
                total += float(row.get('consumo_total') or row.get('consumo_promedio') or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return round(total, 2)