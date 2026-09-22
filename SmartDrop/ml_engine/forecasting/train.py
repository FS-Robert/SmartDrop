"""Entrenamiento del modelo global de consumo (Sección 5 del spec).

Baseline: LightGBM con regresión cuantílica (p10/p50/p90), entrenado de
forma global sobre todos los hogares (no un modelo por hogar), usando
`home_id`/`cluster_id` como features categóricas para que el modelo global
se ajuste por hogar. Split temporal (nunca aleatorio) para validación.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from django.conf import settings

from ml_engine.features.build_features import CATEGORICAL_COLUMNS, FEATURE_COLUMNS, build_training_frame
from ml_engine.models import ModelArtifact

logger = logging.getLogger(__name__)

QUANTILES = (0.1, 0.5, 0.9)
MODEL_DIR = Path(getattr(settings, 'BASE_DIR', Path('.'))) / 'ml_models' / 'consumption'


def _time_split(df: pd.DataFrame, valid_fraction: float = 0.15):
    df = df.sort_values('period_start')
    cutoff_idx = int(len(df) * (1 - valid_fraction))
    cutoff_ts = df['period_start'].iloc[cutoff_idx]
    train = df[df['period_start'] < cutoff_ts]
    valid = df[df['period_start'] >= cutoff_ts]
    return train, valid


def _pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def train_consumption_model(home_ids: list[int] | None = None) -> ModelArtifact:
    import lightgbm as lgb

    frame = build_training_frame(home_ids)
    if frame.empty or len(frame) < 200:
        raise ValueError(
            'No hay suficiente historial de consumo para entrenar. '
            'Corre primero `python manage.py ml_generate_synthetic_data`.'
        )

    train_df, valid_df = _time_split(frame)
    x_train, y_train = train_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS], train_df['y']
    x_valid, y_valid = valid_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS], valid_df['y']

    boosters = {}
    metrics = {}
    feature_importance = {}
    for q in QUANTILES:
        train_set = lgb.Dataset(x_train, label=y_train, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False)
        valid_set = lgb.Dataset(x_valid, label=y_valid, categorical_feature=CATEGORICAL_COLUMNS, reference=train_set, free_raw_data=False)
        params = {
            'objective': 'quantile',
            'alpha': q,
            'metric': 'quantile',
            'learning_rate': 0.05,
            'num_leaves': 31,
            'min_data_in_leaf': 20,
            'verbosity': -1,
        }
        booster = lgb.train(
            params, train_set, num_boost_round=400,
            valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
        )
        preds = booster.predict(x_valid, num_iteration=booster.best_iteration)
        metrics[f'pinball_p{int(q * 100)}'] = _pinball_loss(y_valid.to_numpy(), preds, q)
        feature_importance[f'p{int(q * 100)}'] = dict(zip(
            booster.feature_name(), [int(v) for v in booster.feature_importance(importance_type='gain')],
        ))
        boosters[q] = booster

    # Cobertura empírica: ¿el intervalo p10-p90 realmente cubre ~80% de los casos?
    p10_preds = boosters[0.1].predict(x_valid)
    p90_preds = boosters[0.9].predict(x_valid)
    coverage = float(np.mean((y_valid.to_numpy() >= p10_preds) & (y_valid.to_numpy() <= p90_preds)))
    metrics['coverage_p10_p90'] = coverage
    metrics['n_train'] = len(train_df)
    metrics['n_valid'] = len(valid_df)

    version = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    out_dir = MODEL_DIR / version
    out_dir.mkdir(parents=True, exist_ok=True)
    for q, booster in boosters.items():
        joblib.dump(booster, out_dir / f'lgbm_q{int(q * 100)}.joblib')
    (out_dir / 'feature_importance.json').write_text(json.dumps(feature_importance, indent=2))

    ModelArtifact.objects.filter(kind=ModelArtifact.Kind.CONSUMPTION_QUANTILE, is_active=True).update(is_active=False)
    artifact = ModelArtifact.objects.create(
        kind=ModelArtifact.Kind.CONSUMPTION_QUANTILE,
        version=version,
        file_path=str(out_dir),
        metrics=metrics,
        feature_names=FEATURE_COLUMNS + CATEGORICAL_COLUMNS,
        is_active=True,
    )
    logger.info('Modelo de consumo entrenado: %s metrics=%s', version, metrics)
    return artifact
