"""Gestión de los modelos de ML sobre datos reales: entrena solo si hace falta."""
from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ml_engine.anomaly.train import train_anomaly_model
from ml_engine.forecasting.train import train_consumption_model
from ml_engine.models import Home, ModelArtifact
from ml_engine.realdata.sync import data_quality

logger = logging.getLogger(__name__)

MODEL_MAX_AGE_DAYS = 7
# Versiones inactivas que se conservan por tipo (para poder volver atrás); el resto se borra del disco.
KEEP_INACTIVE_VERSIONS = 1
MODEL_SUBDIRS = {
    ModelArtifact.Kind.CONSUMPTION_QUANTILE: 'consumption',
    ModelArtifact.Kind.ANOMALY_DETECTOR: 'anomaly',
}


class ModelsUnavailable(Exception):
    """No hay datos suficientes para entrenar o usar los modelos."""


def modeling_homes():
    """Hogares reales con datos aptos (con variación, recientes y con historial suficiente)."""
    homes, excluded = [], []
    for home in Home.objects.filter(activo=True, source='real').select_related('zone').order_by('id'):
        quality = data_quality(home)
        if quality['status'] == 'ok':
            homes.append(home)
        else:
            excluded.append({'home_id': home.id, 'nic': home.etiqueta, **quality})
    return homes, excluded


def _stale(kind):
    artifact = ModelArtifact.objects.filter(kind=kind, is_active=True).order_by('-trained_at').first()
    if artifact is None:
        return True, None
    return artifact.trained_at < timezone.now() - timedelta(days=MODEL_MAX_AGE_DAYS), artifact


def ensure_models(force=False, progress=None):
    """Entrena con los datos reales de Supabase si no hay modelo o tiene más de 7 días.

    Devuelve {'trained': bool, ...}. Lanza ModelsUnavailable si no hay hogares aptos.
    """
    progress = progress or (lambda percent, text: None)
    homes, _ = modeling_homes()
    if not homes:
        raise ModelsUnavailable('Ningún hogar tiene datos suficientes para entrenar (lecturas planas, antiguas o con poco historial).')
    home_ids = [home.id for home in homes]

    trained = []
    consumption_old, _ = _stale(ModelArtifact.Kind.CONSUMPTION_QUANTILE)
    if force or consumption_old:
        progress(0, 'Entrenando el modelo de consumo con datos reales')
        try:
            train_consumption_model(home_ids)
        except ValueError as exc:
            raise ModelsUnavailable(str(exc)) from exc
        trained.append('consumo')

    anomaly_old, _ = _stale(ModelArtifact.Kind.ANOMALY_DETECTOR)
    if force or anomaly_old:
        progress(50, 'Entrenando el detector de anomalías con datos reales')
        try:
            train_anomaly_model(home_ids)
        except ValueError as exc:
            raise ModelsUnavailable(str(exc)) from exc
        trained.append('anomalías')

    if trained:
        prune_model_files()
    return {'trained': bool(trained), 'models': trained, 'homes_used': len(home_ids)}


def prune_model_files():
    """Borra las versiones de modelos que ya no se usan: deja la activa y KEEP_INACTIVE_VERSIONS anteriores,
    y elimina las carpetas que ningún ModelArtifact referencia (p. ej. entrenamientos interrumpidos)."""
    removed = 0
    for kind, subdir in MODEL_SUBDIRS.items():
        inactive = ModelArtifact.objects.filter(kind=kind, is_active=False).order_by('-trained_at')
        for artifact in inactive[KEEP_INACTIVE_VERSIONS:]:
            shutil.rmtree(artifact.file_path, ignore_errors=True)
            artifact.delete()
            removed += 1
        referenced = {Path(path).resolve() for path in ModelArtifact.objects.filter(kind=kind).values_list('file_path', flat=True)}
        folder = Path(settings.ML_MODELS_DIR) / subdir
        if folder.is_dir():
            for version_dir in folder.iterdir():
                if version_dir.is_dir() and version_dir.resolve() not in referenced:
                    shutil.rmtree(version_dir, ignore_errors=True)
                    removed += 1
    return removed
