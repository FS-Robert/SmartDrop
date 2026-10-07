"""Pruebas del motor de predicción con un Supabase simulado en memoria (no tocan la base real)."""
import itertools
import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from App import supabase_client
from App.models import Rol, Usuario
from ml_engine import jobs, pipeline
from ml_engine.leaks.alerts import ORIGIN
from ml_engine.management.commands.ml_seed_demo_readings import STEP_MINUTES, simulate_home
from ml_engine.models import Home, LeakPrediction, PredictionJob, SensorReading, ShortagePrediction, Zone
from ml_engine.realdata import sync

DAYS = 14
PRIMARY_KEYS = {
    'fuga': 'id_fuga', 'alerta': 'id_alerta', 'notificacion': 'id_notificacion', 'prediccion_desabasto': 'id_prediccion',
}


def _comparable(value, other):
    for cast in (float, lambda text: datetime.fromisoformat(str(text).replace('Z', '+00:00'))):
        try:
            return cast(value), cast(other)
        except (TypeError, ValueError):
            continue
    return str(value), str(other)


_MODELS_DIR = None
_models_override = None


def setUpModule():
    """Todos los modelos que entrenan estas pruebas van a una carpeta temporal, nunca a ml_models/."""
    global _MODELS_DIR, _models_override
    _MODELS_DIR = Path(tempfile.mkdtemp(prefix='smartdrop-ml-'))
    _models_override = override_settings(ML_MODELS_DIR=_MODELS_DIR)
    _models_override.enable()


def tearDownModule():
    _models_override.disable()
    shutil.rmtree(_MODELS_DIR, ignore_errors=True)


class FakeSupabase:
    """Tablas en memoria con los filtros de PostgREST que usa el proyecto (eq, in, gt, gte, lt, ->>, order, limit)."""

    def __init__(self, **tables):
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self._ids = itertools.count(1)

    def _matches(self, row, params):
        for key, condition in params.items():
            if key in ('order', 'limit', 'offset'):
                continue
            if '->>' in key:
                column, field = key.split('->>')
                value = (row.get(column) or {}).get(field)
            else:
                value = row.get(key)
            operator, _, argument = condition.partition('.')
            if operator == 'eq':
                if str(value) != argument:
                    return False
            elif operator == 'in':
                if str(value) not in argument.strip('()').split(','):
                    return False
            elif operator in ('gt', 'gte', 'lt'):
                if value is None:
                    return False
                left, right = _comparable(value, argument)
                if not {'gt': left > right, 'gte': left >= right, 'lt': left < right}[operator]:
                    return False
            else:
                raise NotImplementedError(condition)
        return True

    def select(self, table, select='*', params=None):
        params = params or {}
        rows = [row for row in self.tables.get(table, []) if self._matches(row, params)]
        if params.get('order'):
            column, _, direction = params['order'].partition('.')
            rows.sort(key=lambda row: row[column], reverse=direction == 'desc')
        offset = int(params.get('offset', 0))
        return [dict(row) for row in rows[offset:offset + int(params.get('limit', len(rows)))]]

    def insert(self, table, data, return_representation=True):
        row = dict(data)
        if table in PRIMARY_KEYS:
            row.setdefault(PRIMARY_KEYS[table], next(self._ids))
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def insert_many(self, table, rows):
        for row in rows:
            self.insert(table, row)

    def update(self, table, data, params=None, return_representation=False):
        for row in self.tables.get(table, []):
            if self._matches(row, params or {}):
                row.update(data)

    def patch(self):
        return mock.patch.multiple(
            supabase_client, select=self.select, insert=self.insert, insert_many=self.insert_many, update=self.update,
        )


def build_supabase(scenarios):
    """Una vivienda por escenario ('normal' | 'fuga' | 'bomba' | 'plana'), cada una con su tanque y sus 4 sensores."""
    now = timezone.now().astimezone(dt_timezone.utc).replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % STEP_MINUTES)
    steps = DAYS * 24 * 60 // STEP_MINUTES + 1
    start = now - timedelta(minutes=STEP_MINUTES * (steps - 1))
    viviendas, tanques, sensores, lecturas = [], [], [], []
    kinds = (('flujo', 'Flujo', 'L/min'), ('presion', 'Presion', 'bar'), ('nivel', 'Nivel', 'cm'), ('tds', 'TDS', 'ppm'))
    for index, scenario in enumerate(scenarios, start=1):
        sensor_ids = {kind: index * 10 + offset for offset, (kind, _, _) in enumerate(kinds)}
        tank = {
            'id_tanque': index, 'nombre': f'Tanque {index}', 'altura_total': 30, 'capacidad_maxima_litros': 14.73,
            'nivel_critico_cm': 7, 'nivel_minimo_cm': 3, 'id_sensor_nivel': sensor_ids['nivel'],
            'forma_geometrica': 'cilindrico', 'diametro': 25, 'estado': 'normal',
        }
        tanques.append(tank)
        viviendas.append({
            'id_vivienda': index, 'nic': f'NIC-{index:04d}', 'direccion': f'Pasaje {index}', 'zona': 'Zona Norte',
            'nombre_completo_titular': f'Titular {index}', 'telefono_titular': f'7000-000{index}',
            'tipo_establecimiento': 'residencial',
        })
        sensores += [{'id_sensor': sensor_ids[kind], 'tipo_sensor': name, 'unidad_medida': unit} for kind, name, unit in kinds]
        flow, pressure, level, tds, _ = simulate_home(index, tank, start, steps, {
            'leak': scenario == 'fuga', 'leak_steps': 8 * 60 // STEP_MINUTES,
            'pump_fail': scenario == 'bomba', 'pump_fail_steps': 3 * 60 // STEP_MINUTES,
        }, np.random.default_rng(100 + index))
        if scenario == 'plana':
            flow, pressure, level, tds = (np.full(steps, value) for value in (0.2, 2.5, 20.0, 150.0))
        for step in range(steps):
            moment = (start + timedelta(minutes=STEP_MINUTES * step)).isoformat()
            for kind, series in (('flujo', flow), ('presion', pressure), ('nivel', level), ('tds', tds)):
                lecturas.append({'id_sensor': sensor_ids[kind], 'id_vivienda': index,
                                 'valor': round(float(series[step]), 4), 'fecha_registro': moment})
    # Una vivienda registrada pero sin lecturas: debe omitirse.
    viviendas.append({'id_vivienda': 99, 'nic': 'NIC-0099', 'direccion': 'Sin sensores', 'zona': 'Zona Sur',
                      'nombre_completo_titular': 'Nadie', 'telefono_titular': '', 'tipo_establecimiento': 'residencial'})
    usuarios = [{'id_usuario': 1, 'id_rol': 1}, {'id_usuario': 2, 'id_rol': 2}, {'id_usuario': 3, 'id_rol': 2}]
    return FakeSupabase(vivienda=viviendas, tanque=tanques, sensor=sensores, lectura=lecturas, usuario=usuarios)


class MlTestCase(TestCase):
    databases = {'default', 'timeseries'}

    def setUp(self):
        cache.clear()

    @classmethod
    def create_users(cls):
        user_role = Rol.objects.create(nombre_rol='user')
        admin_role = Rol.objects.create(nombre_rol='admin')
        cls.user = Usuario.objects.create_user(
            email='u@example.com', nombre='U', apellido='U', password='password-segura', rol=user_role, supabase_id=1)
        cls.admin = Usuario.objects.create_user(
            email='a@example.com', nombre='A', apellido='A', password='password-segura', rol=admin_role, supabase_id=2)


class RealDataPipelineTests(MlTestCase):
    """Ejecuta una vez el flujo completo del botón 'Realizar predicciones' y comprueba cada resultado."""

    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.create_users()
        cls.supabase = build_supabase(['normal', 'fuga', 'bomba', 'plana'])
        cls.enterClassContext(cls.supabase.patch())
        cls.steps = []
        cls.summary = pipeline.run_full_prediction(lambda percent, text: cls.steps.append(percent))

    def test_sincroniza_viviendas_y_tanques_reales(self):
        self.assertEqual(self.summary['sync']['homes'], 4)
        self.assertEqual([item['nic'] for item in self.summary['sync']['omitidas']], ['NIC-0099'])
        self.assertFalse(Home.objects.exclude(source='real').exists())
        home = Home.objects.get(source_ref_vivienda_id=2)
        self.assertEqual(home.meta['sensores'], {'flujo': 20, 'presion': 21, 'nivel': 22, 'calidad': 23})
        self.assertEqual(home.zone.source_ref_tanque_id, 2)
        self.assertAlmostEqual(home.zone.nivel_critico_litros, 14.73 * 7 / 30, places=3)

    def test_el_nivel_se_convierte_de_cm_a_litros(self):
        zone = Zone.objects.get(source_ref_tanque_id=1)
        latest_cm = max((row for row in self.supabase.tables['lectura'] if row['id_sensor'] == 12),
                        key=lambda row: row['fecha_registro'])['valor']
        self.assertAlmostEqual(zone.nivel_actual_litros, latest_cm / 30 * 14.73, places=2)

    def test_sincronizar_de_nuevo_no_duplica_lecturas(self):
        before = SensorReading.objects.count()
        homes = list(Home.objects.select_related('zone'))
        self.assertEqual(sync.sync_readings(homes), 0)
        self.assertEqual(SensorReading.objects.count(), before)

    def test_las_lecturas_planas_se_excluyen_con_motivo(self):
        excluded = {item['nic']: item['status'] for item in self.summary['fugas']['excluidos']}
        self.assertEqual(excluded, {'NIC-0004': 'plana'})
        self.assertEqual(self.summary['modelos']['homes_used'], 3)

    def test_predice_desabasto_por_tanque_y_lo_guarda_en_supabase(self):
        risk = {item['tanque']: item['nivel_riesgo'] for item in self.summary['tanques']}
        self.assertEqual(set(risk), {'Tanque 1', 'Tanque 2', 'Tanque 3'})
        self.assertIn(risk['Tanque 3'], ('alto', 'critico'))  # la bomba dejó de recargar
        self.assertEqual(risk['Tanque 1'], 'bajo')
        rows = self.supabase.tables['prediccion_desabasto']
        self.assertEqual(sorted(row['id_tanque'] for row in rows), [1, 2, 3])
        self.assertTrue(all(row['estado'] == 'activa' for row in rows))
        details = ShortagePrediction.objects.filter(zone__source_ref_tanque_id=3).first().details
        self.assertTrue(details['bomba_sin_recargar_ahora'])

    def test_detecta_la_fuga_y_avisa_a_cada_admin_con_los_detalles(self):
        results = {item['nic']: item for item in self.summary['fugas']['resultados']}
        self.assertGreaterEqual(results['NIC-0002']['probabilidad'], 0.6)
        self.assertLess(results['NIC-0001']['probabilidad'], 0.5)
        self.assertEqual(self.summary['fugas']['alertas_creadas'], 1)

        (alerta,) = self.supabase.tables['alerta']
        self.assertEqual(alerta['tipo_alerta'], 'fuga')
        self.assertEqual(alerta['datos_adicionales']['origen'], ORIGIN)
        for expected in ('NIC-0002', 'Pasaje 2', 'Titular 2', '7000-0002', 'Probabilidad estimada', 'pérdida estimada'):
            self.assertIn(expected, alerta['mensaje'])
        (fuga,) = self.supabase.tables['fuga']
        self.assertEqual((fuga['id_sensor_flujo'], fuga['id_sensor_presion']), (20, 21))
        self.assertEqual(alerta['id_fuga'], fuga['id_fuga'])
        self.assertEqual(sorted(row['id_usario_destino'] for row in self.supabase.tables['notificacion']), [2, 3])
        self.assertEqual(LeakPrediction.objects.get(alerta_id=alerta['id_alerta']).home.etiqueta, 'NIC-0002')

    def test_el_monitor_no_repite_el_aviso_de_la_misma_vivienda(self):
        summary = pipeline.run_monitor_cycle()
        self.assertEqual(summary['fugas']['posibles_fugas'], 1)
        self.assertEqual(summary['fugas']['alertas_creadas'], 0)
        self.assertEqual(len(self.supabase.tables['alerta']), 1)

    def test_el_progreso_avanza_hasta_100(self):
        self.assertEqual(self.steps, sorted(self.steps))
        self.assertEqual(self.steps[-1], 100)

    def test_resumen_de_tanques_y_fugas_para_el_panel(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        zonas = {zona['nic']: zona for zona in client.get(reverse('ml_engine:zone_summary')).json()['zonas']}
        self.assertEqual(zonas['NIC-0004']['calidad_datos'], 'plana')
        self.assertEqual(zonas['NIC-0001']['nivel_riesgo'], 'bajo')
        self.assertIsNone(zonas['NIC-0001']['horas_hasta_desabasto'])

        leaks = client.get(reverse('ml_engine:leak_overview')).json()
        self.assertEqual(leaks['posibles_fugas'], 1)
        first = leaks['viviendas'][0]
        self.assertEqual((first['nic'], first['posible_fuga']), ('NIC-0002', True))
        self.assertTrue(first['causas'])
        self.assertGreater(first['metricas']['perdida_estimada_lph'], 0)

    def test_avisos_de_fuga_se_listan_y_se_marcan_como_leidos(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        data = client.get(reverse('ml_engine:leak_alerts'), {'solo_no_leidas': 1}).json()
        self.assertEqual(data['no_leidas'], 1)
        self.assertEqual(data['alertas'][0]['nic'], 'NIC-0002')
        self.assertEqual(data['alertas'][0]['titular'], 'Titular 2')

        response = client.post(reverse('ml_engine:leak_alerts_read'), {'todas': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get(reverse('ml_engine:leak_alerts')).json()['no_leidas'], 0)
        # El otro administrador conserva su aviso sin leer.
        other = [row for row in self.supabase.tables['notificacion'] if row['id_usario_destino'] == 3]
        self.assertEqual(other[0]['estado_visualizacion'], 'no_leida')

    def test_un_usuario_normal_no_ve_los_avisos_con_datos_del_titular(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('alertas_historial')), 'Titular 2')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('alertas_historial')), 'Titular 2')


class AdminOnlyTests(MlTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_users()

    def test_las_paginas_redirigen_a_quien_no_es_admin(self):
        self.client.force_login(self.user)
        for name in ('ml_engine:dashboard', 'ml_engine:leaks'):
            self.assertRedirects(self.client.get(reverse(name)), reverse('dashboard'), fetch_redirect_response=False)

    def test_las_paginas_del_admin_muestran_el_boton_y_el_apartado_de_fugas(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('ml_engine:dashboard'))
        self.assertContains(response, 'Realizar predicciones')
        self.assertContains(response, 'Predicción de fugas')
        self.assertContains(self.client.get(reverse('ml_engine:leaks')), 'Avisos de fuga')

    def test_la_api_rechaza_a_quien_no_es_admin(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.post(reverse('ml_engine:prediction_run')).status_code, 403)
        for name in ('ml_engine:zone_summary', 'ml_engine:leak_overview', 'ml_engine:leak_alerts', 'ml_engine:prediction_status'):
            self.assertEqual(client.get(reverse(name)).status_code, 403, name)
        self.assertFalse(PredictionJob.objects.exists())


class PredictionJobTests(MlTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_users()

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.thread = self.enterContext(mock.patch('ml_engine.jobs.threading.Thread')).return_value
        self.enterContext(mock.patch('ml_engine.jobs.close_old_connections'))

    def test_el_boton_lanza_un_job_y_su_progreso_se_puede_consultar(self):
        response = self.client.post(reverse('ml_engine:prediction_run'))
        self.assertEqual(response.status_code, 202)
        job_id = response.json()['job_id']
        self.assertEqual(response.json()['status'], 'running')
        self.thread.start.assert_called_once()

        def fake_pipeline(progress):
            progress(40, 'Comprobando modelos de ML')
            self.assertEqual(self.client.get(reverse('ml_engine:prediction_job', args=[job_id])).json()['progress'], 40)
            return {'tanques': [], 'fugas': {}}

        with mock.patch('ml_engine.jobs.run_full_prediction', side_effect=fake_pipeline):
            jobs._run_job(job_id)
        job = self.client.get(reverse('ml_engine:prediction_job', args=[job_id])).json()
        self.assertEqual((job['status'], job['progress'], job['result']), ('done', 100, {'tanques': [], 'fugas': {}}))

    def test_un_segundo_clic_reutiliza_la_ejecucion_en_curso(self):
        first = self.client.post(reverse('ml_engine:prediction_run')).json()
        second = self.client.post(reverse('ml_engine:prediction_run')).json()
        self.assertEqual(second['job_id'], first['job_id'])
        self.assertTrue(second['ya_en_curso'])
        self.assertEqual(PredictionJob.objects.count(), 1)

    def test_un_job_huerfano_tras_reiniciar_el_servidor_no_bloquea_el_boton(self):
        first = self.client.post(reverse('ml_engine:prediction_run')).json()
        jobs._threads.clear()  # el servidor se reinició: el hilo ya no existe
        second = self.client.post(reverse('ml_engine:prediction_run')).json()
        self.assertNotEqual(second['job_id'], first['job_id'])
        self.assertFalse(second['ya_en_curso'])
        self.assertEqual(PredictionJob.objects.get(id=first['job_id']).status, 'error')

    def test_un_error_del_pipeline_se_informa_en_el_job(self):
        job_id = self.client.post(reverse('ml_engine:prediction_run')).json()['job_id']
        with mock.patch('ml_engine.jobs.run_full_prediction', side_effect=RuntimeError('sin conexión')), \
                self.assertLogs('ml_engine.jobs', level='ERROR'):
            jobs._run_job(job_id)
        job = self.client.get(reverse('ml_engine:prediction_job', args=[job_id])).json()
        self.assertEqual(job['status'], 'error')
        self.assertIn('sin conexión', job['error'])
