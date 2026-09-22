from django.core.management.base import BaseCommand

from ml_engine.anomaly.train import train_anomaly_model


class Command(BaseCommand):
    help = 'Entrena el detector de anomalías (Isolation Forest) sobre residuales de presión/flujo/calidad.'

    def add_arguments(self, parser):
        parser.add_argument('--contamination', type=float, default=0.02)

    def handle(self, *args, **options):
        artifact = train_anomaly_model(contamination=options['contamination'])
        self.stdout.write(self.style.SUCCESS(f'Detector entrenado: {artifact.kind} v{artifact.version}'))
        for key, value in artifact.metrics.items():
            self.stdout.write(f'  {key}: {value}')
