"""Inferencia del modelo de consumo: carga el artefacto activo y predice
p10/p50/p90 para el siguiente periodo, con explicabilidad opcional (SHAP).
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ml_engine.features.build_features import CATEGORICAL_COLUMNS, FEATURE_COLUMNS, build_inference_frame
from ml_engine.models import ConsumptionForecast, Home, ModelArtifact

logger = logging.getLogger(__name__)

try:
    import shap
    _SHAP_AVAILABLE = True
except ImportError:  
    _SHAP_AVAILABLE = False


def _load_active_artifact() -> ModelArtifact:
    artifact = ModelArtifact.objects.filter(
        kind=ModelArtifact.Kind.CONSUMPTION_QUANTILE, is_active=True,
    ).order_by('-trained_at').first()
    if artifact is None:
        raise ValueError('No hay un modelo de consumo entrenado. Corre `ml_train_consumption_model`.')
    return artifact


def _load_boosters(artifact: ModelArtifact) -> dict:
    out_dir = Path(artifact.file_path)
    return {
        0.1: joblib.load(out_dir / 'lgbm_q10.joblib'),
        0.5: joblib.load(out_dir / 'lgbm_q50.joblib'),
        0.9: joblib.load(out_dir / 'lgbm_q90.joblib'),
    }


def _explain_row(booster, row: pd.DataFrame) -> dict:
    if not _SHAP_AVAILABLE:
        return {}
    try:
        explainer = shap.TreeExplainer(booster)
        shap_values = explainer.shap_values(row)
        contributions = dict(zip(row.columns, [float(v) for v in np.ravel(shap_values)]))
        top = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        return {'top_features': [{'feature': f, 'shap_value': v} for f, v in top]}
    except Exception:  # pragma: no cover - explicabilidad best-effort
        logger.exception('SHAP explanation failed')
        return {}


def predict_consumption_for_homes(home_ids: list[int] | None = None) -> list[ConsumptionForecast]:
    if home_ids is None:
        home_ids = list(Home.objects.filter(activo=True).values_list('id', flat=True))
    if not home_ids:
        return []

    artifact = _load_active_artifact()
    boosters = _load_boosters(artifact)
    frame = build_inference_frame(home_ids)
    if frame.empty:
        return []

    feature_cols = FEATURE_COLUMNS + CATEGORICAL_COLUMNS
    x = frame[feature_cols]

    preds = {q: boosters[q].predict(x) for q in (0.1, 0.5, 0.9)}
    stacked = np.vstack([preds[0.1], preds[0.5], preds[0.9]])
    stacked.sort(axis=0)  # garantiza monotonicidad p10 <= p50 <= p90
    p10, p50, p90 = stacked[0], stacked[1], stacked[2]

    results = []
    for i, (_, frame_row) in enumerate(frame.iterrows()):
        explanation = _explain_row(boosters[0.5], x.iloc[[i]])
        forecast = ConsumptionForecast.objects.create(
            home_id=int(frame_row['home_id']),
            target_ts=frame_row['period_start'],
            horizon='hourly',
            p10=max(0.0, float(p10[i])),
            p50=max(0.0, float(p50[i])),
            p90=max(0.0, float(p90[i])),
            model_artifact=artifact,
            explanation=explanation,
        )
        results.append(forecast)
    return results
