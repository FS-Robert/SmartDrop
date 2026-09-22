"""Configura particionado con pg_partman para `ml_sensor_reading` (opcional).

Este comando SOLO hace algo si el alias de DB 'timeseries' apunta a Postgres
(ver SmartDrop/settings.py: variables ML_TIMESERIES_DB_*). En SQLite
(configuración por defecto para desarrollo/entrenamiento local) se limita a
avisar y no hace nada — no rompe el flujo local.

Requisitos en el servidor Postgres antes de correr esto:
  CREATE EXTENSION IF NOT EXISTS pg_partman;  (ya mencionaste tenerla habilitada)
  CREATE SCHEMA IF NOT EXISTS partman;

Lo que hace este comando:
  1. Verifica que `ml_sensor_reading` exista (créala antes con `migrate`).
  2. Registra la tabla como padre particionado por rango de `ts` (mensual)
     usando `partman.create_parent`.
  3. Deja instrucciones para programar el mantenimiento periódico
     (`partman.run_maintenance_proc`), ya que este proyecto no usa Celery/
     pg_cron por defecto: puedes llamarlo desde una tarea programada del
     sistema operativo (cron / Task Scheduler) ejecutando
     `python manage.py ml_run_partman_maintenance`.
"""
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = 'Configura pg_partman para particionar ml_sensor_reading (solo aplica si el alias timeseries es Postgres).'

    def handle(self, *args, **options):
        conn = connections['timeseries']
        if conn.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING(
                "El alias 'timeseries' no es Postgres (es "
                f"'{conn.vendor}'). No hay nada que particionar en desarrollo local; "
                'este comando solo aplica cuando configures ML_TIMESERIES_DB_HOST hacia tu Supabase.'
            ))
            return

        with conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass('ml_sensor_reading');")
            exists = cursor.fetchone()[0]
            if not exists:
                self.stdout.write(self.style.ERROR(
                    'ml_sensor_reading no existe todavía. Corre '
                    '`python manage.py migrate --database=timeseries` primero.'
                ))
                return

            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_partman';")
            if cursor.fetchone() is None:
                self.stdout.write(self.style.ERROR(
                    'La extensión pg_partman no está habilitada en esta base. '
                    'Habilítala con: CREATE EXTENSION pg_partman;'
                ))
                return

            cursor.execute("""
                SELECT partman.create_parent(
                    p_parent_table => 'public.ml_sensor_reading',
                    p_control => 'ts',
                    p_interval => 'monthly',
                    p_premake => 3
                );
            """)
        self.stdout.write(self.style.SUCCESS(
            'ml_sensor_reading registrada en pg_partman (particiones mensuales, 3 futuras pre-creadas). '
            'Programa `partman.run_maintenance_proc()` periódicamente (pg_cron o un cron externo) '
            'para que siga creando particiones futuras automáticamente.'
        ))
