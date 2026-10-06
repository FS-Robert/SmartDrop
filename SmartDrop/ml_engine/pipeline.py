"""Orquestación del ML sobre datos reales: botón 'Realizar predicciones' y ciclo automático de fugas."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from ml_engine.leaks.detect import run_leak_analysis
from ml_engine.models import ConsumptionForecast, Home, LeakPrediction, MonitorState, ShortagePrediction, Zone
from ml_engine.realdata import sync
from ml_engine.shortage.tank_simulation import run_shortage_prediction
from ml_engine.supabase_out import save_shortage_prediction
from ml_engine.training import ModelsUnavailable, ensure_models, modeling_homes

logger = logging.getLogger(__name__)

LOCK_KEY = 'pipeline'
LOCK_STALE_MINUTES = 20
DISCOVERY_KEY = 'discovery'
DISCOVERY_MAX_AGE_HOURS = 6


class PipelineBusy(Exception):
    pass


def _try_acquire(min_gap_seconds=0):
    """Cerrojo entre procesos/hilos: un solo pipeline a la vez (se libera aunque el proceso muera, a los 20 min)."""
    now = timezone.now()
    MonitorState.objects.get_or_create(key=LOCK_KEY)
    cond = Q(last_started_at__isnull=True) | Q(last_finished_at__gte=F('last_started_at')) | Q(
        last_started_at__lt=now - timedelta(minutes=LOCK_STALE_MINUTES))
    if min_gap_seconds:
        cond &= Q(last_finished_at__isnull=True) | Q(last_finished_at__lt=now - timedelta(seconds=min_gap_seconds))
    return MonitorState.objects.filter(cond, key=LOCK_KEY).update(last_started_at=now) == 1


@contextmanager
def pipeline_lock(wait_seconds=0, min_gap_seconds=0):
    deadline = time.monotonic() + wait_seconds
    while not _try_acquire(min_gap_seconds):
        if time.monotonic() >= deadline:
            raise PipelineBusy()
        time.sleep(2)
    try:
        yield
    finally:
        MonitorState.objects.filter(key=LOCK_KEY).update(last_finished_at=timezone.now())


def _discovery_is_stale():
    if not Home.objects.filter(source='real').exists():
        return True
    state = MonitorState.objects.filter(key=DISCOVERY_KEY).first()
    return state is None or state.last_finished_at is None or \
        state.last_finished_at < timezone.now() - timedelta(hours=DISCOVERY_MAX_AGE_HOURS)


def _sync(progress, progress_range, force_discovery=False):
    if force_discovery or _discovery_is_stale():
        summary = sync.sync_all(progress, progress_range)
        MonitorState.objects.update_or_create(key=DISCOVERY_KEY, defaults={'last_finished_at': timezone.now()})
        return summary
    homes = list(Home.objects.filter(source='real', activo=True).select_related('zone'))
    new_rows = sync.sync_readings(homes, progress, progress_range)
    return {'homes': len(homes), 'omitidas': [], 'lecturas_nuevas': new_rows}


def run_full_prediction(progress=None):
    """Sincroniza Supabase, asegura modelos, predice desabasto por tanque y analiza fugas."""
    progress = progress or (lambda percent, text: None)
    started = time.monotonic()
    summary = {}

    summary['sync'] = _sync(progress, (0, 40), force_discovery=True)

    progress(40, 'Comprobando modelos de ML')
    try:
        summary['modelos'] = ensure_models(progress=lambda p, t: progress(40 + p * 0.15, t))
    except ModelsUnavailable as exc:
        summary['modelos'] = {'trained': False, 'error': str(exc)}
        summary['tanques'], summary['fugas'] = [], {'analizados': 0, 'alertas_creadas': 0, 'posibles_fugas': 0, 'excluidos': []}
        summary['duracion_s'] = round(time.monotonic() - started, 1)
        return summary

    homes, excluded = modeling_homes()
    # Una vivienda sin tanque registrado no tiene nivel que predecir.
    zones = Zone.objects.filter(
        id__in={home.zone_id for home in homes}, source_ref_tanque_id__isnull=False,
    ).order_by('id')
    tanks = []
    total = max(zones.count(), 1)
    for index, zone in enumerate(zones):
        progress(55 + 30.0 * index / total, f'Prediciendo desabasto: {zone.name}')
        try:
            prediction = run_shortage_prediction(zone.id)
        except Exception:
            logger.exception('Falló la predicción de desabasto de la zona %s', zone.pk)
            continue
        try:
            save_shortage_prediction(zone, prediction)
        except Exception:
            logger.exception('No se pudo guardar la predicción de la zona %s en Supabase', zone.pk)
        tanks.append({
            'zone_id': zone.id, 'tanque': zone.name, 'nivel_riesgo': prediction.nivel_riesgo,
            'horas_hasta_desabasto': prediction.median_hours_to_shortage,
            'probabilidad': round(prediction.probabilidad_desabasto_horizonte, 3),
        })
    summary['tanques'] = tanks

    summary['fugas'] = run_leak_analysis(
        homes=homes, progress=lambda p, t: progress(85 + p * 0.15, t),
    )
    summary['fugas']['excluidos'] = excluded + [e for e in summary['fugas']['excluidos'] if e not in excluded]
    summary['duracion_s'] = round(time.monotonic() - started, 1)
    progress(100, 'Listo')
    return summary


RETENTION_DAYS = 7


def prune_old_results():
    """El monitor evalúa cada pocos minutos: se conservan 7 días de predicciones (y las que generaron alerta)."""
    limit = timezone.now() - timedelta(days=RETENTION_DAYS)
    LeakPrediction.objects.filter(generated_at__lt=limit, alerta_id__isnull=True).delete()
    ConsumptionForecast.objects.filter(generated_at__lt=limit).delete()
    ShortagePrediction.objects.filter(generated_at__lt=limit).delete()


def run_monitor_cycle():
    """Ciclo automático: lecturas nuevas -> (reentrena si hace falta) -> análisis de fugas con alertas."""
    prune_old_results()
    summary = {'sync': _sync(lambda p, t: None, (0, 100))}
    try:
        summary['modelos'] = ensure_models()
    except ModelsUnavailable as exc:
        summary['modelos'] = {'trained': False, 'error': str(exc)}
    summary['fugas'] = run_leak_analysis(create_alerts=True)
    summary['fugas'].pop('resultados', None)
    return summary
