"""Consultas a Supabase compartidas por las vistas web y la API móvil.

Cada consulta remota cuesta ~0,7 s, así que se agrupan en una sola ronda
paralela y los datos casi estáticos (viviendas, sensores, tanques) se cachean.
"""
import threading
import time
from collections import defaultdict, namedtuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
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


ADMIN_ROLE_ID = 2


def owner_id(user):
    """id_usuario de Supabase del usuario (web o JWT de la app)."""
    return getattr(user, 'supabase_id', None) or getattr(user, 'id_usuario', None)


def is_admin(user):
    return getattr(user, 'rol_id', None) == ADMIN_ROLE_ID


def users_by_id(ids, columns):
    """{id_usuario: fila} de los usuarios indicados, en una sola consulta IN y sin repetir ids.

    Devuelve {} si no hay ids o si Supabase falla (los nombres son decorativos en todas las vistas).
    """
    ids = sorted({str(i) for i in ids if i is not None and str(i).strip()})
    if not ids:
        return {}
    try:
        rows = supabase_client.select(
            'usuario', columns, {'id_usuario': f"in.({','.join(ids)})", 'limit': str(len(ids))},
        )
    except supabase_client.SupabaseError:
        return {}
    return {row['id_usuario']: row for row in rows}


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
FLOW_WORKERS = 8
# Tiempo máximo (en segundos) que una petición espera los días pasados; los que no terminen a
# tiempo se siguen calculando en segundo plano y quedan en caché para la siguiente carga.
FLOW_TIME_BUDGET_SECONDS = 4

# Pool compartido: los días se consultan en paralelo y un cálculo puede terminar después de la respuesta.
_flow_executor = ThreadPoolExecutor(max_workers=FLOW_WORKERS, thread_name_prefix='flowday')


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

    Devuelve (filas, truncado): truncado es True si se alcanzó el tope de páginas. La primera
    página va sola (casi siempre basta); si viene llena, el resto se pide en tandas paralelas.
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

    rows = page_loader(0)()
    if len(rows) < FLOW_PAGE_SIZE:
        return rows, False
    page = 1
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


def _flow_day_cached(in_filter, sensor_ids, day, today):
    liters, truncated = _flow_liters_for_day(in_filter, sensor_ids, day)
    if day >= today:
        ttl = FLOW_TODAY_CACHE_SECONDS
    else:
        ttl = FLOW_PARTIAL_DAY_CACHE_SECONDS if truncated else FLOW_PAST_DAY_CACHE_SECONDS
    cache.set(f'flowday:{in_filter}:{day}', liters, ttl)
    return liters


def flow_consumption_by_day(viviendas, since_date, until_date=None, skip_days=()):
    """{fecha: litros} estimados desde el sensor de flujo entre ambas fechas (inclusive).

    Cada día se calcula en paralelo y se guarda en caché por separado (los pasados un día entero),
    así que solo se consultan los días que faltan. Los días de `skip_days` (p. ej. con consumo
    registrado) no se calculan.
    """
    in_filter = _in_filter(viviendas)
    today = timezone.localdate()
    until_date = min(until_date or today, today)
    if not in_filter or until_date < since_date:
        return {}

    skip = set(skip_days)
    result = {}
    missing = []
    for offset in range((until_date - since_date).days, -1, -1):
        day = since_date + timedelta(days=offset)
        if day in skip:
            continue
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
    futures = {day: _flow_executor.submit(_flow_day_cached, in_filter, sensor_ids, day, today) for day in missing}
    deadline = time.monotonic() + FLOW_TIME_BUDGET_SECONDS
    for day, future in futures.items():
        # Hoy siempre se espera; los días pasados solo hasta agotar el presupuesto.
        timeout = None if day >= today else max(deadline - time.monotonic(), 0)
        try:
            result[day] = future.result(timeout=timeout)
        except FuturesTimeout:
            continue
    return result


def with_flow_consumption(viviendas, rows, since_date, until_date=None):
    """Agrega a `rows` filas diarias calculadas desde el flujo para los días sin consumo registrado.

    Si el flujo no está disponible devuelve `rows` sin cambios.
    """
    recorded = defaultdict(float)
    for row in rows:
        try:
            recorded[consumption_date(row['fecha'])] += float(row.get('consumo_total') or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

    try:
        flow_by_day = flow_consumption_by_day(
            viviendas, since_date, until_date, skip_days=[day for day, total in recorded.items() if total > 0],
        )
    except Exception:
        return rows

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


# ── Series agregadas de un sensor (lo comparten todas las viviendas) ──────────
#
# Un mes de lecturas son >100 mil filas por sensor (~30 s en traerlas de Supabase), así que para los
# rangos largos se guardan en memoria sumas por intervalos de 15 min y en cada consulta solo se piden
# las lecturas nuevas. Cada SENSOR_REBUILD_SECONDS se reconstruye todo por si se editaron lecturas viejas.

SENSOR_RANGES = {'1h': timedelta(hours=1), '1d': timedelta(days=1), '1w': timedelta(days=7), '1m': timedelta(days=30)}
# Tamaño del intervalo en que se promedian las lecturas de todas las viviendas, por rango.
SENSOR_BUCKETS = {'1h': timedelta(minutes=2), '1d': timedelta(minutes=15), '1w': timedelta(hours=1), '1m': timedelta(hours=4)}
SENSOR_PAGE = 1000
SENSOR_MAX_PAGES = 40
SENSOR_BASE_BUCKET_SECONDS = 15 * 60
SENSOR_BASE_DAYS = 31
SENSOR_REBUILD_SECONDS = 6 * 3600
SENSOR_REFRESH_SECONDS = 30
_SHORT_CACHE_SECONDS = 30

_base_state = {}
_base_locks = defaultdict(threading.Lock)
_base_locks_guard = threading.Lock()


def _fmt_utc(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_reading_ts(raw):
    try:
        ts = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt_timezone.utc)


def _sensor_rows_between(sensor_id, since, until=None, columns='fecha_registro,valor'):
    """Lecturas (de todas las viviendas) de un sensor desde `since` (inclusive) hasta `until`, paginando."""
    params = {
        'id_sensor': f'in.({sensor_id})',
        'fecha_registro': f'gte.{_fmt_utc(since)}',
        'order': 'fecha_registro.asc,id_lectura.asc',
        'limit': str(SENSOR_PAGE),
    }
    if until is not None:
        params['and'] = f'(fecha_registro.lt.{_fmt_utc(until)})'
    rows = []
    for page in range(SENSOR_MAX_PAGES):
        page_params = {**params, 'offset': str(page * SENSOR_PAGE)}
        try:
            chunk = supabase_client.select('lectura', columns, page_params)
        except supabase_client.SupabaseError:
            # Un corte momentáneo (timeout) no debe tirar toda la serie: se reintenta una vez.
            time.sleep(1)
            chunk = supabase_client.select('lectura', columns, page_params)
        rows.extend(chunk)
        if len(chunk) < SENSOR_PAGE:
            break
    return rows


def _add_to_buckets(sums, rows, bucket_seconds):
    for row in rows:
        ts = _parse_reading_ts(row.get('fecha_registro'))
        if ts is None or row.get('valor') is None:
            continue
        key = int(ts.timestamp() // bucket_seconds * bucket_seconds)
        total, count = sums.get(key, (0.0, 0))
        sums[key] = (total + safe_float(row.get('valor')), count + 1)


def _remember_last(state, rows):
    """Guarda la lectura más reciente vista (y los ids con esa misma hora, para no contarlos dos veces)."""
    for row in rows:
        ts = _parse_reading_ts(row.get('fecha_registro'))
        if ts is None:
            continue
        if state['last_ts'] is None or ts > state['last_ts']:
            state['last_ts'], state['last_ids'] = ts, {row.get('id_lectura')}
        elif ts == state['last_ts']:
            state['last_ids'].add(row.get('id_lectura'))


def _base_buckets(sensor_id):
    """Sumas por intervalos de 15 min de los últimos SENSOR_BASE_DAYS días, actualizadas de forma incremental."""
    with _base_locks_guard:
        lock = _base_locks[sensor_id]
    with lock:
        now = time.time()
        state = _base_state.get(sensor_id)
        columns = 'id_lectura,fecha_registro,valor'
        if state is None or now - state['built'] > SENSOR_REBUILD_SECONDS:
            until = datetime.now(dt_timezone.utc)
            since = until - timedelta(days=SENSOR_BASE_DAYS)
            bounds = [(since + timedelta(days=i), since + timedelta(days=i + 1)) for i in range(SENSOR_BASE_DAYS)]
            bounds[-1] = (bounds[-1][0], None)
            # 8 hilos: deja conexiones libres del pool para las páginas que se estén sirviendo a la vez.
            with ThreadPoolExecutor(max_workers=8) as executor:
                parts = list(executor.map(lambda b: _sensor_rows_between(sensor_id, b[0], b[1], columns), bounds))
            state = {'sums': {}, 'last_ts': None, 'last_ids': set(), 'built': now, 'checked': now}
            for part in parts:
                _add_to_buckets(state['sums'], part, SENSOR_BASE_BUCKET_SECONDS)
                _remember_last(state, part)
            _base_state[sensor_id] = state
        elif now - state['checked'] > SENSOR_REFRESH_SECONDS and state['last_ts'] is not None:
            rows = [
                row for row in _sensor_rows_between(sensor_id, state['last_ts'], None, columns)
                if not (_parse_reading_ts(row.get('fecha_registro')) == state['last_ts']
                        and row.get('id_lectura') in state['last_ids'])
            ]
            _add_to_buckets(state['sums'], rows, SENSOR_BASE_BUCKET_SECONDS)
            _remember_last(state, rows)
            state['checked'] = now
            oldest = now - SENSOR_BASE_DAYS * 86400
            for key in [key for key in state['sums'] if key < oldest]:
                del state['sums'][key]
        return dict(state['sums'])


def _build_base_in_background(sensor_id):
    with _base_locks_guard:
        lock = _base_locks[sensor_id]
    if not lock.locked():
        threading.Thread(target=_base_buckets, args=(sensor_id,), daemon=True, name=f'sensor-base-{sensor_id}').start()


def sensor_series(sensor_id, range_key):
    """{inicio_intervalo (epoch s): promedio} con las lecturas de todas las viviendas que comparten el sensor."""
    window = SENSOR_RANGES.get(range_key, SENSOR_RANGES['1d'])
    bucket_seconds = SENSOR_BUCKETS.get(range_key, SENSOR_BUCKETS['1d']).total_seconds()
    until = datetime.now(dt_timezone.utc)
    since = until - window

    if range_key == '1h' or (range_key == '1d' and sensor_id not in _base_state):
        # Rangos cortos: se consultan directo (pocas filas). Si aún no existen las sumas del mes,
        # se arman en segundo plano para que semana/mes respondan rápido después.
        if range_key == '1d':
            _build_base_in_background(sensor_id)
        cache_key = f'sensor_series:{sensor_id}:{range_key}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        chunks = 1 if range_key == '1h' else 6
        step = window / chunks
        bounds = [(since + step * i, since + step * (i + 1)) for i in range(chunks)]
        sums = {}
        for rows in run_parallel(*[(lambda b=b: _sensor_rows_between(sensor_id, *b)) for b in bounds]):
            _add_to_buckets(sums, rows, bucket_seconds)
        series = {key: round(total / count, 3) for key, (total, count) in sums.items()}
        cache.set(cache_key, series, _SHORT_CACHE_SECONDS)
        return series

    cutoff = since.timestamp()
    grouped = {}
    for key, (total, count) in _base_buckets(sensor_id).items():
        if key < cutoff:
            continue
        group = int(key // bucket_seconds * bucket_seconds)
        group_total, group_count = grouped.get(group, (0.0, 0))
        grouped[group] = (group_total + total, group_count + count)
    return {key: round(total / count, 3) for key, (total, count) in grouped.items() if count}


def warm_sensor_series():
    """Precarga las sumas de todos los sensores (se llama en segundo plano al arrancar el servidor)."""
    for sensor in sensors():
        if sensor.get('id_sensor') is not None:
            _base_buckets(str(sensor['id_sensor']))
