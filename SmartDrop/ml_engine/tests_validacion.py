"""Pruebas de PB055: limpieza y validación automática de datos."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from App import supabase_client
from App.models import Rol, Usuario
from ml_engine import pipeline, validacion
from ml_engine.models import ConsumptionAggregate, Home, MonitorState, SensorReading, Zone
from ml_engine.realdata import sync
from ml_engine.tests import FakeSupabase


class ValidacionTestCase(TestCase):
    databases = {'default', 'timeseries'}

    def setUp(self):
        self.zona = Zone.objects.create(name='Tanque 1', source='real', capacidad_maxima_litros=20, altura_total_cm=30)
        self.casa = Home.objects.create(zone=self.zona, source='real', etiqueta='NIC-0001',
                                        meta={'sensores': {'flujo': 4, 'presion': 1, 'nivel': 2}})
        self.ahora = timezone.now().replace(minute=0, second=0, microsecond=0)

    def lectura(self, metric, valor, minutos=0, **extra):
        return SensorReading.objects.create(
            home=None if metric == 'nivel_tanque' else self.casa, zone=self.zona if metric == 'nivel_tanque' else None,
            metric=metric, ts=self.ahora - timedelta(minutes=minutos), value=valor, **extra)

    def horas_de_historial(self, horas):
        ConsumptionAggregate.objects.bulk_create([ConsumptionAggregate(
            home=self.casa, period='hourly', period_start=self.ahora - timedelta(hours=h), litros=10.0,
        ) for h in range(horas)])


class DuplicadosTests(ValidacionTestCase):
    """Escenario 1: eliminación de registros duplicados."""

    def test_borra_los_repetidos_y_deja_uno(self):
        for _ in range(3):
            self.lectura('flujo', 1.5, minutos=10)
        self.lectura('flujo', 1.5, minutos=20)            # mismo valor, otra hora: no es duplicado
        self.lectura('presion', 9.0, minutos=10)          # misma hora, otro sensor: no es duplicado
        for _ in range(2):
            self.lectura('nivel_tanque', 12.0, minutos=10)

        resumen = validacion.validar()

        self.assertEqual(resumen['duplicados_eliminados'], 3)
        self.assertEqual(resumen['duplicados_por_metrica'], {'flujo': 2, 'nivel_tanque': 1})
        self.assertEqual(SensorReading.objects.filter(metric='flujo').count(), 2)
        self.assertEqual(SensorReading.objects.filter(metric='nivel_tanque').count(), 1)
        self.assertEqual(validacion.validar()['duplicados_eliminados'], 0)  # ya no queda nada repetido

    def test_la_sincronizacion_no_guarda_repetidos(self):
        fila = {'valor': 1.2, 'fecha_registro': self.ahora.isoformat()}
        self.assertEqual(sync._store_home_rows(self.casa, [('flujo', fila), ('flujo', dict(fila))]), 1)
        self.assertEqual(sync._store_home_rows(self.casa, [('flujo', dict(fila))]), 0)


class FueraDeRangoTests(ValidacionTestCase):
    """Escenario 2: detección de valores fuera de rango."""

    def test_marca_como_invalidos_los_valores_imposibles(self):
        bien = [self.lectura('flujo', 2.0, 1), self.lectura('presion', 9.5, 1), self.lectura('nivel_tanque', 15.0, 1)]
        mal = [self.lectura('flujo', -3.0, 2), self.lectura('flujo', 250.0, 3), self.lectura('presion', 9999.0, 4),
               self.lectura('calidad', 5000.0, 5), self.lectura('nivel_tanque', 45.0, 6)]

        resumen = validacion.validar()

        self.assertEqual(resumen['invalidas_marcadas'], 5)
        self.assertEqual(resumen['invalidas_por_metrica'], {'flujo': 2, 'presion': 1, 'calidad': 1, 'nivel_tanque': 1})
        for lectura in mal:
            lectura.refresh_from_db()
            self.assertFalse(lectura.is_valid)
            self.assertIn('fuera de rango', lectura.motivo_invalido)
        for lectura in bien:
            lectura.refresh_from_db()
            self.assertTrue(lectura.is_valid)
        self.assertEqual(SensorReading.objects.count(), 8)  # se marcan, no se borran

    def test_los_invalidos_no_cuentan_en_el_consumo_por_hora(self):
        self.lectura('flujo', 2.0, 5)
        self.lectura('flujo', 250.0, 10)
        sync._rebuild_hourly(self.casa, self.ahora - timedelta(hours=1))
        validacion.validar()
        hora = ConsumptionAggregate.objects.get(home=self.casa, period_start=self.ahora - timedelta(hours=1))
        self.assertAlmostEqual(hora.litros, 2.0 * 60)  # sin el 250, el promedio de la hora es 2 L/min

    def test_al_sincronizar_ya_llegan_marcadas(self):
        filas = [('flujo', {'valor': 999, 'fecha_registro': self.ahora.isoformat()}),
                 ('nivel', {'valor': 9000, 'fecha_registro': self.ahora.isoformat()}),
                 ('presion', {'valor': 9.1, 'fecha_registro': self.ahora.isoformat()})]
        sync._store_home_rows(self.casa, filas)
        invalidas = {l.metric: l.motivo_invalido for l in SensorReading.objects.filter(is_valid=False)}
        self.assertEqual(set(invalidas), {'flujo', 'nivel_tanque'})
        self.assertIn('9000 cm', invalidas['nivel_tanque'])


class DatosInsuficientesTests(ValidacionTestCase):
    """Escenario 3: notificación por datos insuficientes."""

    def test_avisa_cuando_falta_historial(self):
        self.lectura('flujo', 1.0)
        self.lectura('flujo', 2.0, 30)
        self.horas_de_historial(10)
        resumen = validacion.validar()
        (aviso,) = resumen['avisos']
        self.assertIn('NIC-0001: no hay suficientes datos históricos', aviso)
        self.assertIn('10 h de historial (mínimo 48)', aviso)

    def test_con_historial_suficiente_no_avisa(self):
        self.lectura('flujo', 1.0)
        self.lectura('flujo', 2.0, 30)
        self.horas_de_historial(60)
        self.assertEqual(validacion.validar()['avisos'], [])

    def test_realizar_predicciones_devuelve_el_aviso(self):
        self.lectura('flujo', 1.0)
        self.lectura('flujo', 2.0, 30)
        self.horas_de_historial(5)
        fake = FakeSupabase(vivienda=[], tanque=[], sensor=[], usuario=[])
        with fake.patch(), mock.patch.object(pipeline, '_sync', return_value={'homes': 1}):
            resumen = pipeline.run_full_prediction()
        self.assertEqual([i['nic'] for i in resumen['validacion']['datos_insuficientes']], ['NIC-0001'])
        self.assertIn('insuficiente', {e['status'] for e in resumen['fugas']['excluidos']})


class ApiYComandoTests(ValidacionTestCase):
    def setUp(self):
        super().setUp()
        usuario = Rol.objects.create(nombre_rol='user')
        admin = Rol.objects.create(nombre_rol='admin')
        self.admin = Usuario.objects.create_user(email='a@example.com', nombre='A', apellido='A',
                                                 password='password-segura', rol=admin, supabase_id=1)
        self.vecino = Usuario.objects.create_user(email='u@example.com', nombre='U', apellido='U',
                                                  password='password-segura', rol=usuario, supabase_id=10)
        self.client = APIClient()

    def test_el_admin_valida_desde_la_web(self):
        self.lectura('flujo', 2.0)
        self.lectura('flujo', 2.0)
        self.lectura('presion', -8.0, 3)
        self.client.force_authenticate(self.admin)
        url = reverse('ml_engine:data_quality')
        self.assertIsNone(self.client.get(url).json()['resumen'])
        datos = self.client.post(url).json()
        self.assertEqual((datos['resumen']['duplicados_eliminados'], datos['resumen']['invalidas_marcadas']), (1, 1))
        self.assertEqual(datos['invalidas'][0]['metrica'], 'presion')
        self.assertEqual(self.client.get(url).json()['resumen']['duplicados_eliminados'], 1)
        self.assertTrue(MonitorState.objects.filter(key='validacion').exists())

    def test_un_residente_no_puede(self):
        self.client.force_authenticate(self.vecino)
        self.assertEqual(self.client.post(reverse('ml_engine:data_quality')).status_code, 403)

    def test_comando_busca_y_borra_repetidos_en_supabase(self):
        momento = timezone.now().isoformat()
        filas = [{'id_lectura': i, 'id_sensor': 4, 'id_vivienda': 1, 'fecha_registro': momento, 'valor': 1.5}
                 for i in (1, 2, 3)] + [{'id_lectura': 4, 'id_sensor': 4, 'id_vivienda': 1,
                                          'fecha_registro': momento, 'valor': 1.6}]
        fake = FakeSupabase(lectura=filas)

        def borrar(tabla, params):
            ids = params['id_lectura'].strip('in.()').split(',')
            fake.tables[tabla] = [f for f in fake.tables[tabla] if str(f['id_lectura']) not in ids]

        salida = StringIO()
        with fake.patch(), mock.patch.object(supabase_client, 'delete', side_effect=borrar):
            call_command('ml_validar_datos', '--supabase', stdout=salida)
            self.assertEqual(len(fake.tables['lectura']), 4)  # sin --confirmar no borra
            call_command('ml_validar_datos', '--supabase', '--confirmar', stdout=salida)
        self.assertEqual(sorted(f['id_lectura'] for f in fake.tables['lectura']), [1, 4])
        self.assertIn('2 repetidas', salida.getvalue())
