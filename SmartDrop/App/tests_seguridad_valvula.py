"""Pruebas de: válvula desde la app (control, temporizador, log, recuperación), aviso de reportes,
horas locales, rate limiting y protección contra inyección de filtros."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase as DjangoTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import preferencias, supabase_client, valve_service
from .api.authentication import MobileUser
from .models import Rol, Usuario
from .templatetags.sd_ui import duracion_texto, fecha_hora_local, hora_local

TOPIC = 'smartdrop/1/valvula/comando'


class TestCase(DjangoTestCase):
    def _pre_setup(self):
        super()._pre_setup()
        cache.clear()

    def tearDown(self):
        for key in list(valve_service._REGISTRY):
            valve_service.cancel_timer(key)
        super().tearDown()


def _admin_app():
    return MobileUser({'id_usuario': 77, 'correo': 'admin@app.com', 'id_rol': 2, 'nombre_rol': 'admin'})


def _usuario_app():
    return MobileUser({'id_usuario': 78, 'correo': 'user@app.com', 'id_rol': 1, 'nombre_rol': 'user'})


def _valvula(estado='cerrada', **extra):
    return [{'id_valvula': 1, 'nombre': 'Principal', 'estado_actual': estado,
             'topic_mqtt_comando': TOPIC, 'ultima_apertura': None, **extra}]


# ── Válvula desde la app ─────────────────────────────────────────────────────

@patch('App.api.views_valves.publish_command')
@patch('App.valve_service.supabase_client.insert')
@patch('App.valve_service.supabase_client.update')
@patch('App.valve_service.supabase_client.select')
class MobileValvulaControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=_admin_app())

    def test_control_abrir_con_duracion_publica_registra_y_arranca_temporizador(self, select, update, insert, publish):
        select.return_value = _valvula()
        insert.return_value = {'id_log_valvula': 5, 'fecha_hora': '2026-10-22T10:00:00+00:00'}

        response = self.client.post(
            '/api/valvula/1/control/', {'accion': 'ABRIR', 'duracion': 30, 'usuario': 'otro', 'origen': 'x'},
            format='json', REMOTE_ADDR='10.0.0.8',
        )

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['estado_actual'], 'abierta')
        self.assertTrue(body['log_registrado'])
        self.assertEqual(body['temporizador_activo']['duracion_programada'], 30)
        self.assertGreaterEqual(body['temporizador_activo']['segundos_restantes'], 29)
        publish.assert_called_once_with(TOPIC, 'abrir')
        self.assertEqual(update.call_args.args[1]['estado_actual'], 'abierta')
        log = insert.call_args.args[1]
        self.assertEqual(insert.call_args.args[0], 'log_valvula')
        self.assertEqual(log['origen_accion'], 'app')
        self.assertEqual(log['id_usuario'], 77)  # del token, nunca el "usuario" que manda la app
        self.assertEqual(log['tipo_activacion'], 'temporizado')
        self.assertEqual(log['duracion_programada'], 30)
        self.assertEqual(log['ip_dispositivo'], '10.0.0.8')
        self.assertTrue(valve_service.timer_info(1)['timer_activo'])

    def test_estado_devuelve_la_cuenta_regresiva(self, select, update, insert, publish):
        select.return_value = _valvula()
        self.client.post('/api/valvula/1/control/', {'accion': 'ABRIR', 'duracion': 60}, format='json')
        select.return_value = _valvula('abierta', ultima_apertura=timezone.now().isoformat())
        with patch('App.api.views_valves.supabase_client.select', return_value=select.return_value):
            response = self.client.get('/api/valvula/1/estado/')

        self.assertEqual(response.status_code, 200)
        temporizador = response.json()['temporizador_activo']
        self.assertEqual(temporizador['duracion_programada'], 60)
        self.assertGreater(temporizador['segundos_restantes'], 55)

    def test_control_cerrar_cancela_el_temporizador_y_registra(self, select, update, insert, publish):
        select.return_value = _valvula()
        self.client.post('/api/valvula/1/control/', {'accion': 'ABRIR', 'duracion': 60}, format='json')
        abierta = (timezone.now() - timedelta(seconds=12)).isoformat()
        select.return_value = _valvula('abierta', ultima_apertura=abierta)

        response = self.client.post('/api/valvula/1/control/', {'accion': 'CERRAR', 'duracion': 0}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['temporizador_activo'])
        self.assertFalse(valve_service.timer_info(1)['timer_activo'])
        log = insert.call_args.args[1]
        self.assertEqual(log['accion'], 'cerrar')
        self.assertEqual(log['razon'], 'Cierre manual durante temporizador')
        self.assertGreaterEqual(log['duracion_real'], 12)
        self.assertIsNone(update.call_args.args[1]['ultima_apertura'])

    def test_rutas_abrir_y_cerrar_siguen_funcionando(self, select, update, insert, publish):
        select.return_value = _valvula()
        self.assertEqual(self.client.post('/api/valvula/1/abrir/', {}, format='json').status_code, 200)
        self.assertEqual(self.client.post('/api/valvula/1/cerrar-remoto/', {}, format='json').status_code, 200)
        self.assertEqual([c.args[1] for c in publish.call_args_list], ['abrir', 'cerrar'])

    def test_comandos_o_duraciones_invalidas_dan_400(self, select, update, insert, publish):
        select.return_value = _valvula()
        for cuerpo in ({'accion': 'EXPLOTAR'}, {'accion': 'ABRIR', 'duracion': 3},
                       {'accion': 'ABRIR', 'duracion': 500}, {'accion': 'ABRIR', 'duracion': 'abc'}):
            response = self.client.post('/api/valvula/1/control/', cuerpo, format='json')
            self.assertEqual(response.status_code, 400, cuerpo)
            self.assertIn('error', response.json())
        publish.assert_not_called()

    def test_valvula_inexistente_da_404(self, select, update, insert, publish):
        select.return_value = []
        response = self.client.post('/api/valvula/9/control/', {'accion': 'ABRIR'}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_usuario_comun_no_puede_mover_la_valvula(self, select, update, insert, publish):
        self.client.force_authenticate(user=_usuario_app())
        response = self.client.post('/api/valvula/1/control/', {'accion': 'ABRIR'}, format='json')
        self.assertEqual(response.status_code, 403)
        publish.assert_not_called()

    @override_settings(RATELIMIT_OVERRIDES={'valvula_comando': '2/m'})
    def test_comandos_de_valvula_tienen_limite(self, select, update, insert, publish):
        select.return_value = _valvula()
        codes = [
            self.client.post('/api/valvula/1/control/', {'accion': 'CERRAR'}, format='json').status_code
            for _ in range(3)
        ]
        self.assertEqual(codes, [200, 200, 429])


class ValvulaServicioTests(TestCase):
    def test_parse_duracion(self):
        self.assertIsNone(valve_service.parse_duracion(None))
        self.assertIsNone(valve_service.parse_duracion(''))
        self.assertIsNone(valve_service.parse_duracion(0))
        self.assertEqual(valve_service.parse_duracion('45'), 45)
        for invalida in (4, 121, 'x', True, float('nan')):
            with self.assertRaises(ValueError):
                valve_service.parse_duracion(invalida)

    @patch('App.valve_service.supabase_client.insert')
    @patch('App.valve_service.supabase_client.update')
    @patch('App.valve_service.supabase_client.select')
    def test_recuperacion_cierra_si_el_tiempo_ya_paso(self, select, update, insert):
        inicio = (timezone.now() - timedelta(seconds=90)).isoformat()

        def fake_select(table, columns='*', params=None):
            if table == 'log_valvula':
                return [{'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'temporizado',
                         'fecha_hora': inicio, 'duracion_programada': 30}]
            return _valvula('abierta', ultima_apertura=inicio)
        select.side_effect = fake_select
        publish = []

        resultado = valve_service.recuperar_temporizadores(publish=lambda topic, cmd: publish.append(cmd))

        self.assertEqual(resultado['cerradas'], [1])
        self.assertEqual(publish, ['cerrar'])
        self.assertEqual(update.call_args.args[1]['estado_actual'], 'cerrada')
        self.assertIn('servidor no estaba disponible', insert.call_args.args[1]['razon'])

    @patch('App.valve_service.supabase_client.select')
    def test_recuperacion_reprograma_si_aun_falta_tiempo(self, select):
        inicio = (timezone.now() - timedelta(seconds=10)).isoformat()

        def fake_select(table, columns='*', params=None):
            if table == 'log_valvula':
                return [{'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'temporizado',
                         'fecha_hora': inicio, 'duracion_programada': 120}]
            return _valvula('abierta')
        select.side_effect = fake_select

        resultado = valve_service.recuperar_temporizadores(publish=lambda *a: None)

        self.assertEqual(resultado['reprogramadas'], [1])
        restante = valve_service.timer_info(1)['timer_remaining']
        self.assertTrue(105 <= restante <= 110, restante)

    @patch('App.valve_service.supabase_client.select')
    def test_recuperacion_ignora_aperturas_manuales(self, select):
        def fake_select(table, columns='*', params=None):
            if table == 'log_valvula':
                return [{'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'manual',
                         'fecha_hora': timezone.now().isoformat(), 'duracion_programada': None}]
            return _valvula('abierta')
        select.side_effect = fake_select
        self.assertEqual(valve_service.recuperar_temporizadores(publish=lambda *a: None),
                         {'cerradas': [], 'reprogramadas': []})


# ── Reportes: aviso al autor ─────────────────────────────────────────────────

class AvisoReporteTests(TestCase):
    def setUp(self):
        admin_role = Rol.objects.create(id_rol=2, nombre_rol='admin')
        self.admin = Usuario.objects.create_user(
            email='admin-rep@example.com', nombre='Admin', apellido='R', password='password-segura', rol=admin_role,
        )
        self.admin.supabase_id = 4
        self.admin.save(update_fields=['supabase_id'])

    @patch('App.views_reportes.supabase_client.insert')
    @patch('App.views_reportes.supabase_client.update')
    @patch('App.views_reportes.supabase_client.select')
    def test_resolver_un_reporte_avisa_al_autor(self, select, update, insert):
        select.return_value = [{'id_reporte': 12, 'id_usuario': 30, 'estado': 'pendiente', 'tipo_problema': 'sin_agua'}]
        insert.side_effect = lambda table, data, *a, **k: {'id_alerta': 99} if table == 'alerta' else {}
        self.client.force_login(self.admin)

        self.client.post(reverse('admin_reporte_update', args=[12]), {'estado': 'resuelto'})

        tablas = [c.args[0] for c in insert.call_args_list]
        self.assertEqual(tablas, ['reporte_mensaje', 'alerta', 'notificacion'])
        alerta = insert.call_args_list[1].args[1]
        self.assertEqual(alerta['tipo_alerta'], 'reporte_actualizado')
        self.assertIn('resuelto', alerta['mensaje'])
        self.assertEqual(alerta['datos_adicionales']['id_usuario'], 30)
        self.assertEqual(insert.call_args_list[2].args[1]['id_usario_destino'], 30)

    def test_el_aviso_es_personal(self):
        alerta = {'datos_adicionales': {'origen': preferencias.REPORTE_USUARIO_ORIGIN, 'id_usuario': 30}}
        self.assertFalse(preferencias.es_reporte_ajeno(alerta, 30))
        self.assertTrue(preferencias.es_reporte_ajeno(alerta, 31))


# ── Horas locales ────────────────────────────────────────────────────────────

class HoraLocalTests(DjangoTestCase):
    def test_filtros_de_hora_usan_el_salvador(self):
        self.assertEqual(fecha_hora_local('2026-10-22T04:32:28+00:00'), '21/10/2026 22:32:28')
        self.assertEqual(hora_local('2026-10-22T04:32:28+00:00'), '22:32:28')
        self.assertEqual(fecha_hora_local('sin fecha'), 'sin fecha')
        self.assertEqual(duracion_texto(95), '1 min 35 s')
        self.assertEqual(duracion_texto(3700), '1 h 1 min')
        self.assertEqual(duracion_texto(None), '')


# ── Rate limiting ────────────────────────────────────────────────────────────

class RateLimitTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    @patch('App.api.views_auth.authenticate', return_value=None)
    def test_login_movil_bloquea_fuerza_bruta_por_cuenta(self, authenticate):
        codes = []
        for intento in range(11):
            # Cada intento "cambia" de X-Forwarded-For: no debe servir para saltarse el límite.
            response = self.api.post(
                reverse('mobile_login'), {'correo': 'victima@example.com', 'contrasena': f'x{intento}'},
                format='json', HTTP_X_FORWARDED_FOR=f'1.2.3.{intento}',
            )
            codes.append(response.status_code)
        self.assertEqual(codes[:10], [400] * 10)
        self.assertEqual(codes[10], 429)
        self.assertIn('Demasiadas solicitudes', response.json()['error'])
        self.assertIn('Retry-After', response)

    @override_settings(RATELIMIT_OVERRIDES={'login_cuenta': '3/m'})
    @patch('App.forms.authenticate', return_value=None)
    def test_login_web_muestra_aviso_al_superar_el_limite(self, authenticate):
        for _ in range(3):
            self.client.post(reverse('login'), {'email': 'victima@example.com', 'password': 'x'})
        response = self.client.post(reverse('login'), {'email': 'victima@example.com', 'password': 'x'})
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Demasiadas solicitudes', status_code=429)
        self.assertEqual(authenticate.call_count, 3)

    @override_settings(RATELIMIT_ENABLED=False)
    @patch('App.api.views_auth.authenticate', return_value=None)
    def test_se_puede_apagar_para_pruebas(self, authenticate):
        for _ in range(15):
            response = self.api.post(reverse('mobile_login'), {'correo': 'a@b.com', 'contrasena': 'x'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_api_login_legado_exige_json(self):
        response = self.client.post(reverse('api_login'), {'email': 'a@b.com', 'password': 'x'})
        self.assertEqual(response.status_code, 415)


@override_settings(DEVICE_API_TOKEN='token-de-prueba')
class TokenDispositivoTests(TestCase):
    LECTURA = {'id_sensor': 4, 'fecha_registro': '2026-10-22T10:00:00Z', 'valor': 1.5}

    def test_sin_token_se_rechaza(self):
        response = self.client.post(reverse('api_lectura'), data=self.LECTURA, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    @patch('App.views._broadcast_sensor_reading')
    @patch('App.views.supabase_client.insert', return_value={'id_lectura': 1})
    def test_con_token_correcto_se_guarda(self, insert, broadcast):
        response = self.client.post(
            reverse('api_lectura'), data=self.LECTURA, content_type='application/json',
            HTTP_X_DEVICE_TOKEN='token-de-prueba',
        )
        self.assertEqual(response.status_code, 201)
        insert.assert_called_once()


# ── Inyección de filtros (PostgREST) ─────────────────────────────────────────

class InyeccionTests(TestCase):
    def test_listas_de_ids_solo_aceptan_enteros(self):
        self.assertEqual(supabase_client.ids_in([3, '4']), 'in.(3,4)')
        for malo in (['1)'], ['1,2'], ['1 or 1=1'], ['-1'], []):
            with self.assertRaises(supabase_client.FiltroInvalido):
                supabase_client.ids_in(malo)

    def test_listas_de_texto_se_entrecomillan(self):
        self.assertEqual(supabase_client.in_list(['a"b', 'x,y)']), 'in.("a\\"b","x,y)")')

    def test_parametros_con_formato_no_permitido_no_llegan_a_supabase(self):
        with patch.object(supabase_client._session, 'request') as request:
            for params in ({'id;drop table usuario': 'eq.1'}, {'limit': '5;'}, {'correo': 'eq.a\x00b'},
                           {'id_usuario': ['eq.1']}):
                with self.assertRaises(supabase_client.FiltroInvalido):
                    supabase_client._request('GET', 'https://x.supabase.co/rest/v1/usuario', params=params)
            with self.assertRaises(supabase_client.FiltroInvalido):
                supabase_client._base_url('usuario;drop')
            request.assert_not_called()

    @patch('App.views_reportes.supabase_client.select', return_value=[])
    def test_filtro_de_alertas_ignora_texto_libre(self, select):
        user = Usuario.objects.create_user(email='u@example.com', nombre='U', apellido='U', password='password-segura')
        self.client.force_login(user)
        self.client.get(reverse('alertas_historial'), {'estado': 'pendiente,id_alerta.gt.0'})
        params = next(c.args[2] for c in select.call_args_list if c.args[0] == 'alerta')
        self.assertNotIn('estado_confirmacion', params)

    def test_comando_web_con_id_no_numerico_da_404(self):
        response = self.client.post('/valvula/1%29/comando/', {'comando': 'abrir'})
        self.assertEqual(response.status_code, 404)


class LogValvulaRespaldoTests(TestCase):
    @patch('App.valve_service.supabase_client.insert')
    def test_si_supabase_rechaza_el_origen_se_registra_como_web(self, insert):
        insert.side_effect = [supabase_client.SupabaseError('violates check constraint', status_code=400), {'id_log_valvula': 1}]
        fila = valve_service.insertar_log({'accion': 'abrir', 'origen_accion': 'app', 'razon': None})
        self.assertEqual(fila, {'id_log_valvula': 1})
        respaldo = insert.call_args.args[1]
        self.assertEqual(respaldo['origen_accion'], 'web')
        self.assertEqual(respaldo['razon'], 'Origen: app')
