from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = (
        'Corre el pipeline Fase 1 completo end-to-end: genera datos sintéticos '
        '(si no existen), entrena ambos modelos, y produce forecasts/shortage/anomalías.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--skip-generate', action='store_true', help='No regenerar datos sintéticos (usar los existentes).')
        parser.add_argument('--zones', type=int, default=2)
        parser.add_argument('--homes-per-zone', type=int, default=15)
        parser.add_argument('--days', type=int, default=60)

    def handle(self, *args, **options):
        if not options['skip_generate']:
            call_command(
                'ml_generate_synthetic_data',
                zones=options['zones'], homes_per_zone=options['homes_per_zone'], days=options['days'],
            )
        call_command('ml_train_consumption_model')
        call_command('ml_predict_consumption')
        call_command('ml_train_anomaly_model')
        call_command('ml_run_anomaly_detection')
        call_command('ml_run_shortage_prediction')
        self.stdout.write(self.style.SUCCESS('Pipeline Fase 1 completo ejecutado correctamente.'))
