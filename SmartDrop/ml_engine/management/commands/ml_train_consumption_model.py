from django.core.management.base import BaseCommand

from ml_engine.forecasting.train import train_consumption_model


class Command(BaseCommand):
    help = 'Entrena el modelo global LightGBM de consumo (p10/p50/p90) sobre el historial disponible.'

    def handle(self, *args, **options):
        artifact = train_consumption_model()
        self.stdout.write(self.style.SUCCESS(f'Modelo entrenado: {artifact.kind} v{artifact.version}'))
        for key, value in artifact.metrics.items():
            self.stdout.write(f'  {key}: {value}')
