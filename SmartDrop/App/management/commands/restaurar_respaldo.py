"""Revisa y restaura un respaldo de la base (PB064, escenario 3).

    python manage.py restaurar_respaldo --listar                   # respaldos guardados en Supabase Storage
    python manage.py restaurar_respaldo --ultimo                   # descarga el más reciente y lo revisa (no cambia nada)
    python manage.py restaurar_respaldo ARCHIVO --sqlite-en CARPETA  # revisa y saca las bases SQLite a una carpeta
    python manage.py restaurar_respaldo ARCHIVO --confirmar        # devuelve las tablas a Supabase (upsert)

Sin --confirmar solo descifra, comprueba las huellas y cuenta las filas: sirve para probar cada respaldo.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from App import respaldos


class Command(BaseCommand):
    help = 'Descifra, verifica y (con --confirmar) restaura un respaldo creado con respaldar_base.'

    def add_arguments(self, parser):
        parser.add_argument('archivo', nargs='?', help='Ruta del respaldo (.tar.gz.enc).')
        parser.add_argument('--listar', action='store_true', help='Lista los respaldos de Supabase Storage.')
        parser.add_argument('--ultimo', action='store_true', help='Descarga el respaldo más reciente de Storage.')
        parser.add_argument('--tablas', nargs='+', help='Solo restaurar estas tablas.')
        parser.add_argument('--sqlite-en', help='Carpeta donde dejar las bases SQLite del respaldo.')
        parser.add_argument('--confirmar', action='store_true', help='Escribir las filas en Supabase.')

    def handle(self, *args, **op):
        try:
            if op['listar']:
                for nombre in respaldos.listar_remotos():
                    self.stdout.write(nombre)
                return
            ruta = op['archivo']
            if op['ultimo']:
                remotos = respaldos.listar_remotos()
                if not remotos:
                    raise CommandError('No hay respaldos en Supabase Storage.')
                ruta = respaldos.descargar(remotos[0])
                self.stdout.write(f'Descargado: {ruta}')
            if not ruta or not Path(ruta).exists():
                raise CommandError('Indica el archivo del respaldo, --ultimo o --listar.')

            manifiesto, tablas, sqlite_dbs = respaldos.abrir_respaldo(ruta)
            self.stdout.write(self.style.SUCCESS(
                f'Respaldo íntegro del {manifiesto["creado"]}: {len(tablas)} tablas, '
                f'{sum(len(f) for f in tablas.values())} filas, {len(sqlite_dbs)} bases SQLite.'))
            if op['sqlite_en']:
                for destino in respaldos.guardar_sqlite(sqlite_dbs, op['sqlite_en']):
                    self.stdout.write(f'  SQLite guardada en {destino}')

            elegidas = [t for t in respaldos.orden_de_restauracion(tablas) if not op['tablas'] or t in op['tablas']]
            if not op['confirmar']:
                for tabla in elegidas:
                    self.stdout.write(f'  {tabla}: {len(tablas[tabla])} filas')
                self.stdout.write('Revisión sin cambios. Agrega --confirmar para devolver estas tablas a Supabase.')
                return
            for tabla in elegidas:
                cantidad = respaldos.restaurar_tabla(tabla, tablas[tabla], manifiesto['llaves'].get(tabla) or [])
                self.stdout.write(f'  {tabla}: {cantidad} filas restauradas')
            self.stdout.write(self.style.SUCCESS('Restauración terminada.'))
        except respaldos.RespaldoError as exc:
            raise CommandError(str(exc)) from exc
