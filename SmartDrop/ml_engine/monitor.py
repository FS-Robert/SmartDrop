"""Monitor automático: un hilo en el propio servidor que revisa fugas cada pocos minutos, sin intervención del admin."""
from __future__ import annotations

import logging
import threading
import time

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from ml_engine.models import MonitorState
from ml_engine.pipeline import PipelineBusy, pipeline_lock, run_monitor_cycle

logger = logging.getLogger(__name__)

MONITOR_KEY = 'monitor'
_thread = None


def interval_seconds():
    return max(60.0, float(getattr(settings, 'ML_MONITOR_INTERVAL_MINUTES', 10)) * 60.0)


def run_once():
    """Una pasada del monitor; se omite si otro proceso/hilo ya la hizo hace poco o hay un análisis en curso."""
    started = timezone.now()
    try:
        with pipeline_lock(wait_seconds=0, min_gap_seconds=interval_seconds() - 30):
            summary = run_monitor_cycle()
        status = 'ok'
    except PipelineBusy:
        return None
    except Exception as exc:
        logger.exception('Falló el ciclo del monitor de fugas')
        summary, status = {'error': f'{exc.__class__.__name__}: {exc}'}, 'error'
    MonitorState.objects.update_or_create(key=MONITOR_KEY, defaults={
        'last_started_at': started, 'last_finished_at': timezone.now(), 'last_status': status, 'last_summary': summary,
    })
    return summary


def _loop():
    time.sleep(float(getattr(settings, 'ML_MONITOR_START_DELAY_SECONDS', 45)))
    while True:
        try:
            run_once()
        except Exception:
            logger.exception('Error inesperado en el monitor')
        finally:
            close_old_connections()
        time.sleep(interval_seconds())


def start_monitor():
    """Arranca el hilo una sola vez por proceso."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name='ml-leak-monitor', daemon=True)
    _thread.start()
    logger.info('Monitor automático de fugas iniciado (cada %.0f min)', interval_seconds() / 60)
