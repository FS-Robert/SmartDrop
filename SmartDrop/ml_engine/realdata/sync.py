"""Sincroniza viviendas, tanques y lecturas reales de Supabase hacia el esquema de ml_engine.

- Cada tanque real (tabla `tanque`) es una `Zone`; cada vivienda real, un `Home` de esa zona.
- Las lecturas (`lectura`) se copian de forma incremental a `SensorReading` y se resumen por hora en
  `ConsumptionAggregate`, que es lo que consumen los modelos.
- El nivel del tanque (cm) se convierte a litros con la capacidad y la altura del tanque.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone

import pandas as pd
from django.db.models import Count
from django.utils import timezone

from App import queries, supabase_client
from ml_engine.models import ConsumptionAggregate, Home, SensorReading, SourceType, SyncState, Zone

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_PAGES_PER_SENSOR = 12
HISTORY_DAYS = 30
PARALLEL = 8
STALE_AFTER_HOURS = 6
MIN_HOURS_FOR_MODEL = 48
SYNC_OVERLAP_MINUTES = 5  # se vuelve a pedir este margen; las lecturas repetidas se descartan al guardar

SENSOR_KINDS = (
    ('flujo', 'flujo'), ('flow', 'flujo'),
    ('presion', 'presion'), ('pressure', 'presion'),
    ('nivel', 'nivel'), ('level', 'nivel'),
    ('tds', 'calidad'), ('calidad', 'calidad'),
)
METRIC_BY_KIND = {'flujo': 'flujo', 'presion': 'presion', 'nivel': 'nivel_tanque', 'calidad': 'calidad'}


def _sensor_kind(sensor):
    text = str(sensor.get('tipo_sensor') or '').lower()
    return next((kind for name, kind in SENSOR_KINDS if name in text), None)


def _parse_ts(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(dt_timezone.utc)


def _noop(percent, text):
    pass


def level_cm_to_liters(zone, value_cm):
    height = zone.altura_total_cm or 0
    if height <= 0:
        return 0.0
    return float(min(max(value_cm / height, 0.0), 1.0) * zone.capacidad_maxima_litros)


# ── Viviendas, tanques y sensores ────────────────────────────────────────────

def discover_homes():
    """Crea/actualiza Zone (por tanque) y Home (por vivienda). Devuelve (homes, omitidas)."""
    viviendas, tanques, sensores = queries.run_parallel(
        lambda: supabase_client.select('vivienda', '*', {'order': 'id_vivienda', 'limit': '1000'}),
        lambda: supabase_client.select('tanque', '*', {'limit': '1000'}),
        queries.sensors,
    )
    kind_by_sensor = {sensor['id_sensor']: _sensor_kind(sensor) for sensor in sensores}
    unit_by_sensor = {sensor['id_sensor']: sensor.get('unidad_medida') or '' for sensor in sensores}

    # Los sensores se comparten entre viviendas (un solo set por tipo): una vivienda usa un sensor
    # si tiene al menos una lectura propia de él.
    def has_readings(vivienda_id, sensor_id):
        rows = supabase_client.select(
            'lectura', 'id_lectura',
            {'id_vivienda': f'eq.{vivienda_id}', 'id_sensor': f'eq.{sensor_id}', 'limit': '1'},
        )
        return vivienda_id, sensor_id, bool(rows)

    sensor_ids = [sensor['id_sensor'] for sensor in sensores if kind_by_sensor.get(sensor['id_sensor'])]
    pairs = [(v['id_vivienda'], s) for v in viviendas for s in sensor_ids]
    sensors_of = defaultdict(list)
    for i in range(0, len(pairs), PARALLEL * 2):
        group = pairs[i:i + PARALLEL * 2]
        for vivienda_id, sensor_id, used in queries.run_parallel(
            *[(lambda v=v, s=s: has_readings(v, s)) for v, s in group]
        ):
            if used:
                sensors_of[vivienda_id].append(sensor_id)

    # Cada vivienda tiene su tanque "Tanque Principal Vivienda <id>"; si no, el único tanque de su sensor de nivel.
    tank_by_name = {str(tank.get('nombre') or '').strip().lower(): tank for tank in tanques}
    tanks_per_sensor = defaultdict(list)
    for tank in tanques:
        tanks_per_sensor[tank.get('id_sensor_nivel')].append(tank)

    def tank_for(vivienda_id, level_sensor):
        tank = tank_by_name.get(f'tanque principal vivienda {vivienda_id}')
        if tank is None and len(tanks_per_sensor.get(level_sensor, [])) == 1:
            tank = tanks_per_sensor[level_sensor][0]
        return tank

    zonas = sorted({str(v.get('zona') or '') for v in viviendas})
    homes, skipped = [], []
    for vivienda in viviendas:
        vid = vivienda['id_vivienda']
        mapping = {}
        for sensor_id in sorted(sensors_of.get(vid, [])):
            mapping.setdefault(kind_by_sensor[sensor_id], sensor_id)
        if not mapping:
            skipped.append({'nic': vivienda['nic'], 'motivo': 'sin lecturas propias'})
            continue

        tank = tank_for(vid, mapping.get('nivel'))
        zone = _upsert_zone(tank, vivienda)
        home, _ = Home.objects.update_or_create(
            source_ref_vivienda_id=vid,
            defaults={
                'zone': zone,
                'source': SourceType.REAL,
                'etiqueta': vivienda['nic'],
                'activo': True,
                'cluster_id': zonas.index(str(vivienda.get('zona') or '')),
                'meta': {
                    'nic': vivienda['nic'],
                    'direccion': vivienda.get('direccion') or '',
                    'zona': vivienda.get('zona') or '',
                    'titular': vivienda.get('nombre_completo_titular') or '',
                    'telefono': vivienda.get('telefono_titular') or '',
                    'tipo_establecimiento': vivienda.get('tipo_establecimiento') or '',
                    'sensores': mapping,
                    'unidades': {kind: unit_by_sensor.get(sid, '') for kind, sid in mapping.items()},
                    'id_tanque': tank['id_tanque'] if tank else None,
                },
            },
        )
        homes.append(home)
    return homes, skipped


def _upsert_zone(tank, vivienda):
    if tank is None:
        name = f"Sin tanque — {vivienda['nic']}"
        zone = Zone.objects.filter(source=SourceType.REAL, name=name).first() or Zone(source=SourceType.REAL, name=name)
        zone.meta = {'sin_tanque': True}
        zone.capacidad_maxima_litros = zone.nivel_critico_litros = zone.nivel_actual_litros = 0.0
        zone.save()
        return zone

    height = float(tank.get('altura_total') or 0)
    capacity = float(tank.get('capacidad_maxima_litros') or 0)
    critical_cm = float(tank.get('nivel_critico_cm') or 0)
    zone = Zone.objects.filter(source=SourceType.REAL, source_ref_tanque_id=tank['id_tanque']).first()
    # El nivel empieza en 0 hasta que llegue la primera lectura del sensor (no el valor por defecto del modelo).
    zone = zone or Zone(source=SourceType.REAL, source_ref_tanque_id=tank['id_tanque'], nivel_actual_litros=0.0)
    zone.name = tank.get('nombre') or f"Tanque {tank['id_tanque']}"
    zone.capacidad_maxima_litros = capacity
    zone.altura_total_cm = height
    zone.nivel_critico_litros = capacity * critical_cm / height if height else 0.0
    zone.forma_geometrica = tank.get('forma_geometrica') or 'cilindrico'
    zone.diametro_cm = tank.get('diametro')
    zone.largo_cm = tank.get('largo')
    zone.ancho_cm = tank.get('ancho')
    zone.meta = {
        'id_sensor_nivel': tank.get('id_sensor_nivel'),
        'nivel_critico_cm': critical_cm,
        'nivel_minimo_cm': tank.get('nivel_minimo_cm'),
        'estado': tank.get('estado'),
    }
    zone.save()
    return zone


# ── Lecturas ─────────────────────────────────────────────────────────────────

def _fetch_sensor_rows(vivienda_id, sensor_id, last_ts):
    base = {'id_vivienda': f'eq.{vivienda_id}', 'id_sensor': f'eq.{sensor_id}', 'limit': str(PAGE_SIZE)}
    rows = []
    if last_ts is None:
        since = (timezone.now() - timedelta(days=HISTORY_DAYS)).astimezone(dt_timezone.utc)
        for page in range(MAX_PAGES_PER_SENSOR):
            chunk = supabase_client.select('lectura', 'valor,fecha_registro', {
                **base, 'fecha_registro': f'gte.{since.isoformat()}',
                'order': 'fecha_registro.desc', 'offset': str(page * PAGE_SIZE),
            })
            rows.extend(chunk)
            if len(chunk) < PAGE_SIZE:
                break
        return rows

    cursor = last_ts - timedelta(minutes=SYNC_OVERLAP_MINUTES)
    for _ in range(MAX_PAGES_PER_SENSOR):
        chunk = supabase_client.select('lectura', 'valor,fecha_registro', {
            **base, 'fecha_registro': f'gt.{cursor.isoformat()}', 'order': 'fecha_registro.asc',
        })
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        cursor = _parse_ts(chunk[-1]['fecha_registro'])
    return rows


def sync_readings(homes, progress=_noop, progress_range=(0, 100)):
    """Copia a ml_engine las lecturas nuevas de cada hogar y actualiza agregados horarios y nivel del tanque."""
    low, high = progress_range
    states = {state.home_id: state.last_reading_ts for state in SyncState.objects.filter(home__in=homes)}
    tasks = []
    for home in homes:
        for kind, sensor_id in home.meta.get('sensores', {}).items():
            tasks.append((home, kind, sensor_id))

    fetched = defaultdict(list)
    for i in range(0, len(tasks), PARALLEL):
        group = tasks[i:i + PARALLEL]
        results = queries.run_parallel(*[
            (lambda t=t: _fetch_sensor_rows(t[0].source_ref_vivienda_id, t[2], states.get(t[0].id)))
            for t in group
        ])
        for (home, kind, _), rows in zip(group, results):
            fetched[home.id].extend((kind, row) for row in rows)
        progress(low + (high - low) * 0.7 * min(i + PARALLEL, len(tasks)) / max(len(tasks), 1), 'Descargando lecturas de Supabase')

    new_rows = 0
    for index, home in enumerate(homes):
        new_rows += _store_home_rows(home, fetched.get(home.id, []))
        progress(low + (high - low) * (0.7 + 0.3 * (index + 1) / max(len(homes), 1)), 'Calculando agregados por hora')
    logger.info('Sincronización: %d homes, %d lecturas nuevas', len(homes), new_rows)
    return new_rows


def _store_home_rows(home, items):
    if not items:
        return 0
    zone = home.zone
    parsed = []
    for kind, row in items:
        try:
            parsed.append((METRIC_BY_KIND[kind], _parse_ts(row['fecha_registro']), float(row['valor'])))
        except (KeyError, TypeError, ValueError):
            continue
    if not parsed:
        return 0

    earliest = min(ts for _, ts, _ in parsed)
    existing = set(SensorReading.objects.filter(home=home, ts__gte=earliest).values_list('metric', 'ts'))
    readings = []
    for metric, ts, value in parsed:
        if (metric, ts) in existing:
            continue
        existing.add((metric, ts))
        is_level = metric == 'nivel_tanque'
        readings.append(SensorReading(
            home=home, zone=zone if is_level else None, metric=metric, ts=ts,
            value=level_cm_to_liters(zone, value) if is_level else value,
        ))
    if not readings:
        return 0
    SensorReading.objects.bulk_create(readings, batch_size=5000)

    latest = max(reading.ts for reading in readings)
    SyncState.objects.update_or_create(home=home, defaults={'last_reading_ts': latest})
    _rebuild_hourly(home, earliest)
    _update_zone_level(zone)
    return len(readings)


def _rebuild_hourly(home, since):
    start = pd.Timestamp(since).floor('h').to_pydatetime()
    frame = pd.DataFrame.from_records(
        SensorReading.objects.filter(home=home, ts__gte=start, is_valid=True).values('metric', 'ts', 'value')
    )
    if frame.empty:
        return
    frame['ts'] = pd.to_datetime(frame['ts'], utc=True)
    frame['hour'] = frame['ts'].dt.floor('h')
    pivot = frame.pivot_table(index='hour', columns='metric', values='value', aggfunc='mean')
    rows = []
    for hour, values in pivot.iterrows():
        flow = values.get('flujo')
        if pd.isna(flow):
            continue
        pressure, quality = values.get('presion'), values.get('calidad')
        rows.append(ConsumptionAggregate(
            home=home, period=ConsumptionAggregate.Period.HOURLY, period_start=hour.to_pydatetime(),
            litros=float(flow) * 60.0,  # L/min medio de la hora -> litros de esa hora
            presion_media=None if pd.isna(pressure) else float(pressure),
            calidad_media=None if pd.isna(quality) else float(quality),
        ))
    ConsumptionAggregate.objects.bulk_create(
        rows, batch_size=2000, update_conflicts=True,
        unique_fields=['home', 'period', 'period_start'],
        update_fields=['litros', 'presion_media', 'calidad_media'],
    )


def _update_zone_level(zone):
    latest = SensorReading.objects.filter(zone=zone, metric='nivel_tanque').order_by('-ts').first()
    if latest is not None:
        zone.nivel_actual_litros = latest.value
        zone.save(update_fields=['nivel_actual_litros', 'actualizado_en'])


# ── Calidad de datos y orquestación ──────────────────────────────────────────

def data_quality(home):
    """Estado de los datos de un hogar: ok | sin_datos | sin_lecturas_recientes | plana | insuficiente."""
    now = timezone.now()
    last = SensorReading.objects.filter(home=home).order_by('-ts').first()
    if last is None:
        return {'status': 'sin_datos', 'detail': 'No hay lecturas sincronizadas.'}
    age_hours = (now - last.ts).total_seconds() / 3600
    result = {'last_reading': last.ts.isoformat(), 'age_hours': round(age_hours, 1)}
    if age_hours > STALE_AFTER_HOURS:
        return {**result, 'status': 'sin_lecturas_recientes',
                'detail': f'La última lectura tiene {age_hours:.0f} h; el sensor no está reportando.'}

    # Valores distintos por métrica en la última semana, contados en la base (sin cargar las lecturas).
    distinct = SensorReading.objects.filter(
        home=home, ts__gte=now - timedelta(days=7), metric__in=['flujo', 'presion', 'nivel_tanque'],
    ).values('metric').annotate(n=Count('value', distinct=True)).values_list('n', flat=True)
    distinct = list(distinct)
    if distinct and all(n <= 1 for n in distinct):
        return {**result, 'status': 'plana',
                'detail': 'Las lecturas de la última semana no varían (valor constante): no hay patrón que analizar.'}

    hours = ConsumptionAggregate.objects.filter(home=home, period='hourly').count()
    if hours < MIN_HOURS_FOR_MODEL:
        return {**result, 'status': 'insuficiente', 'hours': hours,
                'detail': f'Solo hay {hours} h de historial (mínimo {MIN_HOURS_FOR_MODEL}).'}
    return {**result, 'status': 'ok', 'hours': hours}


def sync_all(progress=_noop, progress_range=(0, 100)):
    """Descubre viviendas/tanques y sincroniza sus lecturas. Devuelve un resumen."""
    low, high = progress_range
    progress(low, 'Leyendo viviendas y tanques de Supabase')
    homes, skipped = discover_homes()
    new_rows = sync_readings(homes, progress, (low + (high - low) * 0.1, high))
    return {'homes': len(homes), 'omitidas': skipped, 'lecturas_nuevas': new_rows}
