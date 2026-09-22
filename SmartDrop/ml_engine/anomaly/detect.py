"""Detección de anomalías en producción: independiente y más rápida que el
pipeline de forecasting multi-día (Sección 7 del spec).
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np

from ml_engine.anomaly.features import RESIDUAL_COLUMNS, build_residual_frame
from ml_engine.models import AnomalyEvent, Home, ModelArtifact

logger = logging.getLogger(__name__)

SEVERITY_THRESHOLDS = {
    'alta': -0.6,
    'media': -0.4,
    'baja': -0.2,
}


def _severity_for_score(score: float) -> str | None:
    if score <= SEVERITY_THRESHOLDS['alta']:
        return 'alta'
    if score <= SEVERITY_THRESHOLDS['media']:
        return 'media'
    if score <= SEVERITY_THRESHOLDS['baja']:
        return 'baja'
    return None


def _dominant_metric(row) -> str:
    return 'presion' if abs(row['residual_presion']) >= abs(row['residual_litros']) else 'flujo'


def run_anomaly_detection(home_ids: list[int] | None = None, lookback_rows_per_home: int = 24) -> list[AnomalyEvent]:
    artifact = ModelArtifact.objects.filter(
        kind=ModelArtifact.Kind.ANOMALY_DETECTOR, is_active=True,
    ).order_by('-trained_at').first()
    if artifact is None:
        raise ValueError('No hay un detector de anomalías entrenado. Corre `ml_train_anomaly_model`.')

    model = joblib.load(Path(artifact.file_path) / 'isolation_forest.joblib')
    frame = build_residual_frame(home_ids)
    if frame.empty:
        return []

    recent = frame.groupby('home_id').tail(lookback_rows_per_home).reset_index(drop=True)
    x = recent[RESIDUAL_COLUMNS]
    scores = model.score_samples(x)

    home_zone_lookup = dict(Home.objects.filter(id__in=recent['home_id'].unique()).values_list('id', 'zone_id'))

    events = []
    for i, row in recent.iterrows():
        severity = _severity_for_score(float(scores[i]))
        if severity is None:
            continue
        metric = _dominant_metric(row)
        events.append(AnomalyEvent(
            home_id=int(row['home_id']),
            zone_id=home_zone_lookup.get(int(row['home_id'])),
            metric=metric,
            ts=row['period_start'],
            score=float(scores[i]),
            severity=severity,
            detail={
                'residual_litros': float(row['residual_litros']),
                'residual_presion': float(row['residual_presion']),
                'residual_calidad': float(row['residual_calidad']),
            },
        ))

    if events:
        AnomalyEvent.objects.bulk_create(events, ignore_conflicts=True)
    logger.info('Detección de anomalías: %d eventos flagged de %d puntos evaluados', len(events), len(recent))
    return events
