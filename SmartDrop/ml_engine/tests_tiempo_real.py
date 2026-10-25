"""Pruebas de PB026 (fugas al instante) y PB027 (aviso al sector), con Supabase simulado en memoria."""
import time
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from App.models import Rol, Usuario
from ml_engine.leaks import realtime, sector
from ml_engine.leaks.alerts import ORIGIN
from ml_engine.tests import FakeSupabase

SV = ZoneInfo('America/El_Salvador')


def _supabase():
    return FakeSupabase(
        usuario=[{'id_usuario': 1, 'id_rol': 2}, {'id_usuario': 10, 'id_rol': 3}, {'id_usuario': 11, 'id_rol': 3},
                 {'id_usuario': 12, 'id_rol': 3}],
        vivienda=[
            {'id_vivienda': 1, 'nic': 'NIC-0001', 'direccion': 'Pasaje 1', 'zona': 'Sector Norte',
             'nombre_completo_titular': 'Titular Uno', 'id_usuario_propietario': 10},
            {'id_vivienda': 2, 'nic': 'NIC-0002', 'direccion': 'Pasaje 2', 'zona': 'Sector Norte',
             'nombre_completo_titular': 'Titular Dos', 'id_usuario_propietario': 11},
            {'id_vivienda': 3, 'nic': 'NIC-0003', 'direccion': 'Pasaje 3', 'zona': 'Sector Sur',
             'nombre_completo_titular': 'Titular Tres', 'id_usuario_propietario': 12},
        ],
    )


class TiempoRealTestCase(TestCase):
    databases = {'default', 'timeseries'}

    def setUp(self):
        cache.clear()
        realtime.reiniciar()
        self.supabase = _supabase()
        self.enterContext(self.supabase.patch())
        self.enviados = []
        self.enterContext(mock.patch.object(sector, '_enviar', side_effect=lambda g, p: self.enviados.append((g, p))))

    def flujo(self, inicio, segundos, valor=1.8, vivienda=1, paso=2):
        """Simula el ESP32: una lectura de flujo cada 2 s. Devuelve (segundo, evento) del primer evento."""
        for s in range(0, segundos + 1, paso):
            evento = realtime.revisar_lectura(vivienda, 'flujo', valor, id_sensor=4, momento=inicio + timedelta(seconds=s))
            if evento:
                return s, evento
        return None, None

    def alertas(self, origen):
        return [a for a in self.supabase.tables.get('alerta', []) if a['datos_adicionales']['origen'] == origen]


class FlujoNocturnoTests(TiempoRealTestCase):
    """PB026 · Escenario 1: fuga por flujo nocturno anormal (Modo de Prueba Nocturna)."""

    def test_modo_prueba_clasifica_posible_fuga_en_menos_de_un_minuto(self):
        realtime.cambiar_modo_prueba(True, usuario=1)
        mediodia = datetime(2026, 10, 25, 12, 0, tzinfo=SV)
        segundo, evento = self.flujo(mediodia, 30)

        self.assertIsNotNone(evento, 'Abrir la llave 30 s debe detectarse como fuga')
        self.assertLess(segundo, 60)
        self.assertEqual(evento['titulo'], 'Posible Fuga')
        (fuga,) = self.supabase.tables['fuga']
        self.assertEqual((fuga['metodo_deteccion'], fuga['tipo_fuga'], fuga['id_sensor_flujo']),
                         ('flujo_nocturno', 'posible_fuga', 4))
        (alerta,) = self.alertas(ORIGIN)
        self.assertEqual(alerta['datos_adicionales']['titulo'], 'Posible Fuga')
        self.assertTrue(alerta['datos_adicionales']['modo_prueba'])
        self.assertIn('Modo de Prueba Nocturna', alerta['mensaje'])
        self.assertEqual(alerta['id_fuga'], fuga['id_fuga'])
        admins = [n for n in self.supabase.tables['notificacion'] if n['id_alerta'] == alerta['id_alerta']]
        self.assertEqual([n['id_usario_destino'] for n in admins], [1])
        # La web del admin se entera al instante (campana y lista de avisos).
        self.assertIn(('sensor-readings-admin', mock.ANY), self.enviados)

    def test_entre_3_y_6_am_detecta_sin_modo_prueba(self):
        _, evento = self.flujo(datetime(2026, 10, 25, 3, 30, tzinfo=SV), 30)
        self.assertIsNotNone(evento)
        self.assertFalse(evento['modo_prueba'])

    def test_de_dia_sin_modo_prueba_no_es_fuga(self):
        _, evento = self.flujo(datetime(2026, 10, 25, 12, 0, tzinfo=SV), 40)
        self.assertIsNone(evento)
        self.assertNotIn('fuga', self.supabase.tables)

    def test_un_chorro_corto_no_es_fuga(self):
        realtime.cambiar_modo_prueba(True)
        _, evento = self.flujo(datetime(2026, 10, 25, 4, 0, tzinfo=SV), 10)
        self.assertIsNone(evento)

    def test_no_repite_el_aviso_mientras_dura_la_espera(self):
        realtime.cambiar_modo_prueba(True)
        inicio = datetime(2026, 10, 25, 4, 0, tzinfo=SV)
        self.flujo(inicio, 30)
        _, otro = self.flujo(inicio + timedelta(seconds=32), 60)
        self.assertIsNone(otro)
        self.assertEqual(len(self.supabase.tables['fuga']), 1)


class PresionCriticaTests(TiempoRealTestCase):
    """PB026 · Escenario 2: fuga crítica por caída de presión con flujo constante."""

    def presion(self, momento, valor, alerta_esp32=False):
        return realtime.revisar_lectura(1, 'presion', valor, id_sensor=1, momento=momento, alerta_esp32=alerta_esp32)

    def test_caida_repentina_con_flujo_constante_es_fuga_critica(self):
        t = datetime(2026, 10, 25, 14, 0, tzinfo=SV)
        for s in range(0, 12, 2):  # presión normal y flujo constante
            realtime.revisar_lectura(1, 'flujo', 2.0, id_sensor=4, momento=t + timedelta(seconds=s))
            self.assertIsNone(self.presion(t + timedelta(seconds=s), 9.5))
        realtime.revisar_lectura(1, 'flujo', 2.1, id_sensor=4, momento=t + timedelta(seconds=12))
        evento = self.presion(t + timedelta(seconds=12), 3.2)

        self.assertIsNotNone(evento)
        self.assertEqual(evento['titulo'], 'Fuga Crítica por Presión')
        (alerta,) = self.alertas(ORIGIN)
        self.assertEqual(alerta['prioridad'], 'critica')
        self.assertEqual(alerta['datos_adicionales']['tipo'], 'fuga_critica_presion')
        self.assertAlmostEqual(alerta['datos_adicionales']['metricas']['caida_presion'], 6.3)
        (fuga,) = self.supabase.tables['fuga']
        self.assertEqual((fuga['metodo_deteccion'], fuga['id_sensor_presion']), ('caida_presion', 1))
        # El estado del sensor cambia en el panel del admin (al cargar y en vivo).
        self.assertEqual(realtime.estados_sensores()[1]['estado'], 'Fuga crítica')
        vivo = [p for g, p in self.enviados if g == 'sensor-readings-admin']
        self.assertEqual((vivo[0]['id_sensor'], vivo[0]['estado_sensor']), (1, 'Fuga crítica'))

    def test_sin_flujo_no_es_fuga_critica(self):
        t = datetime(2026, 10, 25, 14, 0, tzinfo=SV)
        self.presion(t, 9.5)
        self.assertIsNone(self.presion(t + timedelta(seconds=2), 3.0))

    def test_flujo_variable_no_es_fuga_critica(self):
        t = datetime(2026, 10, 25, 14, 0, tzinfo=SV)
        for s, valor in ((0, 0.6), (4, 3.5), (8, 1.0)):
            realtime.revisar_lectura(1, 'flujo', valor, momento=t + timedelta(seconds=s))
        self.presion(t, 9.5)
        self.assertIsNone(self.presion(t + timedelta(seconds=8), 3.0))

    def test_presion_que_baja_poco_a_poco_no_es_fuga(self):
        t = datetime(2026, 10, 25, 14, 0, tzinfo=SV)
        for s, valor in enumerate((5.8, 5.6, 5.3, 5.1, 4.9, 4.7)):
            realtime.revisar_lectura(1, 'flujo', 2.0, momento=t + timedelta(seconds=2 * s))
            self.assertIsNone(self.presion(t + timedelta(seconds=2 * s), valor))

    def test_aviso_del_esp32_cuenta_como_caida(self):
        t = datetime(2026, 10, 25, 14, 0, tzinfo=SV)
        for s in (0, 2, 4):
            realtime.revisar_lectura(1, 'flujo', 2.0, momento=t + timedelta(seconds=s))
        self.assertIsNotNone(self.presion(t + timedelta(seconds=4), 4.0, alerta_esp32=True))


class AvisoAlSectorTests(TiempoRealTestCase):
    """PB027 · Escenario 1: notificación al sector en menos de 10 s y en "Alertas Activas"."""

    def detectar(self):
        realtime.cambiar_modo_prueba(True)
        inicio = time.monotonic()
        _, evento = self.flujo(datetime(2026, 10, 25, 4, 0, tzinfo=SV), 30, vivienda=2)
        return evento, time.monotonic() - inicio

    def test_cada_vecino_del_sector_recibe_el_aviso_en_menos_de_10_s(self):
        evento, segundos = self.detectar()
        self.assertIsNotNone(evento)
        self.assertLess(segundos, 10)

        (aviso,) = self.alertas(sector.ORIGEN_SECTOR)
        self.assertEqual(aviso['datos_adicionales']['titulo'], 'Fuga detectada en su sector')
        self.assertTrue(aviso['mensaje'].startswith('Fuga detectada en su sector'))
        self.assertEqual(aviso['datos_adicionales']['id_alerta_origen'], evento['id_alerta'])
        destinos = sorted(n['id_usario_destino'] for n in self.supabase.tables['notificacion']
                          if n['id_alerta'] == aviso['id_alerta'])
        self.assertEqual(destinos, [10, 11])  # Sector Norte; el vecino del Sector Sur no

        en_vivo = {g: p for g, p in self.enviados if g.startswith('sensor-vivienda-')}
        self.assertEqual(set(en_vivo), {'sensor-vivienda-1', 'sensor-vivienda-2'})
        self.assertEqual(en_vivo['sensor-vivienda-1']['titulo'], 'Fuga detectada en su sector')
        self.assertEqual(en_vivo['sensor-vivienda-1']['para'], 'residente')

    def test_el_aviso_al_vecino_no_revela_la_casa_ni_el_titular(self):
        self.detectar()
        (aviso,) = self.alertas(sector.ORIGEN_SECTOR)
        for privado in ('NIC-0002', 'Pasaje 2', 'Titular Dos'):
            self.assertNotIn(privado, aviso['mensaje'])
            self.assertNotIn(privado, str(aviso['datos_adicionales']))

    def test_aparece_en_alertas_activas_del_residente(self):
        self.detectar()
        activas = sector.alertas_activas(10)
        self.assertEqual([a['titulo'] for a in activas], ['Fuga detectada en su sector'])
        self.assertEqual(sector.alertas_activas(12), [])

    def test_la_prediccion_por_horas_tambien_avisa_al_sector(self):
        resumen = sector.avisar_sector('Sector Sur', 99, 'Posible Fuga')
        self.assertEqual(resumen['avisados'], 1)
        self.assertLess(resumen['segundos'], 10)


class ModoPruebaApiTests(TiempoRealTestCase):
    def setUp(self):
        super().setUp()
        usuario = Rol.objects.create(nombre_rol='user')
        admin = Rol.objects.create(nombre_rol='admin')  # id 2 = administrador
        self.admin = Usuario.objects.create_user(email='a@example.com', nombre='A', apellido='A',
                                                 password='password-segura', rol=admin, supabase_id=1)
        self.user = Usuario.objects.create_user(email='u@example.com', nombre='U', apellido='U',
                                                password='password-segura', rol=usuario, supabase_id=10)
        self.client = APIClient()

    def test_el_admin_enciende_y_apaga_el_modo_prueba(self):
        self.client.force_authenticate(self.admin)
        url = reverse('ml_engine:leak_night_test_mode')
        self.assertFalse(self.client.get(url).json()['activo'])
        self.assertTrue(self.client.post(url, {'activo': True}, format='json').json()['activo'])
        self.assertTrue(realtime.modo_prueba_activo())
        self.assertFalse(self.client.post(url, {'activo': False}, format='json').json()['activo'])
        self.assertEqual(self.client.post(url, {'activo': 'si'}, format='json').status_code, 400)

    def test_un_residente_no_puede_cambiarlo(self):
        self.client.force_authenticate(self.user)
        url = reverse('ml_engine:leak_night_test_mode')
        self.assertEqual(self.client.post(url, {'activo': True}, format='json').status_code, 403)
        self.assertFalse(realtime.modo_prueba_activo())

    @override_settings(LEAK_RT_FLOW_SECONDS=10)
    def test_los_umbrales_se_ajustan_desde_el_env(self):
        realtime.cambiar_modo_prueba(True)
        segundo, evento = self.flujo(datetime(2026, 10, 25, 12, 0, tzinfo=SV), 20)
        self.assertEqual(segundo, 10)
        self.assertIsNotNone(evento)
