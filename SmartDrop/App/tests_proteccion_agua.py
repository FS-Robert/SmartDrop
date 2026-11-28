"""Pruebas de la protección de válvulas: nivel bajo del tanque (PB052) y calidad del agua.

No usan el ESP32 ni Supabase de verdad: las lecturas se simulan y Supabase se reemplaza por datos de prueba.
"""
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from . import proteccion_agua
from .models import BloqueoValvula, Rol, Usuario

VALVULA_ABIERTA = {'id_valvula': 1, 'nombre': 'Principal', 'estado_actual': 'abierta',
                   'topic_mqtt_comando': 'smartdrop/1/valvula/comando', 'ultima_apertura': None}
VALVULA_CERRADA = dict(VALVULA_ABIERTA, estado_actual='cerrada')


class SupabaseFalso:
	"""Guarda lo que se le pide a Supabase para poder revisarlo en cada prueba."""

	def __init__(self, valvulas):
		self.valvulas = valvulas
		self.insertados = []
		self.actualizados = []

	def select(self, tabla, columnas='*', filtros=None):
		if tabla == 'valvula':
			return self.valvulas
		if tabla == 'usuario':
			usuarios = [{'id_usuario': 4, 'id_rol': 2}, {'id_usuario': 9, 'id_rol': 1}]
			if (filtros or {}).get('id_rol') == 'eq.2':
				return usuarios[:1]
			return usuarios
		return []

	def insert(self, tabla, datos, return_representation=True):
		self.insertados.append((tabla, datos))
		return {'id_alerta': len(self.insertados)} if tabla == 'alerta' else {}

	def update(self, tabla, datos, filtros):
		self.actualizados.append((tabla, datos))
		return []

	def de_tabla(self, tabla):
		return [datos for nombre, datos in self.insertados if nombre == tabla]


class ProteccionBase(TestCase):
	valvulas = [VALVULA_ABIERTA]

	def setUp(self):
		proteccion_agua._seguidas.clear()
		self.supabase = SupabaseFalso(list(self.valvulas))
		self.publicados = []
		for nombre in ('select', 'insert', 'update'):
			parche = patch(f'App.supabase_client.{nombre}', side_effect=getattr(self.supabase, nombre))
			parche.start()
			self.addCleanup(parche.stop)
		parche = patch('App.mqtt_service.publish_command', side_effect=lambda topico, cmd: self.publicados.append((topico, cmd)))
		parche.start()
		self.addCleanup(parche.stop)

	def lecturas_nivel(self, *porcentajes):
		for porcentaje in porcentajes:
			proteccion_agua.revisar_nivel(porcentaje)

	def lecturas_calidad(self, calidad, veces, ppm=1190):
		for _ in range(veces):
			proteccion_agua.revisar_calidad(calidad, ppm)

	def bloqueado(self, motivo):
		return BloqueoValvula.objects.filter(motivo=motivo, activo=True).exists()


class NivelDelTanqueTests(ProteccionBase):
	def test_bajo_el_10_avisa_una_sola_vez_y_no_cierra(self):
		self.lecturas_nivel(9, 9, 9)
		avisos = [a for a in self.supabase.de_tabla('alerta') if a['datos_adicionales']['titulo'] == 'Nivel bajo del tanque']
		self.assertEqual(len(avisos), 1)
		self.assertEqual(self.publicados, [])
		self.assertFalse(self.bloqueado('nivel'))

	def test_una_lectura_falsa_de_8_no_cierra(self):
		self.lecturas_nivel(8, 50, 7, 50)
		self.assertEqual(self.publicados, [])
		self.assertFalse(self.bloqueado('nivel'))

	def test_tres_lecturas_en_8_o_menos_cierran_y_bloquean(self):
		self.lecturas_nivel(8, 7, 6)
		self.assertTrue(self.bloqueado('nivel'))
		self.assertEqual(self.publicados, [('smartdrop/1/valvula/comando', 'cerrar')])
		log = self.supabase.de_tabla('log_valvula')[0]
		self.assertEqual(log['tipo_activacion'], 'automatico')
		self.assertIn('Nivel bajo del tanque (6 %)', log['razon'])
		titulos = [a['datos_adicionales']['titulo'] for a in self.supabase.de_tabla('alerta')]
		self.assertIn('Suministro suspendido', titulos)
		# El aviso de suspensión llega a todos los usuarios, no solo a los administradores.
		self.assertEqual(len(self.supabase.de_tabla('notificacion')), 1 + 2)

	def test_entre_10_y_30_sigue_bloqueada(self):
		self.lecturas_nivel(5, 5, 5, 20, 25, 30)
		self.assertTrue(self.bloqueado('nivel'))

	def test_sobre_30_se_habilita_pero_no_abre_sola(self):
		self.lecturas_nivel(5, 5, 5)
		self.publicados.clear()
		self.lecturas_nivel(31, 40, 45)
		self.assertFalse(self.bloqueado('nivel'))
		self.assertEqual(self.publicados, [])
		titulos = [a['datos_adicionales']['titulo'] for a in self.supabase.de_tabla('alerta')]
		self.assertIn('Válvula disponible para operar', titulos)


class NivelConValvulaCerradaTests(ProteccionBase):
	valvulas = [VALVULA_CERRADA]

	def test_si_ya_esta_cerrada_no_manda_otro_cierre(self):
		self.lecturas_nivel(5, 5, 5, 5)
		self.assertTrue(self.bloqueado('nivel'))
		self.assertEqual(self.publicados, [])
		self.assertEqual(self.supabase.de_tabla('log_valvula'), [])


class CalidadDelAguaTests(ProteccionBase):
	def test_agua_pobre_cierra_y_bloquea_aunque_el_tanque_este_lleno(self):
		self.lecturas_nivel(100)
		self.lecturas_calidad('Pobre', 3)
		self.assertTrue(self.bloqueado('calidad'))
		self.assertEqual(self.publicados, [('smartdrop/1/valvula/comando', 'cerrar')])
		alerta = [a for a in self.supabase.de_tabla('alerta') if a['tipo_alerta'] == 'calidad_agua'][0]
		self.assertIn('filtren el agua', alerta['mensaje'])
		# Solo se avisa al administrador.
		self.assertEqual(len(self.supabase.de_tabla('notificacion')), 1)

	def test_agua_inaceptable_tambien_bloquea(self):
		self.lecturas_calidad('Inaceptable', 3)
		self.assertTrue(self.bloqueado('calidad'))

	def test_una_lectura_mala_suelta_no_bloquea(self):
		self.lecturas_calidad('Pobre', 2)
		self.lecturas_calidad('Buena', 1, ppm=200)
		self.lecturas_calidad('Pobre', 1)
		self.assertFalse(self.bloqueado('calidad'))

	def test_agua_apta_quita_el_bloqueo(self):
		self.lecturas_calidad('Pobre', 3)
		self.lecturas_calidad('Buena', 3, ppm=200)
		self.assertFalse(self.bloqueado('calidad'))

	def test_nivel_recuperado_con_agua_mala_sigue_bloqueada(self):
		self.lecturas_nivel(5, 5, 5)
		self.lecturas_calidad('Pobre', 3)
		self.lecturas_nivel(80, 80, 80)
		self.assertFalse(self.bloqueado('nivel'))
		self.assertTrue(self.bloqueado('calidad'))
		self.assertIn('Calidad del agua pobre', proteccion_agua.mensaje_bloqueo())

	def test_la_ingesta_mqtt_pasa_las_lecturas_tds(self):
		from .ingesta_mqtt import procesar_mensaje
		with patch('App.ingesta_mqtt._guardar_lectura'), patch('App.ingesta_mqtt._incidente_calidad'):
			for _ in range(3):
				procesar_mensaje('smartdrop/2/tds', json.dumps({'id_sensor': 5, 'valor': 1193, 'calidad': 'Pobre'}))
		self.assertTrue(self.bloqueado('calidad'))


class AperturaBloqueadaTests(ProteccionBase):
	valvulas = [VALVULA_CERRADA]

	def setUp(self):
		super().setUp()
		rol = Rol.objects.create(id_rol=2, nombre_rol='admin')
		self.admin = Usuario.objects.create_user(
			email='admin@example.com', nombre='Admin', apellido='Prueba', password='password-segura', rol=rol,
		)
		self.admin.supabase_id = 4
		self.admin.save(update_fields=['supabase_id'])
		BloqueoValvula.objects.create(motivo='nivel', activo=True, valor='6 %')

	def test_la_web_no_deja_abrir(self):
		self.client.force_login(self.admin)
		with patch('App.views.publish_command') as publicar:
			respuesta = self.client.post(reverse('valvula_comando', kwargs={'valvula_id': 1}), {'comando': 'abrir'},
										 HTTP_X_REQUESTED_WITH='XMLHttpRequest')
		self.assertEqual(respuesta.status_code, 409)
		self.assertIn('Bloqueado - Nivel insuficiente', respuesta.json()['error'])
		publicar.assert_not_called()

	def test_la_web_si_deja_cerrar(self):
		self.client.force_login(self.admin)
		with patch('App.views.publish_command') as publicar:
			respuesta = self.client.post(reverse('valvula_comando', kwargs={'valvula_id': 1}), {'comando': 'cerrar'},
										 HTTP_X_REQUESTED_WITH='XMLHttpRequest')
		self.assertEqual(respuesta.status_code, 200)
		publicar.assert_called_once()

	def test_la_app_no_deja_abrir_ni_con_temporizador(self):
		from .api.authentication import MobileUser
		cliente = APIClient()
		cliente.force_authenticate(MobileUser({'id_usuario': 4, 'id_rol': 2}))
		with patch('App.api.views_valves.publish_command') as publicar:
			respuesta = cliente.post('/api/valvula/1/abrir/', {'duracion': 30}, format='json')
		self.assertEqual(respuesta.status_code, 409)
		self.assertTrue(respuesta.json()['bloqueada'])
		publicar.assert_not_called()

	def test_la_pagina_muestra_el_bloqueo_y_apaga_el_boton(self):
		self.client.force_login(self.admin)
		respuesta = self.client.get(reverse('valvulas'))
		self.assertContains(respuesta, 'Bloqueado - Nivel insuficiente')
		self.assertContains(respuesta, 'Se habilitará cuando el nivel del tanque pase del 30 %.')
		self.assertContains(respuesta, 'data-valve-action="abrir" disabled')
