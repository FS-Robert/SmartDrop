from django.core.management.base import BaseCommand

from App.asistente import historial


class Command(BaseCommand):
    help = 'Borra las conversaciones con Gotita que llevan más de 20 días sin usarse'

    def handle(self, *args, **options):
        borradas = historial.limpiar_viejas(forzar=True)
        self.stdout.write(f'Listo: se borraron {borradas} registros viejos del historial de Gotita.')
