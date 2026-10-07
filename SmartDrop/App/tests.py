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
		# vivienda, consumo y la búsqueda de sensores de flujo para estimar días sin consumo registrado.
		self.assertEqual(select.call_count, 3)
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

class VincularViviendaViewTests(TestCase):
	def setUp(self):
		self.role = Rol.objects.create(nombre_rol='user')
		self.user = Usuario.objects.create_user(
			email='vincular@example.com',
			nombre='Luis',
			apellido='Mena',
			password='password-segura',
			rol=self.role,
		)
		self.user.supabase_id = 55
		self.user.save(update_fields=['supabase_id'])
		self.client.force_login(self.user)
		self.vivienda = {
			'id_vivienda': 9,
			'nic': 'NIC-9',
			'nombre_completo_titular': 'Luis Mena',
			'id_usuario_propietario': None,
		}

	def _post(self, account='NIC-9', holder='luis mena'):
		return self.client.post(reverse('vincular_vivienda'), {
			'numero_cuenta': account,
			'nombre_completo_titular': holder,
		})

	@patch('App.queries.supabase_client.update')
	@patch('App.queries.supabase_client.select')
	def test_vincula_vivienda_con_supabase_id_del_usuario(self, select, update):
		select.return_value = [self.vivienda]

		response = self._post()

		self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
		update.assert_called_once_with(
			'vivienda',
			{'id_usuario_propietario': 55},
			{'id_vivienda': 'eq.9'},
		)

	@patch('App.queries.supabase_client.update')
	@patch('App.queries.supabase_client.select')
	def test_rechaza_cuenta_inexistente_titular_distinto_o_vivienda_ocupada(self, select, update):
		select.return_value = []
		self.assertContains(self._post(), 'No encontramos ese número de cuenta.')

		select.return_value = [self.vivienda]
		self.assertContains(self._post(holder='Otra Persona'), 'El nombre no coincide con el titular.')

		select.return_value = [{**self.vivienda, 'id_usuario_propietario': 77}]
		self.assertContains(self._post(), 'Esta vivienda ya está vinculada.')
		update.assert_not_called()

	def test_login_sin_vivienda_redirige_a_vincular(self):
		with patch('App.views.user_viviendas', return_value=[]):
			self.assertRedirects(
				self.client.get(reverse('login')), reverse('vincular_vivienda'), fetch_redirect_response=False
			)
		with patch('App.views.user_viviendas', return_value=[{'id_vivienda': 9}]):
			self.assertRedirects(
				self.client.get(reverse('login')), reverse('dashboard'), fetch_redirect_response=False
			)

class DashboardAveragedReadingsTests(TestCase):
	def test_promedia_las_lecturas_recientes_del_mismo_sensor(self):
		from .views import _averaged_readings_by_type

		lecturas = [
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'valor': 0},
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'valor': 0.3},
			{'id_sensor': 2, 'tipo_sensor': 'tds', 'valor': 90},
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'valor': 0.3},
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'valor': 0.6},
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'valor': 9},
		]

		result = _averaged_readings_by_type(lecturas, window=4)

		self.assertEqual(result['presion']['valor'], 0.3)
		self.assertEqual(result['presion']['recientes'], [0, 0.3, 0.3, 0.6])
		self.assertEqual(result['tds']['valor'], 90)

class AlertasExportTests(TestCase):
	def setUp(self):
		user_role = Rol.objects.create(nombre_rol='user')
		admin_role = Rol.objects.create(nombre_rol='admin')
		self.user = Usuario.objects.create_user(
			email='u@example.com', nombre='U', apellido='U', password='password-segura', rol=user_role,
		)
		self.admin = Usuario.objects.create_user(
			email='a@example.com', nombre='A', apellido='A', password='password-segura', rol=admin_role,
		)

	@patch('App.views_reportes.supabase_client.select', return_value=[])
	def test_exportar_csv_solo_para_admin(self, select):
		self.client.force_login(self.user)
		self.assertNotContains(self.client.get(reverse('alertas_historial')), 'Exportar CSV')
		response = self.client.get(reverse('alertas_export'))
		self.assertRedirects(response, reverse('alertas_historial'), fetch_redirect_response=False)

		self.client.force_login(self.admin)
		self.assertContains(self.client.get(reverse('alertas_historial')), 'Exportar CSV')
		self.assertEqual(self.client.get(reverse('alertas_export'))['Content-Type'], 'text/csv; charset=utf-8')

class FlowConsumptionTests(TestCase):
	def setUp(self):
		from . import queries
		self.queries = queries
		self.today = timezone.localdate()
		self.viviendas = [{'id_vivienda': 1}]

	def _row(self, seconds, value):
		start = timezone.make_aware(timezone.datetime.combine(self.today, timezone.datetime.min.time()))
		return {'id_vivienda': 1, 'id_sensor': 4, 'valor': value, 'fecha_registro': (start + timedelta(seconds=seconds)).isoformat()}

	def test_integra_litros_por_minuto_y_limita_los_huecos(self):
		rows = [self._row(300, 0), self._row(120, 6), self._row(60, 6), self._row(0, 6)]  # más reciente primero

		liters = self.queries._liters_by_day(rows)

		# 6 L/min durante 60 s + 60 s + 60 s (el hueco de 180 s hasta la última lectura se limita a 60 s).
		self.assertEqual(liters, {self.today: 18.0})

	def test_calcula_por_dia_y_guarda_en_cache_los_dias_pasados(self):
		yesterday = self.today - timedelta(days=1)
		calls = []

		def fake_day(in_filter, sensor_ids, day):
			calls.append(day)
			return 10.0, False

		with patch.object(self.queries, '_flow_sensor_ids', return_value=['4']), \
				patch.object(self.queries, '_flow_liters_for_day', side_effect=fake_day):
			first = self.queries.flow_consumption_by_day(self.viviendas, yesterday, self.today)
			second = self.queries.flow_consumption_by_day(self.viviendas, yesterday, self.today)

		self.assertEqual(first, {self.today: 10.0, yesterday: 10.0})
		self.assertEqual(second, first)
		# Los días se calculan en paralelo (sin orden fijo); en la segunda llamada salen de la caché.
		self.assertEqual(sorted(calls), [yesterday, self.today])

	def test_el_presupuesto_de_tiempo_corta_los_dias_pasados_pero_no_hoy(self):
		import threading

		since = self.today - timedelta(days=3)
		release = threading.Event()

		def fake_day(in_filter, sensor_ids, day):
			if day != self.today:
				release.wait(5)  # Día pasado lento: no alcanza el presupuesto.
			return 5.0, False

		with patch.object(self.queries, '_flow_sensor_ids', return_value=['4']), \
				patch.object(self.queries, '_flow_liters_for_day', side_effect=fake_day), \
				patch.object(self.queries, 'FLOW_TIME_BUDGET_SECONDS', 0.05):
			result = self.queries.flow_consumption_by_day(self.viviendas, since, self.today)
			self.assertEqual(result, {self.today: 5.0})

			# Los días pendientes terminan en segundo plano y quedan en caché para la siguiente carga.
			release.set()
			self.queries._flow_executor.submit(lambda: None).result()
			for _ in range(50):
				later = self.queries.flow_consumption_by_day(self.viviendas, since, self.today)
				if len(later) == 4:
					break
				release.wait(0.05)
		self.assertEqual(len(later), 4)

	def test_no_calcula_el_flujo_de_los_dias_con_consumo_registrado(self):
		recorded_day = self.today - timedelta(days=1)
		rows = [{'id_vivienda': 1, 'fecha': f'{recorded_day.isoformat()}T10:00:00Z', 'consumo_total': 8}]
		calls = []

		def fake_day(in_filter, sensor_ids, day):
			calls.append(day)
			return 3.0, False

		with patch.object(self.queries, '_flow_sensor_ids', return_value=['4']), \
				patch.object(self.queries, '_flow_liters_for_day', side_effect=fake_day):
			self.queries.with_flow_consumption(self.viviendas, rows, recorded_day)

		self.assertEqual(calls, [self.today])
	def test_usa_flujo_solo_en_los_dias_sin_consumo_registrado(self):
		recorded_day = self.today - timedelta(days=1)
		rows = [{'id_vivienda': 1, 'fecha': f'{recorded_day.isoformat()}T10:00:00Z', 'consumo_total': 8}]
		flow = {self.today: 25.5, recorded_day: 99}

		with patch.object(self.queries, 'flow_consumption_by_day', return_value=flow):
			merged = self.queries.with_flow_consumption(self.viviendas, rows, recorded_day)

		self.assertEqual(self.queries.consumption_on(merged, self.today), 25.5)
		self.assertEqual(self.queries.consumption_on(merged, recorded_day), 8)

	def test_si_falla_el_flujo_devuelve_las_filas_originales(self):
		rows = [{'fecha': f'{self.today.isoformat()}T10:00:00Z', 'consumo_total': 3}]

		with patch.object(self.queries, 'flow_consumption_by_day', side_effect=RuntimeError('sin red')):
			self.assertEqual(self.queries.with_flow_consumption(self.viviendas, rows, self.today), rows)

class PreferenciasTests(TestCase):
	"""Preferencias del perfil: compartidas entre la web y la app, y aplicadas a las alertas."""

	def setUp(self):
		from .models import PreferenciasUsuario
		self.Prefs = PreferenciasUsuario
		user_role = Rol.objects.create(id_rol=3, nombre_rol='user')
		self.user = Usuario.objects.create_user(
			email='prefs@example.com', nombre='Ana', apellido='Prefs', password='password-segura', rol=user_role,
		)
		self.user.supabase_id = 77
		self.user.save()

	def test_valores_por_defecto(self):
		from . import preferencias
		prefs = preferencias.preferencias_de(self.user)
		self.assertEqual(preferencias.como_dict(prefs), {
			'alertas_nivel': True, 'suministro': True, 'calidad': True, 'consumo_elevado': True,
			'modo_oscuro': False, 'reportes_semanales': False, 'idioma': 'es',
		})

	def test_la_web_guarda_y_valida_preferencias(self):
		self.client.force_login(self.user)
		url = reverse('usuario_preferencias')
		response = self.client.post(url, data='{"alertas_nivel": false, "idioma": "en", "modo_oscuro": true}',
									content_type='application/json')
		self.assertEqual(response.status_code, 200)
		prefs = self.Prefs.objects.get(id_usuario=77)
		self.assertEqual((prefs.alertas_nivel, prefs.idioma, prefs.modo_oscuro), (False, 'en', True))

		bad = self.client.post(url, data='{"idioma": "fr"}', content_type='application/json')
		self.assertEqual(bad.status_code, 400)
		self.assertEqual(self.Prefs.objects.get(id_usuario=77).idioma, 'en')

	def test_el_tema_del_servidor_se_aplica_en_todas_las_paginas(self):
		self.Prefs.objects.create(id_usuario=77, modo_oscuro=True)
		self.client.force_login(self.user)
		with patch('App.views.supabase_client.select', return_value=[]):
			html = self.client.get(reverse('usuario')).content.decode()
		self.assertIn("const theme = 'dark';", html)

	def test_filtra_alertas_por_categoria_y_oculta_reportes_ajenos(self):
		from . import preferencias
		from .views_reportes import _alertas_visibles
		self.Prefs.objects.create(id_usuario=77, alertas_nivel=False)
		alertas = [
			{'tipo_alerta': 'nivel_tanque_bajo', 'datos_adicionales': {}},
			{'tipo_alerta': 'presion_baja', 'datos_adicionales': {}},
			{'tipo_alerta': 'fuga', 'datos_adicionales': {'origen': 'ml_engine'}},
			{'tipo_alerta': 'reporte_semanal', 'datos_adicionales': {'origen': 'reporte_semanal', 'id_usuario': 77}},
			{'tipo_alerta': 'reporte_semanal', 'datos_adicionales': {'origen': 'reporte_semanal', 'id_usuario': 5}},
		]
		visibles = _alertas_visibles(alertas, False, self.user)
		self.assertEqual([a['tipo_alerta'] for a in visibles], ['presion_baja', 'reporte_semanal'])
		self.assertEqual(preferencias.categoria_alerta('calidad_agua'), 'calidad')

	@patch('App.preferencias.supabase_client.insert')
	@patch('App.preferencias.owned_consumption')
	def test_reporte_semanal_una_vez_por_semana(self, consumption, insert):
		from . import preferencias
		today = timezone.localdate()
		consumption.return_value = ([], [
			{'fecha': f'{today.isoformat()}T10:00:00Z', 'consumo_total': 30},
			{'fecha': f'{(today - timedelta(days=9)).isoformat()}T10:00:00Z', 'consumo_total': 20},
		])
		insert.side_effect = [{'id_alerta': 9}, {}]
		prefs = self.Prefs.objects.create(id_usuario=77, reportes_semanales=True)

		first = preferencias.reporte_semanal(self.user, prefs)
		second = preferencias.reporte_semanal(self.user, prefs)

		self.assertEqual(first['total_litros'], 30.0)
		self.assertEqual(first['variacion_porcentual'], 50.0)
		self.assertEqual(second, first)
		self.assertEqual(insert.call_count, 2)  # alerta + notificación, solo la primera vez
		self.assertEqual(insert.call_args_list[0].args[1]['datos_adicionales']['id_usuario'], 77)

	def test_consumo_elevado_desactivado_no_registra_alerta(self):
		self.Prefs.objects.create(id_usuario=77, consumo_elevado=False)
		self.client.force_login(self.user)
		rows = [
			{'id_vivienda': 1, 'fecha': _month_day(0), 'consumo_total': 400},
			{'id_vivienda': 1, 'fecha': _month_day(1), 'consumo_total': 100},
		]
		with patch('App.views.owned_consumption', return_value=([{'id_vivienda': 1}], rows)), \
				patch('App.views.supabase_client.insert') as insert:
			response = self.client.get(reverse('retroalimentacion'))
		self.assertEqual(response.status_code, 200)
		insert.assert_not_called()

	@patch('App.views.supabase_client.select')
	def test_presion_muestra_el_estado_de_la_valvula(self, select):
		select.side_effect = select_by_table(valvula=[{'id_valvula': 1, 'nombre': 'Principal', 'estado_actual': 'abierta',
													   'ultima_apertura': None}])
		self.client.force_login(self.user)
		html = self.client.get(reverse('presion')).content.decode()
		self.assertIn('Abierta', html)
		self.assertIn('badge-open', html)


class MobilePerfilTests(TestCase):
	def setUp(self):
		from .api.authentication import MobileUser
		self.user = MobileUser({'id_usuario': 77, 'correo': 'ana@example.com', 'id_rol': 3, 'nombre_rol': 'user'})
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_preferencias_moviles_son_las_mismas_de_la_web(self):
		from .models import PreferenciasUsuario
		response = self.client.patch(reverse('mobile_preferencias'), {'calidad': False, 'idioma': 'en'}, format='json')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['preferencias']['calidad'], False)
		prefs = PreferenciasUsuario.objects.get(id_usuario=77)
		self.assertEqual((prefs.calidad, prefs.idioma), (False, 'en'))

	@patch('App.api.views_mobile_profile.supabase_client.select')
	def test_perfil_movil_trae_datos_y_preferencias(self, select):
		select.side_effect = select_by_table(
			usuario=[{'id_usuario': 77, 'nombre': 'Ana', 'apellido': 'López', 'correo': 'ana@example.com',
					  'fecha_registro': '2026-08-31T03:00:00+00:00', 'id_rol': 3}],
			vivienda=[{'id_vivienda': 4, 'direccion': 'Calle 1', 'telefono_titular': '7000-0004'}],
		)
		with patch('App.queries.supabase_client.select', side_effect=select.side_effect):
			data = self.client.get(reverse('mobile_perfil')).json()
		self.assertEqual((data['nombre_completo'], data['iniciales'], data['telefono']), ('Ana López', 'AL', '7000-0004'))
		self.assertEqual(data['miembro_desde'], 'agosto 2026')
		self.assertIn('preferencias', data)

	@patch('App.api.views_mobile_profile.supabase_client')
	def test_editar_perfil_movil_rechaza_correo_ajeno(self, client):
		client.SupabaseError = supabase_client.SupabaseError
		client.get_user_by_email.return_value = {'id_usuario': 5}
		response = self.client.patch(reverse('mobile_perfil'), {'nombre': 'Ana', 'apellido': 'L', 'correo': 'otro@example.com'},
									 format='json')
		self.assertEqual(response.status_code, 409)
		client.update.assert_not_called()

	def test_avisos_respetan_las_categorias(self):
		from .models import PreferenciasUsuario
		PreferenciasUsuario.objects.create(id_usuario=77, alertas_nivel=False)
		now = timezone.now().isoformat()
		sensores = [
			{'id_sensor': 1, 'tipo_sensor': 'presion', 'unidad_medida': 'kPa', 'rango_min': 1, 'rango_max': 5},
			{'id_sensor': 2, 'tipo_sensor': 'nivel', 'unidad_medida': 'cm', 'rango_min': 10, 'rango_max': 30},
		]
		lecturas = [
			{'id_sensor': 1, 'id_vivienda': 4, 'valor': 0.2, 'fecha_registro': now},
			{'id_sensor': 2, 'id_vivienda': 4, 'valor': 2, 'fecha_registro': now},
		]
		from .queries import OwnedData
		with patch('App.api.views_mobile_data.queries.owned_data', return_value=OwnedData([{'id_vivienda': 4}], sensores, lecturas, [], [])), \
				patch('App.api.views_mobile_profile.supabase_client.select', return_value=[]):
			avisos = self.client.get(reverse('mobile_notificaciones')).json()['avisos']
		self.assertEqual([(a['clave'], a['estado']) for a in avisos], [('sensor:presion', 'Baja')])


class CatalogoTraduccionTests(TestCase):
	"""Catálogo español→inglés compartido por la web y la app."""

	def test_catalogo_valido(self):
		from .i18n import catalogo
		from .i18n.validacion import errores_catalogo
		data = catalogo('en')
		self.assertGreater(len(data['frases']), 500)
		self.assertEqual(errores_catalogo(data), [])

	def test_la_validacion_detecta_huecos_y_cadenas(self):
		from .i18n.validacion import errores_catalogo
		errores = errores_catalogo({
			'frases': {'Alta': 'High', 'High': 'Alta'},
			'plantillas': {'Hay {0} alertas': 'There are alerts'},
		})
		self.assertEqual(len(errores), 3)

	def test_catalogo_publico_para_la_app_y_la_web(self):
		data = self.client.get(reverse('i18n_json', args=['en'])).json()
		self.assertEqual(data['frases']['Modo oscuro'], 'Dark mode')
		script = self.client.get(reverse('i18n_js', args=['en']))
		self.assertTrue(script.content.decode().startswith('window.SD_I18N = '))
		self.assertEqual(self.client.get(reverse('i18n_json', args=['fr'])).status_code, 404)

	def test_la_web_carga_el_traductor_solo_en_ingles(self):
		login = reverse('login')
		self.assertNotIn('i18n.js', self.client.get(login).content.decode())
		self.client.cookies['sd_idioma'] = 'en'
		html = self.client.get(login).content.decode()
		self.assertIn('i18n.js', html)
		self.assertIn('<html lang="en">', html)

	def test_busqueda_en_ingles(self):
		from .search_sections import matching_sections
		resultados = matching_sections('pressure', False, 'en')
		self.assertIn('Water Pressure', [r['titulo'] for r in resultados])
		self.assertTrue(matching_sections('presión', False, 'en'))
