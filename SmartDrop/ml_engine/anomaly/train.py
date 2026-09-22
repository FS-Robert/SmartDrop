"""Entrenamiento del detector de anomalías (Isolation Forest), Sección 7.

Entrena sobre residuales normales de presión/flujo/calidad. No requiere
etiquetas: aprende la forma "normal" de los residuales y luego marca como
anómalo lo que se aleja de ella (reconstruction/isolation score).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import joblib
from django.conf import settings
from sklearn.ensemble import IsolationForest

from ml_engine.anomaly.features import RESIDUAL_COLUMNS, build_residual_frame
from ml_engine.models import ModelArtifact
logger = logging.getLogger(__name__)

MODEL_DIR = Path(getattr(settings, 'BASE_DIR', Path('.'))) / 'ml_models' / 'anomaly'


def train_anomaly_model(home_ids: list[int] | None = None, contamination: float = 0.02) -> ModelArtifact:
    frame = build_residual_frame(home_ids)
    if frame.empty or len(frame) < 200:
        raise ValueError(
            'No hay suficiente historial para entrenar el detector de anomalías. '
            'Corre primero `python manage.py ml_generate_synthetic_data`.'
        )

    x = frame[RESIDUAL_COLUMNS]
    model = IsolationForest(
        n_estimators=200, contamination=contamination, random_state=42, n_jobs=-1,
    )
    model.fit(x)

    scores = model.score_samples(x)
    sorted_scores = sorted(scores)
    version = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    out_dir = MODEL_DIR / version
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / 'isolation_forest.joblib')

    metrics = {
        'n_train': len(frame),
        'score_mean': float(scores.mean()),
        'score_p01': float(sorted_scores[max(0, int(len(sorted_scores) * 0.01) - 1)]),
        'contamination': contamination,
    }
    (out_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2))

    ModelArtifact.objects.filter(kind=ModelArtifact.Kind.ANOMALY_DETECTOR, is_active=True).update(is_active=False)
    artifact = ModelArtifact.objects.create(
        kind=ModelArtifact.Kind.ANOMALY_DETECTOR,
        version=version,
        file_path=str(out_dir),
        metrics=metrics,
        feature_names=RESIDUAL_COLUMNS,
        is_active=True,
    )
    logger.info('Detector de anomalías entrenado: %s metrics=%s', version, metrics)
    return artifact
