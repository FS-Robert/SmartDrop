import logging
import os
import sys
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)

WARM_DELAY_SECONDS = 20
WARM_INTERVAL_SECONDS = 300


class AppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'App'

    def ready(self):
        if self._is_serving_process():
            threading.Thread(target=_keep_sensor_series_warm, name='sensor-series-warm', daemon=True).start()

    @staticmethod
    def _is_serving_process():
        """True solo cuando corre un servidor web (no migrate, tests, shell ni el proceso padre del autoreload)."""
        argv = ' '.join(sys.argv).lower()
        if 'runserver' in argv:
            return os.environ.get('RUN_MAIN') == 'true' or '--noreload' in argv
        return any(server in argv for server in ('daphne', 'gunicorn', 'uvicorn', 'hypercorn'))


def _keep_sensor_series_warm():
    """Mantiene en memoria las series de los sensores para que las gráficas de semana/mes carguen rápido."""
    from App.queries import warm_sensor_series

    time.sleep(WARM_DELAY_SECONDS)
    while True:
        try:
            warm_sensor_series()
        except Exception:  # noqa: BLE001 — sin conexión momentánea: se reintenta en el siguiente ciclo.
            logger.warning('No se pudieron precargar las series de los sensores', exc_info=True)
        time.sleep(WARM_INTERVAL_SECONDS)
