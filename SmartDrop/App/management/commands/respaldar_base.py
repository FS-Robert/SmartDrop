"""Crea el respaldo cifrado de la base y lo guarda fuera del servidor (PB064).

    python manage.py respaldar_base                 # respaldo + subida a Supabase Storage + limpieza de viejos
    python manage.py respaldar_base --sin-subir     # solo la copia local (pruebas en tu computadora)
    python manage.py respaldar_base --nueva-clave   # muestra una RESPALDO_CLAVE nueva para el .env

En el servidor lo corre cada noche el temporizador deploy/smartdrop-respaldo.timer.
"""
from django.core.management.base import BaseCommand, CommandError

from App import respaldos


class Command(BaseCommand):
    help = 'Respalda Supabase y las bases SQLite en un archivo cifrado y lo sube a Supabase Storage.'

    def add_arguments(self, parser):
        parser.add_argument('--sin-subir', action='store_true', help='No subir el respaldo (solo copia local).')
        parser.add_argument('--tablas', nargs='+', help='Solo estas tablas de Supabase.')
        parser.add_argument('--nueva-clave', action='store_true', help='Genera una RESPALDO_CLAVE y termina.')

    def handle(self, *args, **opciones):
        if opciones['nueva_clave']:
            self.stdout.write('Pon esta línea en el .env del servidor y guarda una copia en un lugar seguro:')
            self.stdout.write(f'RESPALDO_CLAVE={respaldos.nueva_clave()}')
            return
        try:
            ruta, manifiesto = respaldos.crear_respaldo(tablas=opciones['tablas'])
            filas = sum(manifiesto['tablas'].values())
            self.stdout.write(self.style.SUCCESS(
                f'Respaldo creado: {ruta.name} ({len(manifiesto["tablas"])} tablas, {filas} filas, '
                f'{ruta.stat().st_size / 1024:.0f} KB, cifrado)'))
            for tabla, cantidad in manifiesto['tablas'].items():
                self.stdout.write(f'  {tabla}: {cantidad}')
            if opciones['sin_subir']:
                self.stdout.write('No se subió (--sin-subir).')
                respaldos.limpiar_viejos(remoto=False)
                return
            destino = respaldos.subir(ruta)
            self.stdout.write(self.style.SUCCESS(f'Guardado fuera del servidor en Supabase Storage: {destino}'))
            borrados = respaldos.limpiar_viejos()
            if borrados:
                self.stdout.write(f'Respaldos viejos borrados: {len(borrados)}')
        except respaldos.RespaldoError as exc:
            raise CommandError(str(exc)) from exc
