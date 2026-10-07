"""Predicción de fugas por hogar a partir de lecturas reales (flujo, presión, nivel).

Combina señales físicas con el detector de anomalías (Isolation Forest) entrenado con los datos reales:

- *Flujo mínimo sostenido*: en una vivienda normal el flujo cae a ~0 en algún momento de cada tramo de 6 h; una
  fuga mantiene un flujo mínimo por encima de lo habitual (es la señal más fiable).
- *Caída de presión* respecto de su comportamiento normal de los últimos 7 días.
- *Flujo inusual para la hora* del día.
- *Score del Isolation Forest* sobre los residuales recientes.

Cada señal se normaliza a 0–1 y se combinan como probabilidad de "al menos una causa" ponderada.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from django.conf import settings
from django.utils import timezone

from ml_engine.anomaly.features import RESIDUAL_COLUMNS, build_residual_frame
from ml_engine.leaks.explain import describe_causes
from ml_engine.models import ConsumptionAggregate, LeakPrediction, ModelArtifact, SensorReading

logger = logging.getLogger(__name__)

BASELINE_DAYS = 7
FLOOR_WINDOW_HOURS = 6
PRESSURE_WINDOW_HOURS = 3
FLOW_WINDOW_HOURS = 3
MIN_BASELINE_HOURS = 48
MIN_FLOOR_EXCESS_LPM = 0.03
WEIGHTS = {'floor': 0.85, 'pressure': 0.6, 'flow': 0.35, 'iforest': 0.45}
HORIZON_HOURS = 12


def alert_threshold():
    return float(getattr(settings, 'ML_LEAK_ALERT_THRESHOLD', 0.6))


def _sigmoid(x):
    return float(1.0 / (1.0 + np.exp(-x)))


def risk_level(probability):
    if probability >= 0.75:
        return 'alto'
    if probability >= 0.5:
        return 'medio'
    return 'bajo'


@dataclass
class DetectorModel:
    model: object
    artifact: ModelArtifact
    score_mean: float
    score_p01: float


def load_detector():
    artifact = ModelArtifact.objects.filter(
        kind=ModelArtifact.Kind.ANOMALY_DETECTOR, is_active=True,
    ).order_by('-trained_at').first()
    if artifact is None:
        return None
    model = joblib.load(Path(artifact.file_path) / 'isolation_forest.joblib')
    metrics = artifact.metrics or {}
    return DetectorModel(model, artifact, float(metrics.get('score_mean', -0.45)), float(metrics.get('score_p01', -0.65)))


def _hourly_frame(home, now):
    rows = ConsumptionAggregate.objects.filter(
        home=home, period='hourly', period_start__gte=now - timedelta(days=BASELINE_DAYS + 1),
    ).order_by('period_start').values('period_start', 'litros', 'presion_media')
    frame = pd.DataFrame.from_records(rows)
    if frame.empty:
        return frame
    frame['period_start'] = pd.to_datetime(frame['period_start'], utc=True)
    frame = frame.set_index('period_start').sort_index()
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq='h'))
    frame['flow'] = frame['litros'] / 60.0  # L/min medio de la hora
    return frame


def _iforest_signal(detector, home):
    if detector is None:
        return 0.0, None
    frame = build_residual_frame([home.id])
    if frame.empty:
        return 0.0, None
    recent = frame.sort_values('period_start').tail(3)
    score = float(detector.model.score_samples(recent[RESIDUAL_COLUMNS]).mean())
    span = max(detector.score_mean - detector.score_p01, 1e-6)
    return float(np.clip((detector.score_mean - score) / span, 0.0, 1.0)), score


def evaluate_home(home, detector=None, now=None):
    """Calcula señales y probabilidad de fuga de un hogar. Devuelve None si no hay datos suficientes."""
    now = now or timezone.now()
    frame = _hourly_frame(home, now)
    if frame.empty or frame['flow'].notna().sum() < MIN_BASELINE_HOURS:
        return None

    flow = frame['flow']
    pressure = frame['presion_media']
    last_hour = flow.dropna().index.max()
    recent_floor = flow[flow.index > last_hour - pd.Timedelta(hours=FLOOR_WINDOW_HOURS)].dropna()
    if len(recent_floor) < FLOOR_WINDOW_HOURS - 2:
        return None
    baseline_end = last_hour - pd.Timedelta(hours=FLOOR_WINDOW_HOURS)
    baseline_flow = flow[flow.index <= baseline_end].dropna()
    if len(baseline_flow) < MIN_BASELINE_HOURS:
        return None

    # 1) Flujo mínimo sostenido frente a lo habitual.
    floor_now = float(recent_floor.min())
    baseline_floors = baseline_flow.rolling(FLOOR_WINDOW_HOURS, min_periods=FLOOR_WINDOW_HOURS - 2).min().dropna()
    floor_mean = float(baseline_floors.mean())
    floor_std = max(float(baseline_floors.std() or 0.0), 0.01)
    floor_excess = floor_now - floor_mean
    z_floor = floor_excess / floor_std
    s_floor = _sigmoid((z_floor - 4.0) / 1.2) if floor_excess >= MIN_FLOOR_EXCESS_LPM else 0.0

    # 2) Caída de presión.
    recent_pressure = pressure[pressure.index > last_hour - pd.Timedelta(hours=PRESSURE_WINDOW_HOURS)].dropna()
    baseline_pressure = pressure[pressure.index <= last_hour - pd.Timedelta(hours=PRESSURE_WINDOW_HOURS)].dropna()
    pressure_now = pressure_base = pressure_drop = None
    s_pressure = 0.0
    if len(recent_pressure) >= 2 and len(baseline_pressure) >= MIN_BASELINE_HOURS:
        pressure_now = float(recent_pressure.mean())
        pressure_base = float(baseline_pressure.mean())
        pressure_std = max(float(baseline_pressure.std() or 0.0), 0.03 * abs(pressure_base), 1e-3)
        pressure_drop = pressure_base - pressure_now
        s_pressure = _sigmoid((pressure_drop / pressure_std - 3.0) / 1.0) if pressure_drop > 0 else 0.0

    # 3) Flujo inusual para esa hora del día.
    recent_flow = flow[flow.index > last_hour - pd.Timedelta(hours=FLOW_WINDOW_HOURS)].dropna()
    s_flow = 0.0
    if len(recent_flow):
        by_hour = baseline_flow.groupby(baseline_flow.index.hour)
        means, stds = by_hour.mean(), by_hour.std().fillna(0.0)
        zs = []
        for stamp, value in recent_flow.items():
            mean = float(means.get(stamp.hour, baseline_flow.mean()))
            std = max(float(stds.get(stamp.hour, 0.0)), 0.02)
            zs.append((float(value) - mean) / std)
        s_flow = _sigmoid((float(np.mean(zs)) - 3.0) / 1.5)

    # 4) Isolation Forest.
    s_iforest, iforest_score = _iforest_signal(detector, home)

    signals = {'floor': s_floor, 'pressure': s_pressure, 'flow': s_flow, 'iforest': s_iforest}
    probability = 1.0
    for name, score in signals.items():
        probability *= 1.0 - WEIGHTS[name] * score
    probability = float(np.clip(1.0 - probability, 0.0, 1.0))

    onset, lost_liters = _estimate_onset(flow, last_hour, floor_mean, floor_std)

    zone = home.zone
    level = SensorReading.objects.filter(zone=zone, metric='nivel_tanque').order_by('-ts').first()
    units = (home.meta or {}).get('unidades', {})
    features = {
        'flujo_minimo_6h_lpm': round(floor_now, 4),
        'flujo_minimo_normal_lpm': round(floor_mean, 4),
        'flujo_exceso_lpm': round(max(floor_excess, 0.0), 4),
        'perdida_estimada_lph': round(max(floor_excess, 0.0) * 60.0, 3),
        'litros_perdidos_estimados': round(lost_liters, 2),
        'inicio_estimado': onset.isoformat() if onset is not None else None,
        'presion_actual': None if pressure_now is None else round(pressure_now, 3),
        'presion_normal': None if pressure_base is None else round(pressure_base, 3),
        'caida_presion': None if pressure_drop is None else round(pressure_drop, 3),
        'unidad_presion': units.get('presion', ''),
        'score_isolation_forest': None if iforest_score is None else round(iforest_score, 4),
        'senales': {name: round(score, 3) for name, score in signals.items()},
        'nivel_tanque_litros': None if level is None else round(level.value, 3),
        'nivel_tanque_pct': None if level is None or not zone.capacidad_maxima_litros
        else round(100.0 * level.value / zone.capacidad_maxima_litros, 1),
        'capacidad_tanque_litros': zone.capacidad_maxima_litros,
        'flujo_reciente_lpm': round(float(recent_flow.mean()), 4) if len(recent_flow) else None,
    }
    drivers = describe_causes(features)
    return {
        'probability': probability,
        'drivers': drivers,
        'features': features,
        'evaluated_until': last_hour.to_pydatetime(),
        'method': 'hybrid' if detector is not None else 'heuristic',
    }


def _estimate_onset(flow, last_hour, floor_mean, floor_std):
    """Desde cuándo el flujo no baja del mínimo normal (inicio estimado) y litros perdidos desde entonces."""
    threshold = floor_mean + max(3 * floor_std, MIN_FLOOR_EXCESS_LPM)
    onset = None
    lost = 0.0
    stamp = last_hour
    for _ in range(24):
        value = flow.get(stamp)
        if value is None or pd.isna(value) or value < threshold:
            break
        onset = stamp
        lost += max(float(value) - floor_mean, 0.0) * 60.0
        stamp -= pd.Timedelta(hours=1)
    return (onset.to_pydatetime() if onset is not None else None), lost


def run_leak_analysis(homes=None, progress=None, create_alerts=True):
    """Analiza los hogares con datos aptos, guarda LeakPrediction y avisa de las posibles fugas.

    Devuelve un resumen con una fila por hogar analizado.
    """
    from ml_engine.leaks.alerts import raise_leak_alert, should_alert
    from ml_engine.training import modeling_homes

    progress = progress or (lambda percent, text: None)
    excluded = []
    if homes is None:
        homes, excluded = modeling_homes()
    detector = load_detector()
    threshold = alert_threshold()
    now = timezone.now()

    results = []
    for index, home in enumerate(homes):
        progress(100.0 * index / max(len(homes), 1), f'Analizando fugas: {home.etiqueta}')
        try:
            evaluation = evaluate_home(home, detector, now)
        except Exception:
            logger.exception('Falló el análisis de fugas del hogar %s', home.pk)
            continue
        if evaluation is None:
            excluded.append({'home_id': home.id, 'nic': home.etiqueta, 'status': 'insuficiente',
                             'detail': 'Historial insuficiente para analizar fugas.'})
            continue
        prediction = LeakPrediction.objects.create(
            home=home, zone=home.zone, horizonte_horas=HORIZON_HOURS,
            probabilidad=evaluation['probability'], nivel_riesgo=risk_level(evaluation['probability']),
            method=evaluation['method'], drivers=evaluation['drivers'], features=evaluation['features'],
            evaluated_until=evaluation['evaluated_until'],
            model_artifact=detector.artifact if detector else None,
        )
        alerted = False
        if create_alerts and prediction.probabilidad >= threshold and should_alert(home, now):
            alerted = raise_leak_alert(home, prediction)
        results.append({
            'home_id': home.id, 'nic': home.etiqueta, 'probabilidad': round(prediction.probabilidad, 3),
            'nivel_riesgo': prediction.nivel_riesgo, 'alerta_creada': alerted,
        })
    progress(100, 'Análisis de fugas terminado')
    return {
        'analizados': len(results),
        'posibles_fugas': sum(1 for r in results if r['probabilidad'] >= threshold),
        'alertas_creadas': sum(1 for r in results if r['alerta_creada']),
        'excluidos': excluded,
        'resultados': results,
    }
