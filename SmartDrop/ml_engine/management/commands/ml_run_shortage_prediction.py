from django.core.management.base import BaseCommand

from ml_engine.models import Zone
from ml_engine.shortage.tank_simulation import run_shortage_prediction


class Command(BaseCommand):
    help = 'Corre la simulación Monte Carlo de balance de masa y predicción de desabasto para una o todas las zonas.'

    def add_arguments(self, parser):
        parser.add_argument('--zone-id', type=int, default=None)
        parser.add_argument('--horizon-hours', type=int, default=72)
        parser.add_argument('--n-paths', type=int, default=500)

    def handle(self, *args, **options):
        zone_ids = [options['zone_id']] if options['zone_id'] else list(Zone.objects.values_list('id', flat=True))
        for zone_id in zone_ids:
            prediction = run_shortage_prediction(
                zone_id, horizon_hours=options['horizon_hours'], n_paths=options['n_paths'],
            )
            self.stdout.write(self.style.SUCCESS(
                f'Zona {zone_id}: riesgo={prediction.nivel_riesgo} '
                f'mediana={prediction.median_hours_to_shortage} probabilidad={prediction.probabilidad_desabasto_horizonte:.2f}'
            ))
