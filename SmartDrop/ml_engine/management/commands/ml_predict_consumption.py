from django.core.management.base import BaseCommand

from ml_engine.forecasting.predict import predict_consumption_for_homes


class Command(BaseCommand):
    help = 'Genera predicciones de consumo (p10/p50/p90) para el siguiente periodo, para todos los hogares activos.'

    def handle(self, *args, **options):
        forecasts = predict_consumption_for_homes()
        self.stdout.write(self.style.SUCCESS(f'{len(forecasts)} pronósticos generados.'))
