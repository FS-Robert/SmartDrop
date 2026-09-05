from django.test import TestCase
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from .backends import sync_user_from_supabase
from .models import Rol, Usuario
from .views import _supabase_user_id

# Create your tests here.


class ConsumoViewTests(TestCase):
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
