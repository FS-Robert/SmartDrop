import json
import time

from django.core.management.base import BaseCommand

from ml_engine.monitor import interval_seconds, run_once


class Command(BaseCommand):
    help = (
        'Monitor automático de fugas fuera del servidor web: lee las lecturas nuevas de Supabase, analiza cada '
        'vivienda y avisa a los administradores. Sin --loop hace una sola pasada (útil para el Programador de tareas).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Repite cada ML_MONITOR_INTERVAL_MINUTES hasta detenerlo.')

    def handle(self, *args, **options):
        while True:
            summary = run_once()
            if summary is None:
                self.stdout.write('Omitido: otro proceso ya analizó hace poco o hay un análisis en curso.')
            else:
                self.stdout.write(json.dumps(summary.get('fugas', summary), ensure_ascii=False, default=str))
            if not options['loop']:
                return
            time.sleep(interval_seconds())
