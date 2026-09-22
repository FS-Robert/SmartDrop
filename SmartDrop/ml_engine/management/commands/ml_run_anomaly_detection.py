from django.core.management.base import BaseCommand

from ml_engine.anomaly.detect import run_anomaly_detection


class Command(BaseCommand):
    help = 'Corre el detector de anomalías sobre los datos más recientes y registra eventos flagged.'

    def handle(self, *args, **options):
        events = run_anomaly_detection()
        self.stdout.write(self.style.SUCCESS(f'{len(events)} anomalías detectadas.'))
