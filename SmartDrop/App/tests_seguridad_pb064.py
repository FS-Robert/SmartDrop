"""Pruebas de PB064 (seguridad de la información): ingesta MQTT en el servidor (DEF-17) y respaldos."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from App import ingesta_mqtt, respaldos
from ml_engine.leaks import realtime
from ml_engine.tests import FakeSupabase

CLAVE = Fernet.generate_key().decode()


class IngestaMqttTests(TestCase):
    """DEF-17: el servidor guarda lo que manda el ESP32 por MQTT, sin consumer.js en una laptop."""

    databases = {'default', 'timeseries'}

    def setUp(self):
        realtime.reiniciar()
        self.supabase = FakeSupabase(
            tanque=[{'id_tanque': 1, 'id_sensor_nivel': 2}],
            valvula=[{'id_valvula': 1, 'estado_actual': 'cerrada'}],
        )
        self.enterContext(self.supabase.patch())
        self.en_vivo = self.enterContext(mock.patch('App.views._broadcast_sensor_reading'))
        self.revisar = self.enterContext(mock.patch('ml_engine.leaks.realtime.revisar_lectura'))

    def test_flujo_se_guarda_se_emite_y_pasa_por_el_detector(self):
        ingesta_mqtt.procesar_mensaje('smartdrop/1/flujo', json.dumps({'id_sensor': 4, 'valor': 1.75}))
        (lectura,) = self.supabase.tables['lectura']
        self.assertEqual((lectura['id_sensor'], lectura['id_vivienda'], lectura['valor']), (4, 1, 1.75))
        self.en_vivo.assert_called_once()
        self.revisar.assert_called_once_with(1, 'flujo', 1.75, id_sensor=4, alerta_esp32=False)

    def test_presion_lleva_el_aviso_del_esp32(self):
        ingesta_mqtt.procesar_mensaje('smartdrop/1/presion', json.dumps(
            {'id_sensor': 1, 'valor': 3.1, 'estado': 'BAJA', 'alerta_fuga': True}))
        self.revisar.assert_called_once_with(1, 'presion', 3.1, id_sensor=1, alerta_esp32=True)

    def test_nivel_guarda_lectura_e_historial_del_tanque(self):
        ingesta_mqtt.procesar_mensaje('smartdrop/1/nivel', json.dumps(
            {'id_sensor': 2, 'distancia_cm': 12.5, 'porcentaje': 58}))
        (historial,) = self.supabase.tables['historial_nivel_tanque']
        self.assertEqual((historial['id_tanque'], historial['nivel_cm']), (1, 12.5))
        self.assertEqual(self.supabase.tables['lectura'][0]['valor'], 12.5)

    def test_tds_malo_abre_un_incidente_una_sola_vez(self):
        self.enterContext(mock.patch.dict('ml_engine.tests.PRIMARY_KEYS', {'incidente_calidad': 'id_incidente'}))
        for _ in range(2):
            ingesta_mqtt.procesar_mensaje('smartdrop/1/tds', json.dumps({'id_sensor': 3, 'valor': 700, 'calidad': 'Pobre'}))
        self.assertEqual(len(self.supabase.tables['incidente_calidad']), 1)
        self.assertEqual(self.supabase.tables['alerta'][0]['tipo_alerta'], 'calidad_agua')

    def test_estado_de_valvula_actualiza_y_registra_el_cambio(self):
        ingesta_mqtt.procesar_mensaje('smartdrop/1/valvula/estado', json.dumps({'id_valvula': 1, 'estado': 'abierta'}))
        self.assertEqual(self.supabase.tables['valvula'][0]['estado_actual'], 'abierta')
        (log,) = self.supabase.tables['log_valvula']
        self.assertEqual((log['estado_anterior'], log['estado_nuevo'], log['origen_accion']), ('cerrada', 'abierta', 'esp32'))

    def test_mensajes_malos_se_ignoran(self):
        for topico, cuerpo in (('smartdrop/1/flujo', 'no es json'), ('smartdrop/1/flujo', '{"id_sensor": "4", "valor": 1}'),
                               ('otro/1/flujo', '{"id_sensor": 4, "valor": 1}'), ('smartdrop/1/flujo', '{"id_sensor": 4}'),
                               ('smartdrop/1/flujo', '{"id_sensor": 4, "valor": "NaN"}')):
            ingesta_mqtt.procesar_mensaje(topico, cuerpo)
        self.assertNotIn('lectura', self.supabase.tables)

    @override_settings(MQTT_INGESTA_ENABLED=False)
    def test_apagada_no_arranca(self):
        with mock.patch('App.ingesta_mqtt.threading.Thread') as hilo:
            ingesta_mqtt.iniciar()
        hilo.assert_not_called()

    @override_settings(MQTT_INGESTA_ENABLED=True, MQTT_BROKER_HOST='broker.example', MQTT_USERNAME='u',
                       MQTT_PASSWORD='p', MQTT_CA_CERT='')
    def test_el_cliente_usa_tls_verificado(self):
        cliente = ingesta_mqtt._cliente()
        contexto = cliente._ssl_context
        self.assertTrue(contexto.check_hostname)
        self.assertEqual(contexto.verify_mode.name, 'CERT_REQUIRED')


class FakeStorage:
    """Bucket de Supabase Storage en memoria."""

    def __init__(self):
        self.objetos = {}

    def __call__(self, method, ruta, **kwargs):
        resp = mock.Mock(ok=True, status_code=200)
        if method == 'POST' and ruta.startswith('/object/list/'):
            resp.json.return_value = [{'name': n} for n in self.objetos]
        elif method == 'POST' and ruta.startswith('/object/'):
            self.objetos[ruta.rsplit('/', 1)[1]] = kwargs['data']
        elif method == 'GET':
            resp.content = self.objetos[ruta.rsplit('/', 1)[1]]
        elif method == 'DELETE':
            for nombre in kwargs['json']['prefixes']:
                self.objetos.pop(nombre, None)
        return resp


class RespaldosTests(TestCase):
    """PB064 · Escenario 3: respaldo automático, guardado fuera del servidor y restauración probada."""

    databases = {'default', 'timeseries'}

    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp(prefix='smartdrop-respaldo-'))
        self.addCleanup(shutil.rmtree, self.carpeta, ignore_errors=True)
        self.enterContext(override_settings(RESPALDO_CLAVE=CLAVE, RESPALDO_DIR=str(self.carpeta), RESPALDO_DIAS=14))
        self.supabase = FakeSupabase(
            usuario=[{'id_usuario': i, 'correo': f'u{i}@example.com', 'contrasena': '$2b$12$hash'} for i in range(1, 4)],
            lectura=[{'id_lectura': i, 'id_sensor': 4, 'valor': i / 10} for i in range(1, 2501)],
        )
        self.enterContext(self.supabase.patch())
        self.enterContext(mock.patch.object(respaldos, 'esquema_supabase',
                                            return_value={'usuario': ['id_usuario'], 'lectura': ['id_lectura']}))
        self.storage = FakeStorage()
        self.enterContext(mock.patch.object(respaldos, '_storage', side_effect=self.storage))

    def test_respaldo_cifrado_y_completo(self):
        ruta, manifiesto = respaldos.crear_respaldo()
        self.assertEqual(manifiesto['tablas'], {'usuario': 3, 'lectura': 2500})  # lee las 3 páginas de lectura
        crudo = ruta.read_bytes()
        self.assertNotIn(b'u1@example.com', crudo)  # cifrado: no se lee nada en texto plano
        _, tablas, sqlite_dbs = respaldos.abrir_respaldo(ruta)
        self.assertEqual(tablas['usuario'], self.supabase.tables['usuario'])
        self.assertEqual(len(tablas['lectura']), 2500)

    def test_incluye_copia_de_las_bases_sqlite(self):
        import sqlite3

        base = self.carpeta / 'servidor.sqlite3'
        conexion = sqlite3.connect(base)
        conexion.execute('CREATE TABLE gotita (texto TEXT)')
        conexion.execute("INSERT INTO gotita VALUES ('hola')")
        conexion.commit()
        conexion.close()
        bases = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(base)}}
        with mock.patch.object(respaldos.settings, 'DATABASES', bases),                 mock.patch.object(respaldos, 'connections', ['default']):
            ruta, _ = respaldos.crear_respaldo(tablas=['usuario'])
        _, _, sqlite_dbs = respaldos.abrir_respaldo(ruta)
        (copia,) = respaldos.guardar_sqlite(sqlite_dbs, self.carpeta / 'restaurada')
        self.assertEqual(sqlite3.connect(copia).execute('SELECT texto FROM gotita').fetchone(), ('hola',))

    def test_respaldo_alterado_o_con_otra_clave_se_rechaza(self):
        ruta, _ = respaldos.crear_respaldo()
        datos = bytearray(ruta.read_bytes())
        datos[100] ^= 1
        alterado = self.carpeta / 'alterado.tar.gz.enc'
        alterado.write_bytes(bytes(datos))
        with self.assertRaises(respaldos.RespaldoError):
            respaldos.abrir_respaldo(alterado)
        with override_settings(RESPALDO_CLAVE=Fernet.generate_key().decode()), self.assertRaises(respaldos.RespaldoError):
            respaldos.abrir_respaldo(ruta)

    def test_sin_clave_no_respalda(self):
        with override_settings(RESPALDO_CLAVE=''), self.assertRaises(respaldos.RespaldoError):
            respaldos.crear_respaldo()

    def test_comando_sube_el_respaldo_fuera_del_servidor(self):
        call_command('respaldar_base', stdout=mock.MagicMock())
        (nombre,) = self.storage.objetos
        self.assertTrue(nombre.startswith(respaldos.PREFIJO) and nombre.endswith(respaldos.EXTENSION))

    def test_simulacro_de_perdida_y_restauracion(self):
        """Se pierde la tabla, se baja el último respaldo de Storage y se restaura: los datos vuelven iguales."""
        call_command('respaldar_base', stdout=mock.MagicMock())
        originales = [dict(f) for f in self.supabase.tables['usuario']]
        for ruta in self.carpeta.glob('*.enc'):
            ruta.unlink()  # el servidor perdió también sus copias locales
        self.supabase.tables['usuario'] = []  # falla: se perdieron los datos

        def upsert(method, url, headers=None, params=None, json=None):
            self.assertIn('merge-duplicates', headers['Prefer'])
            self.supabase.tables.setdefault(url.rsplit('/', 1)[1], []).extend(json)
            return mock.Mock(ok=True, status_code=201, content=b'')

        with mock.patch('App.supabase_client._request', side_effect=upsert), \
                mock.patch('App.supabase_client._check_config'):
            call_command('restaurar_respaldo', '--ultimo', '--tablas', 'usuario', '--confirmar', stdout=mock.MagicMock())
        self.assertEqual(self.supabase.tables['usuario'], originales)

    def test_revisar_sin_confirmar_no_escribe(self):
        ruta, _ = respaldos.crear_respaldo()
        with mock.patch('App.supabase_client._request') as peticion:
            call_command('restaurar_respaldo', str(ruta), stdout=mock.MagicMock())
        peticion.assert_not_called()

    def test_conserva_solo_los_dias_configurados(self):
        from datetime import datetime, timedelta, timezone

        ahora = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
        for dias in (0, 5, 20, 40):
            ruta, _ = respaldos.crear_respaldo(tablas=['usuario'], incluir_sqlite=False, ahora=ahora - timedelta(days=dias))
            respaldos.subir(ruta)
        respaldos.limpiar_viejos(ahora=ahora)
        self.assertEqual(len(list(self.carpeta.glob('*.enc'))), 2)
        self.assertEqual(len(self.storage.objetos), 2)

    def test_restaurar_falla_claro_si_falta_el_archivo(self):
        with self.assertRaises(CommandError):
            call_command('restaurar_respaldo', str(self.carpeta / 'no-existe.enc'), stdout=mock.MagicMock())
