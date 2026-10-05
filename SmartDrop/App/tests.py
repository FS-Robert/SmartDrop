from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import supabase_client
from .backends import sync_user_from_supabase
from .forms import LoginForm
from .models import Rol, Usuario
from .views import _supabase_user_id, _valve_statistics


class TestCase(DjangoTestCase):
	"""Vacía la caché antes de cada prueba: queries.py cachea viviendas, sensores y alertas."""

	def _pre_setup(self):
		super()._pre_setup()
		cache.clear()


def select_by_table(**tables):
	"""Mock de supabase_client.select que responde según la tabla (las consultas corren en paralelo)."""
	def fake_select(table, columns='*', params=None):
		return tables.get(table, [])
	return fake_select


def _month_day(months_back, day=15):
	"""Fecha ISO del día `day` de hace `months_back` meses, para no depender de la fecha actual."""
	first = timezone.localdate().replace(day=1)
	for _ in range(months_back):
		first = (first - timedelta(days=1)).replace(day=1)
	return f'{first.replace(day=day).isoformat()}T10:00:00Z'

# Create your tests here.


class LoginFormTests(TestCase):
	@patch('App.forms.authenticate')
	def test_login_muestra_error_cuando_supabase_no_esta_disponible(self, authenticate):
		authenticate.side_effect = supabase_client.SupabaseError('No se pudo conectar con Supabase (NameResolutionError).')

		form = LoginForm(data={'email': 'ana.martinez@example.com', 'password': 'secreto123'})

		self.assertFalse(form.is_valid())
		self.assertIn('No se pudo validar el inicio de sesión', form.non_field_errors()[0])


class ConsumoViewTests(TestCase):
	@patch('App.views.supabase_client.insert')
	def test_api_lectura_valida_envia_datos_a_supabase(self, insert):
		insert.return_value = {'id_lectura': 1}
		response = self.client.post(
			reverse('api_lectura'),
			data={
				'id_sensor': 4,
				'fecha_registro': '2026-09-07T10:00:00Z',
				'valor': 21.5,
			},
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 201)
		insert.assert_called_once_with('lectura', {
			'id_sensor': 4,
			'fecha_registro': '2026-09-07T10:00:00Z',
			'valor': 21.5,
		})

	def test_api_lectura_rechaza_campos_adicionales(self):
		with self.assertLogs('App.views', level='WARNING'):
			response = self.client.post(
				reverse('api_lectura'),
				data={
					'id_sensor': 4,
					'fecha_registro': '2026-09-07T10:00:00Z',
					'valor': 21.5,
					'campo_extra': 'no permitido',
				},
				content_type='application/json',
			)

		self.assertEqual(response.status_code, 400)

	@patch('App.views.supabase_client.insert', side_effect=RuntimeError('Supabase no disponible'))
	def test_api_lectura_registra_error_de_supabase(self, insert):
		with self.assertLogs('App.views', level='ERROR'):
			response = self.client.post(
				reverse('api_lectura'),
				data={
					'id_sensor': 4,
					'fecha_registro': '2026-09-07T10:00:00Z',
					'valor': 21.5,
				},
				content_type='application/json',
			)

		self.assertEqual(response.status_code, 502)

	def test_sincronizacion_reutiliza_correo_local_existente(self):
		rol = Rol.objects.create(nombre_rol='user')
		local_user = Usuario.objects.create_user(
			email='usuario@example.com',
			nombre='Nombre local',
			apellido='Apellido local',
			password='password-segura',
			rol=rol,
		)

		synced_user = sync_user_from_supabase({
			'id_usuario': 9001,
			'correo': 'usuario@example.com',
			'nombre': 'Nombre remoto',
			'apellido': 'Apellido remoto',
			'contrasena': local_user.password,
			'id_rol': rol.id_rol,
		})

		self.assertEqual(synced_user.pk, local_user.pk)
		self.assertEqual(synced_user.supabase_id, 9001)
		self.assertEqual(Usuario.objects.count(), 1)

	def test_consumo_consulta_solo_viviendas_del_usuario(self):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='usuario@example.com',
			nombre='Usuario',
			apellido='Prueba',
			password='password-segura',
			rol=rol,
		)
		responses = [
			[{'id_vivienda': 17, 'nic': 'NIC-17', 'direccion': 'Casa propia'}],
			[{'id_vivienda': 17, 'fecha': f'{timezone.localdate().isoformat()}T10:00:00Z', 'consumo_total': 12}],
		]

		self.client.force_login(user)
		with patch('App.views.supabase_client.select', side_effect=responses) as select:
			response = self.client.get(reverse('consumo'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(select.call_count, 2)
		vivienda_call = select.call_args_list[0]
		consumo_call = select.call_args_list[1]
		self.assertEqual(vivienda_call.args[0], 'vivienda')
		self.assertEqual(vivienda_call.args[2]['id_usuario_propietario'], f'eq.{user.id_usuario}')
		self.assertEqual(consumo_call.args[0], 'consumo')
		self.assertEqual(consumo_call.args[2]['id_vivienda'], 'in.(17)')
		self.assertEqual(response.context['viviendas'][0]['id_vivienda'], 17)
		self.assertEqual(response.context['consumo']['valor_dia'], 12)

	@patch('App.views.supabase_client.select')
	def test_retroalimentacion_compara_mes_actual_con_mes_anterior(self, select):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='retro@example.com', nombre='Retro', apellido='Usuario',
			password='password-segura', rol=rol,
		)
		select.side_effect = [
			[{'id_vivienda': 17, 'nic': 'NIC-17', 'direccion': 'Casa propia'}],
			[
				{'fecha': _month_day(0), 'consumo_total': 12},
				{'fecha': _month_day(1), 'consumo_total': 20},
			],
		]
		self.client.force_login(user)

		response = self.client.get(reverse('retroalimentacion'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['retro']['mensaje'], 'Has reducido tu consumo')
		self.assertEqual(response.context['retro']['periodo_comparacion'], 'mes anterior')
		self.assertEqual(response.context['retro']['variacion_mes'], '-40.0%')

	@patch('App.views.supabase_client.insert')
	@patch('App.views.supabase_client.select')
	def test_consumo_elevado_registra_alerta_y_notificacion(self, select, insert):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='alerta@example.com', nombre='Alerta', apellido='Usuario',
			password='password-segura', rol=rol,
		)
		user.supabase_id = 77
		user.save(update_fields=['supabase_id'])
		select.side_effect = [
			[{'id_vivienda': 17, 'nic': 'NIC-17', 'direccion': 'Casa propia'}],
			[
				{'fecha': _month_day(0), 'consumo_total': 30},
				{'fecha': _month_day(1), 'consumo_total': 20},
			],
		]
		insert.side_effect = [{'id_alerta': 31}, None]
		self.client.force_login(user)

		response = self.client.get(reverse('retroalimentacion'))

		self.assertEqual(response.context['retro']['alerta'], 'Consumo elevado de agua')
		self.assertEqual(insert.call_args_list[0].args[0], 'alerta')
		self.assertEqual(insert.call_args_list[1].args[0], 'notificacion')
		self.assertEqual(insert.call_args_list[1].args[1]['id_usario_destino'], 77)

	@patch('App.views.supabase_client.select')
	def test_usuario_normal_ve_estado_de_valvula(self, select):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='estado@example.com', nombre='Estado', apellido='Usuario',
			password='password-segura', rol=rol,
		)
		select.side_effect = select_by_table(valvula=[{
			'id_valvula': 1,
			'nombre': 'Principal',
			'estado_actual': 'abierta',
			'ultima_apertura': timezone.now().isoformat(),
		}])
		self.client.force_login(user)

		response = self.client.get(reverse('dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['valvula']['estado'], 'ABIERTA')
		self.assertNotContains(response, 'Solo administradores pueden controlar la válvula')

	@patch('App.views.supabase_client.select')
	def test_api_estado_valvula_devuelve_estado_actual(self, select):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='apiestado@example.com', nombre='API', apellido='Estado',
			password='password-segura', rol=rol,
		)
		select.return_value = [{'id_valvula': 1, 'estado_actual': 'cerrada'}]
		self.client.force_login(user)

		response = self.client.get(reverse('api_estado_valvula'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['valvula']['estado'], 'CERRADA')

	@patch('App.views.supabase_client.select', return_value=[{
		'id_valvula': 1,
		'estado_actual': 'abierta',
		'ultima_apertura': timezone.now().isoformat(),
	}])
	def test_usuario_normal_puede_abrir_estado_del_sistema(self, select):
		rol = Rol.objects.create(nombre_rol='user')
		user = Usuario.objects.create_user(
			email='sistema@example.com', nombre='Sistema', apellido='Usuario',
			password='password-segura', rol=rol,
		)
		self.client.force_login(user)

		response = self.client.get(reverse('estado_sistema'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Válvula: ABIERTA')
		self.assertContains(response, 'Última actualización:')


class AdminValveViewTests(TestCase):
	def setUp(self):
		self.admin_role = Rol.objects.create(id_rol=2, nombre_rol='admin')
		self.user_role = Rol.objects.create(id_rol=3, nombre_rol='user')
		self.admin = Usuario.objects.create_user(
			email='admin@example.com', nombre='Admin', apellido='Prueba',
			password='password-segura', rol=self.admin_role,
		)
		self.admin.supabase_id = 4
		self.admin.save(update_fields=['supabase_id'])
		self.user = Usuario.objects.create_user(
			email='user@example.com', nombre='User', apellido='Prueba',
			password='password-segura', rol=self.user_role,
		)

	def test_usuario_normal_no_puede_abrir_el_panel(self):
		self.client.force_login(self.user)
		response = self.client.get(reverse('valvulas'))
		self.assertRedirects(response, reverse('dashboard'))

	@patch('App.views.supabase_client.get_user_by_email')
	def test_movimiento_sincroniza_el_id_remoto_del_administrador(self, get_user_by_email):
		self.admin.supabase_id = None
		self.admin.save(update_fields=['supabase_id'])
		get_user_by_email.return_value = {'id_usuario': 44}

		self.assertEqual(_supabase_user_id(self.admin), 44)
		self.admin.refresh_from_db()
		self.assertEqual(self.admin.supabase_id, 44)

	@patch('App.views.publish_command')
	@patch('App.views.supabase_client.insert')
	@patch('App.views.supabase_client.update')
	@patch('App.views.supabase_client.select')
	def test_admin_lista_todas_y_publica_comando(self, select, update, insert, publish):
		valves = [
			{'id_valvula': 1, 'nombre': 'Principal', 'ping_gpio': 26,
			 'estado_actual': 'cerrada', 'estado_operativo': 'operativa',
			 'topic_mqtt_comando': 'smartdrop/1/valvula/comando'},
			{'id_valvula': 2, 'nombre': 'Jardín', 'ping_gpio': 27,
			 'estado_actual': 'abierta', 'estado_operativo': 'operativa',
			 'topic_mqtt_comando': 'smartdrop/2/valvula/comando'},
		]
		movimientos = [
			{
				'id_log_valvula': 10, 'id_valvula': 1, 'accion': 'abrir',
				'estado_anterior': 'cerrada', 'estado_nuevo': 'abierta',
				'tipo_activacion': 'manual', 'id_usuario': 7,
				'fecha_hora': '2026-09-04T10:00:00',
			},
			{
				'id_log_valvula': 11, 'id_valvula': 2, 'accion': 'cerrar',
				'estado_anterior': 'abierta', 'estado_nuevo': 'cerrada',
				'tipo_activacion': 'automatico', 'id_usuario': None,
				'fecha_hora': '2026-09-04T09:00:00',
			},
		]
		usuarios = [{'id_usuario': 7, 'nombre': 'Ana', 'apellido': 'López', 'correo': 'ana@example.com'}]
		select.side_effect = select_by_table(valvula=valves, log_valvula=movimientos, usuario=usuarios)
		self.client.force_login(self.admin)

		response = self.client.get(reverse('valvulas'))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['valvulas'], valves)
		self.assertEqual(response.context['movimientos'][0]['usuario_mostrar'], 'Ana López')
		self.assertEqual(response.context['movimientos'][1]['usuario_mostrar'], 'Sistema automático')
		self.assertEqual(response.context['movimientos'][1]['tipo_mostrar'], 'Automático')

		response = self.client.post(
			reverse('valvula_comando', kwargs={'valvula_id': 1}),
			{'comando': 'abrir'},
		)
		self.assertRedirects(response, reverse('valvulas'))
		publish.assert_called_once_with('smartdrop/1/valvula/comando', 'abrir')
		update.assert_called_once()
		self.assertEqual(update.call_args.args[1]['estado_actual'], 'abierta')
		insert.assert_called_once()
		self.assertEqual(insert.call_args.args[1]['id_usuario'], 4)
		self.assertEqual(insert.call_args.args[1]['ip_dispositivo'], '127.0.0.1')

	def test_estadisticas_de_valvulas_calculan_aperturas_y_duracion(self):
		statistics = _valve_statistics([
			{
				'accion': 'abrir', 'tipo_activacion': 'manual',
				'fecha_hora': '2026-09-07T10:00:00+00:00', 'duracion_real': 7200,
			},
			{
				'accion': 'abrir', 'tipo_activacion': 'automatico',
				'fecha_hora': '2026-09-07T11:00:00+00:00', 'duracion_real': 3600,
			},
		], now=timezone.now().replace(month=9, day=7, hour=12, minute=0, second=0, microsecond=0))

		self.assertEqual(statistics['total_aperturas_mes'], 2)
		self.assertEqual(statistics['tiempo_promedio_abierta'], 1.5)
		self.assertEqual(statistics['porcentaje_manual'], 50)
		self.assertEqual(statistics['porcentaje_automatico'], 50)

	@patch('App.views.publish_command')
	@patch('App.views.supabase_client.insert')
	@patch('App.views.supabase_client.update')
	@patch('App.views.supabase_client.select')
	def test_comando_ajax_devuelve_usuario_ip_y_estado(self, select, update, insert, publish):
		select.return_value = [{
			'id_valvula': 1,
			'nombre': 'Principal',
			'estado_actual': 'cerrada',
			'topic_mqtt_comando': 'smartdrop/1/valvula/comando',
		}]
		insert.return_value = {
			'fecha_hora': '2026-09-07T12:00:00+00:00',
		}
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('valvula_comando', kwargs={'valvula_id': 1}),
			{'comando': 'abrir'},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
			REMOTE_ADDR='192.168.1.15',
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload['id_usuario'], 4)
		self.assertEqual(payload['ip_dispositivo'], '192.168.1.15')
		self.assertEqual(payload['estado_nuevo'], 'abierta')
		self.assertEqual(insert.call_args.args[1]['id_usuario'], 4)
		self.assertEqual(insert.call_args.args[1]['ip_dispositivo'], '192.168.1.15')

	@patch('App.views.supabase_client.select')
	def test_historial_se_puede_exportar_a_csv(self, select):
		select.side_effect = select_by_table(
			valvula=[{'id_valvula': 1, 'nombre': 'Principal'}],
			log_valvula=[{
				'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'manual',
				'id_usuario': None, 'fecha_hora': '2026-09-07T10:00:00+00:00',
				'origen_accion': 'web',
			}],
		)
		self.client.force_login(self.admin)

		response = self.client.get(reverse('valvulas'), {'formato': 'csv'})

		self.assertEqual(response.status_code, 200)
		self.assertIn('text/csv', response['Content-Type'])
		self.assertIn('Principal', response.content.decode())

	@patch('App.views.supabase_client.insert')
	@patch('App.views.supabase_client.select')
	def test_actividad_inusual_genera_alerta_y_notificacion(self, select, insert):
		movements = [
			{
				'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'manual',
				'id_usuario': 4, 'fecha_hora': timezone.now().isoformat(),
				'origen_accion': 'web',
			}
		] * 21
		select.side_effect = select_by_table(
			valvula=[{'id_valvula': 1, 'nombre': 'Principal'}],
			log_valvula=movements,
			usuario=[{'id_usuario': 4, 'nombre': 'Admin', 'apellido': 'Prueba'}],
		)
		insert.side_effect = [{'id_alerta': 9}, None]
		self.client.force_login(self.admin)

		response = self.client.get(reverse('valvulas'))

		self.assertEqual(response.status_code, 200)
		self.assertIn('21 cambios', response.context['alerta_actividad'])
		self.assertEqual(insert.call_count, 2)
		self.assertEqual(insert.call_args_list[1].args[0], 'notificacion')


class MobileSearchAndValveLogsTests(TestCase):
	def setUp(self):
		admin_role = Rol.objects.create(id_rol=2, nombre_rol='admin')
		user_role = Rol.objects.create(id_rol=3, nombre_rol='user')
		self.admin = Usuario.objects.create_user(
			email='mobile-admin@example.com', nombre='Admin', apellido='App',
			password='password-segura', rol=admin_role,
		)
		self.user = Usuario.objects.create_user(
			email='mobile-user@example.com', nombre='Usuario', apellido='App',
			password='password-segura', rol=user_role,
		)
		self.client = APIClient()

	def test_busqueda_movil_devuelve_secciones_para_usuario(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.get(reverse('mobile_buscar'), {'q': 'calidad'})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['ok'], True)
		titles = [section['titulo'] for section in response.json()['secciones']]
		self.assertIn('Calidad del Agua - TDS', titles)

	def test_busqueda_movil_incluye_secciones_admin_solo_para_admin(self):
		self.client.force_authenticate(user=self.user)
		user_response = self.client.get(reverse('mobile_buscar'), {'q': 'panel administrativo'})
		self.assertEqual(user_response.json()['secciones'], [])

		self.client.force_authenticate(user=self.admin)
		admin_response = self.client.get(reverse('mobile_buscar'), {'q': 'panel administrativo'})

		self.assertEqual(admin_response.status_code, 200)
		self.assertEqual(admin_response.json()['secciones'][0]['titulo'], 'Panel Administrativo')

	@patch('App.api.views_valves.supabase_client.select')
	def test_logs_movil_mapea_campos_y_duracion(self, select):
		select.side_effect = [
			[{'id_valvula': 9}],
			[{
				'id_valvula': 9,
				'accion': 'abrir',
				'estado_anterior': 'cerrada',
				'estado_nuevo': 'abierta',
				'tipo_activacion': 'temporizado',
				'id_usuario': 14,
				'fecha_hora': '2026-10-02T12:00:00Z',
				'origen_accion': 'App (Temporizado)',
				'duracion_programada': 30,
			}],
			[{'id_usuario': 14, 'nombre': 'Ana', 'apellido': 'López', 'correo': 'ana@example.com'}],
		]
		self.client.force_authenticate(user=self.admin)

		response = self.client.get(reverse('mobile_valvula_logs', kwargs={'id_valvula': 9}))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {
			'advertencia': None,
			'logs': [{
				'accion': 'ABRIR',
				'detalle': '30',
				'fecha_hora': '2026-10-02T12:00:00Z',
				'usuario': 'Ana López',
				'origen': 'App (Temporizado)',
			}],
		})
		self.assertEqual(select.call_args_list[1].args[2]['id_valvula'], 'eq.9')

	@patch('App.api.views_valves.supabase_client.select')
	def test_logs_movil_son_exclusivos_de_admin(self, select):
		self.client.force_authenticate(user=self.user)

		response = self.client.get(reverse('mobile_valvula_logs', kwargs={'id_valvula': 9}))

		self.assertEqual(response.status_code, 403)
		select.assert_not_called()


class QueryCacheAndFiltersTests(TestCase):
	def setUp(self):
		self.user_role = Rol.objects.create(id_rol=3, nombre_rol='user')
		self.admin_role = Rol.objects.create(id_rol=2, nombre_rol='admin')
		self.user = Usuario.objects.create_user(
			email='cache@example.com', nombre='Cache', apellido='Usuario',
			password='password-segura', rol=self.user_role,
		)

	@patch('App.queries.supabase_client.select')
	def test_viviendas_se_consultan_una_sola_vez_y_se_invalidan(self, select):
		from . import queries
		select.return_value = [{'id_vivienda': 17}]

		queries.user_viviendas(self.user)
		queries.user_viviendas(self.user)
		self.assertEqual(select.call_count, 1)

		queries.invalidate_user_viviendas(self.user)
		queries.user_viviendas(self.user)
		self.assertEqual(select.call_count, 2)

	@patch('App.views.supabase_client.insert')
	@patch('App.views.supabase_client.select')
	def test_alerta_de_consumo_elevado_se_registra_una_sola_vez(self, select, insert):
		select.side_effect = select_by_table(
			vivienda=[{'id_vivienda': 17}],
			consumo=[
				{'fecha': _month_day(0), 'consumo_total': 30},
				{'fecha': _month_day(1), 'consumo_total': 20},
			],
		)
		insert.side_effect = [{'id_alerta': 31}, None]
		self.client.force_login(self.user)

		self.client.get(reverse('retroalimentacion'))
		self.client.get(reverse('retroalimentacion'))

		self.assertEqual(insert.call_count, 2)

	@patch('App.views.supabase_client.select')
	def test_sensor_data_filtra_en_supabase_y_rechaza_ids_no_numericos(self, select):
		admin = Usuario.objects.create_user(
			email='sensor-admin@example.com', nombre='Admin', apellido='Sensor',
			password='password-segura', rol=self.admin_role,
		)
		select.side_effect = select_by_table(
			sensor=[{'id_sensor': 5, 'tipo_sensor': 'presion', 'unidad_medida': 'bar'}],
			lectura=[{'id_sensor': 5, 'fecha_registro': timezone.now().isoformat(), 'valor': 2.5}],
		)
		self.client.force_login(admin)

		response = self.client.get(reverse('sensor_data', kwargs={'sensor_id': '5'}), {'range': '1h'})

		self.assertEqual(response.json()['data'], [2.5])
		lectura_call = next(call for call in select.call_args_list if call.args[0] == 'lectura')
		self.assertEqual(lectura_call.args[2]['id_sensor'], 'in.(5)')
		self.assertTrue(lectura_call.args[2]['fecha_registro'].startswith('gte.'))

		select.reset_mock()
		response = self.client.get(reverse('sensor_data', kwargs={'sensor_id': '5,id_sensor.eq.9'}))

		self.assertEqual(response.json()['data'], [])
		self.assertNotIn('lectura', [call.args[0] for call in select.call_args_list])


class UsuarioProfileViewTests(TestCase):
	def setUp(self):
		self.role = Rol.objects.create(nombre_rol='user')
		self.user = Usuario.objects.create_user(
			email='perfil@example.com',
			nombre='Ana',
			apellido='Batres',
			password='password-segura',
			rol=self.role,
		)
		self.user.supabase_id = 88
		self.user.save(update_fields=['supabase_id'])
		self.client.force_login(self.user)

	@patch('App.views.supabase_client.select')
	def test_perfil_muestra_iniciales_direccion_real_y_telefono_no_configurado(self, select):
		select.return_value = [{
			'id_vivienda': 17,
			'nic': 'NIC-17',
			'direccion': 'Calle Principal, San Miguel',
		}]

		response = self.client.get(reverse('usuario'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'AB')
		self.assertContains(response, 'Calle Principal, San Miguel')
		self.assertContains(response, 'No configurado')
		self.assertContains(response, 'Información personal')

	@patch('App.views.supabase_client.update')
	def test_editar_perfil_actualiza_supabase_y_usuario_local(self, update):
		update.return_value = {
			'id_usuario': 88,
			'nombre': 'Andrea',
			'apellido': 'Batres',
			'correo': 'perfil@example.com',
		}

		response = self.client.post(reverse('usuario'), {
			'nombre': 'Andrea',
			'apellido': 'Batres',
			'email': 'perfil@example.com',
		})

		self.assertRedirects(response, reverse('usuario'))
		update.assert_called_once_with(
			'usuario',
			{'nombre': 'Andrea', 'apellido': 'Batres', 'correo': 'perfil@example.com'},
			{'id_usuario': 'eq.88'},
			return_representation=True,
		)
		self.user.refresh_from_db()
		self.assertEqual(self.user.nombre, 'Andrea')