"""Ejecución en segundo plano del botón 'Realizar predicciones' (con progreso consultable por la web y la app)."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone

from ml_engine.models import PredictionJob
from ml_engine.pipeline import PipelineBusy, pipeline_lock, run_full_prediction

logger = logging.getLogger(__name__)

STALE_JOB_MINUTES = 20
LOCK_WAIT_SECONDS = 90
_threads = {}  # job_id -> hilo que lo ejecuta en este proceso


def _expire_stale_jobs():
    """Cierra los jobs 'en curso' que ya no avanzan: los muy antiguos y los que quedaron huérfanos porque el
    servidor se reinició a mitad de la ejecución (el servidor corre en un solo proceso, igual que el monitor)."""
    limit = timezone.now() - timedelta(minutes=STALE_JOB_MINUTES)
    for job in PredictionJob.objects.filter(status=PredictionJob.Status.RUNNING):
        thread = _threads.get(job.id)
        if job.started_at < limit or thread is None or not thread.is_alive():
            PredictionJob.objects.filter(id=job.id, status=PredictionJob.Status.RUNNING).update(
                status=PredictionJob.Status.ERROR, error='La ejecución se interrumpió.', finished_at=timezone.now(),
            )


def start_prediction_job(user_id=None):
    """Lanza una ejecución; si ya hay una en curso devuelve esa en vez de duplicar trabajo."""
    _expire_stale_jobs()
    running = PredictionJob.objects.filter(status=PredictionJob.Status.RUNNING).first()
    if running is not None:
        return running, False
    job = PredictionJob.objects.create(id=uuid.uuid4().hex, requested_by=user_id, step='En cola', progress=0)
    thread = threading.Thread(target=_run_job, args=(job.id,), name=f'ml-job-{job.id[:6]}', daemon=True)
    _threads[job.id] = thread
    thread.start()
    return job, True


def _run_job(job_id):
    last = {'percent': -1}

    def progress(percent, text):
        percent = int(max(0, min(99, percent)))
        if percent != last['percent']:
            last['percent'] = percent
            PredictionJob.objects.filter(id=job_id).update(progress=percent, step=text)

    try:
        PredictionJob.objects.filter(id=job_id).update(step='Esperando turno')
        with pipeline_lock(wait_seconds=LOCK_WAIT_SECONDS):
            result = run_full_prediction(progress)
        PredictionJob.objects.filter(id=job_id).update(
            status=PredictionJob.Status.DONE, progress=100, step='Listo', result=result, finished_at=timezone.now(),
        )
    except PipelineBusy:
        PredictionJob.objects.filter(id=job_id).update(
            status=PredictionJob.Status.ERROR, finished_at=timezone.now(),
            error='Hay otro análisis en curso (monitor automático). Inténtalo de nuevo en un momento.',
        )
    except Exception as exc:
        logger.exception('Falló la ejecución de predicciones %s', job_id)
        PredictionJob.objects.filter(id=job_id).update(
            status=PredictionJob.Status.ERROR, finished_at=timezone.now(), error=f'{exc.__class__.__name__}: {exc}',
        )
    finally:
        _threads.pop(job_id, None)
        close_old_connections()


def serialize_job(job):
    return {
        'job_id': job.id, 'status': job.status, 'step': job.step, 'progress': job.progress,
        'started_at': job.started_at, 'finished_at': job.finished_at, 'result': job.result, 'error': job.error,
    }
