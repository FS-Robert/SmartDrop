"""Limpieza y validación de datos (PB055).

    python manage.py ml_validar_datos                          # copia del motor ML: duplicados, rangos e historial
    python manage.py ml_validar_datos --supabase               # además busca lecturas repetidas en Supabase (solo cuenta)
    python manage.py ml_validar_datos --supabase --confirmar   # y las borra (deja la primera de cada grupo)

En Supabase una lectura repetida es otra fila con el mismo sensor, vivienda, fecha y valor (por ejemplo, cuando
el ESP32 reenvía un mensaje o dos procesos guardaron la misma lectura).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from App import supabase_client
from ml_engine import validacion

PAGINA = 1000


def duplicados_supabase(dias):
    """ids de `lectura` repetidos en los últimos `dias` (se deja la de id más bajo de cada grupo)."""
    desde = (timezone.now() - timedelta(days=dias)).isoformat()
    vistos, repetidos, ultimo = set(), [], 0
    while True:
        filas = supabase_client.select('lectura', 'id_lectura,id_sensor,id_vivienda,fecha_registro,valor', {
            'fecha_registro': f'gte.{desde}', 'id_lectura': f'gt.{ultimo}',
            'order': 'id_lectura.asc', 'limit': str(PAGINA),
        })
        for fila in filas:
            clave = (fila.get('id_sensor'), fila.get('id_vivienda'), fila.get('fecha_registro'), fila.get('valor'))
            if clave in vistos:
                repetidos.append(fila['id_lectura'])
            else:
                vistos.add(clave)
        if len(filas) < PAGINA:
            return repetidos, len(vistos) + len(repetidos)
        ultimo = filas[-1]['id_lectura']


class Command(BaseCommand):
    help = 'Borra lecturas duplicadas, marca las fuera de rango y avisa de las viviendas sin datos suficientes.'

    def add_arguments(self, parser):
        parser.add_argument('--supabase', action='store_true', help='Buscar también duplicados en la tabla lectura.')
        parser.add_argument('--dias', type=int, default=7, help='Días hacia atrás a revisar en Supabase (7).')
        parser.add_argument('--confirmar', action='store_true', help='Borrar de verdad los duplicados de Supabase.')

    def handle(self, *args, **op):
        r = validacion.validar()
        self.stdout.write(self.style.SUCCESS('Copia del motor de predicción revisada:'))
        self.stdout.write(f"  Duplicados eliminados: {r['duplicados_eliminados']} {r['duplicados_por_metrica'] or ''}")
        self.stdout.write(f"  Lecturas fuera de rango marcadas como inválidas: {r['invalidas_marcadas']} "
                          f"{r['invalidas_por_metrica'] or ''} (en total {r['invalidas_total']})")
        for lectura in validacion.invalidas_recientes(5):
            self.stdout.write(f"    · {lectura['nic']} {lectura['metrica']}={lectura['valor']:g}: {lectura['motivo']}")
        if r['avisos']:
            self.stdout.write(self.style.WARNING('  Datos insuficientes:'))
            for aviso in r['avisos']:
                self.stdout.write(f'    · {aviso}')
        else:
            self.stdout.write('  Todas las viviendas tienen historial suficiente.')

        if not op['supabase']:
            return
        repetidos, revisadas = duplicados_supabase(op['dias'])
        self.stdout.write(f"Supabase (lectura, últimos {op['dias']} días): {revisadas} filas revisadas, "
                          f'{len(repetidos)} repetidas.')
        if not repetidos:
            return
        if not op['confirmar']:
            self.stdout.write('Sin cambios. Agrega --confirmar para borrarlas.')
            return
        for inicio in range(0, len(repetidos), 200):
            supabase_client.delete('lectura', {'id_lectura': supabase_client.ids_in(repetidos[inicio:inicio + 200])})
        self.stdout.write(self.style.SUCCESS(f'{len(repetidos)} lecturas repetidas borradas de Supabase.'))
