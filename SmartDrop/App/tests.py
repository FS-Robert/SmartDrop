from django.test import TestCase
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from .backends import sync_user_from_supabase
from .models import Rol, Usuario
from .views import _supabase_user_id, _valve_statistics

# Create your tests here.


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
				{'fecha': '2026-09-05T10:00:00Z', 'consumo_total': 12},
				{'fecha': '2026-08-05T10:00:00Z', 'consumo_total': 20},
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
				{'fecha': '2026-09-05T10:00:00Z', 'consumo_total': 30},
				{'fecha': '2026-08-05T10:00:00Z', 'consumo_total': 20},
			],
		]
		insert.side_effect = [{'id_alerta': 31}, None]
		self.client.force_login(user)

		response = self.client.get(reverse('retroalimentacion'))

		self.assertEqual(response.context['retro']['alerta'], 'Consumo elevado de agua')
		self.assertEqual(insert.call_args_list[0].args[0], 'alerta')
		self.assertEqual(insert.call_args_list[1].args[0], 'notificacion')
		self.assertEqual(insert.call_args_list[1].args[1]['id_usario_destino'], 77)


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
		select.side_effect = [valves, movimientos, usuarios, [valves[0]]]
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
		select.side_effect = [
			[{'id_valvula': 1, 'nombre': 'Principal'}],
			[{
				'id_valvula': 1, 'accion': 'abrir', 'tipo_activacion': 'manual',
				'id_usuario': None, 'fecha_hora': '2026-09-07T10:00:00+00:00',
				'origen_accion': 'web',
			}],
			[],
		]
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
		select.side_effect = [[{'id_valvula': 1, 'nombre': 'Principal'}], movements, [
			{'id_usuario': 4, 'nombre': 'Admin', 'apellido': 'Prueba'},
		]]
		insert.side_effect = [{'id_alerta': 9}, None]
		self.client.force_login(self.admin)

		response = self.client.get(reverse('valvulas'))

		self.assertEqual(response.status_code, 200)
		self.assertIn('21 cambios', response.context['alerta_actividad'])
		self.assertEqual(insert.call_count, 2)
		self.assertEqual(insert.call_args_list[1].args[0], 'notificacion')
