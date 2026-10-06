import json

from django.core.management.base import BaseCommand, CommandError

from ml_engine.pipeline import PipelineBusy, pipeline_lock, run_full_prediction


class Command(BaseCommand):
    help = (
        'Hace desde la consola lo mismo que el botón "Realizar predicciones": sincroniza las lecturas de Supabase, '
        'entrena los modelos si hace falta, predice el desabasto de cada tanque y analiza fugas (con avisos).'
    )

    def handle(self, *args, **options):
        def progress(percent, text):
            self.stdout.write(f'[{int(percent):3d} %] {text}')

        try:
            with pipeline_lock(wait_seconds=90):
                summary = run_full_prediction(progress)
        except PipelineBusy:
            raise CommandError('Hay otro análisis en curso. Inténtalo de nuevo en un momento.')
        summary['fugas'].pop('resultados', None)
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        self.stdout.write(self.style.SUCCESS('Predicciones terminadas.'))
