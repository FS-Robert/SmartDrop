import os
import sys

from django.apps import AppConfig
from django.conf import settings


class MlEngineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ml_engine'
    verbose_name = 'Motor de predicción (ML) - SmartDrop'

    def ready(self):
        if not getattr(settings, 'ML_MONITOR_ENABLED', True) or not self._is_serving_process():
            return
        from ml_engine.monitor import start_monitor
        start_monitor()

    @staticmethod
    def _is_serving_process():
        """True solo cuando corre un servidor web (no migrate, tests, shell ni el proceso padre del autoreload)."""
        argv = ' '.join(sys.argv).lower()
        if 'runserver' in argv:
            return os.environ.get('RUN_MAIN') == 'true' or '--noreload' in argv
        return any(server in argv for server in ('daphne', 'gunicorn', 'uvicorn', 'hypercorn'))