"""Pruebas de lo que se completó en el Sprint 4: fechas al revés y filtros por fecha (PB038),
cambiar el estado de una alerta (PB041) y el registro manual de consumo y nivel (PB054)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache, caches
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse

from . import consumo_admin, views_registro_manual
from .models import RegistroManual, Rol, Usuario


# Sin revisar la cuenta en Supabase en cada prueba (eso tiene sus propias pruebas).
@override_settings(REVALIDAR_SESION_SEGUNDOS=0)
class _ConUsuarios(TestCase):
    databases = {'default', 'timeseries'}

    def setUp(self):
        cache.clear()
        caches['limites'].clear()
        self.admin = Usuario.objects.create_user(
            email='admin4@example.com', nombre='Ada', apellido='Min', password='clave-segura-123',
            rol=Rol.objects.create(id_rol=2, nombre_rol='admin'), supabase_id=99,
        )
        self.usuario = Usuario.objects.create_user(
            email='usuario4@example.com', nombre='Uno', apellido='Común', password='clave-segura-123',
            rol=Rol.objects.create(id_rol=1, nombre_rol='user'), supabase_id=11,
        )


class RangoDeFechasTests(TestCase):
    def test_detecta_fechas_al_reves(self):
        self.assertTrue(consumo_admin.rango_invertido(QueryDict('desde=2026-10-15&hasta=2026-10-05')))
        self.assertFalse(consumo_admin.rango_invertido(QueryDict('desde=2026-10-05&hasta=2026-10-15')))
        self.assertFalse(consumo_admin.rango_invertido(QueryDict('desde=2026-10-05&hasta=2026-10-05')))
        self.assertFalse(consumo_admin.rango_invertido(QueryDict('desde=basura&hasta=2026-10-05')))
        self.assertFalse(consumo_admin.rango_invertido(QueryDict('mes=2026-10')))

    def test_leer_rango_y_filtro(self):
        desde, hasta, error = consumo_admin.leer_rango(QueryDict('desde=2026-10-05&hasta=2026-10-15'))
        self.assertEqual((desde, hasta, error), (date(2026, 10, 5), date(2026, 10, 15), None))
        self.assertEqual(consumo_admin.leer_rango(QueryDict('desde=2026-10-15&hasta=2026-10-05'))[2],
                         consumo_admin.MENSAJE_RANGO_INVERTIDO)
        self.assertEqual(consumo_admin.leer_rango(QueryDict('desde=2026-10-05'))[:2], (date(2026, 10, 5), None))
        filtro = consumo_admin.filtro_rango('fecha_creacion', date(2026, 10, 5), date(2026, 10, 15))
        self.assertTrue(filtro.startswith('(fecha_creacion.gte.2026-10-05T00:00:00'))
        self.assertIn('fecha_creacion.lt.2026-10-16T00:00:00', filtro)
        self.assertIsNone(consumo_admin.filtro_rango('fecha_creacion', None, None))


class FechasAlRevesEnPantallasTests(_ConUsuarios):
    AL_REVES = '?desde=2026-10-15&hasta=2026-10-05'

    def test_consumo_no_arma_el_reporte_y_avisa(self):
        self.client.force_login(self.admin)
        with patch('App.views_consumo_admin.consumo_admin.reporte') as reporte:
            respuesta = self.client.get('/admin-consumo/' + self.AL_REVES)
        reporte.assert_not_called()
        self.assertContains(respuesta, 'La fecha de inicio no puede ser posterior a la fecha de fin')
        self.assertContains(respuesta, 'notice--bad')

    def test_descarga_de_consumo_con_fechas_al_reves_regresa_con_error(self):
        self.client.force_login(self.admin)
        with patch('App.views_consumo_admin.consumo_admin.reporte') as reporte:
            respuesta = self.client.get('/admin-consumo/exportar/' + self.AL_REVES)
        reporte.assert_not_called()
        self.assertEqual(respuesta.status_code, 302)

    @patch('App.views_reportes.supabase_client.select', return_value=[])
    def test_reportes_y_alertas_no_consultan_con_fechas_al_reves(self, select):
        self.client.force_login(self.admin)
        for ruta in (reverse('admin_reportes'), reverse('alertas_historial')):
            respuesta = self.client.get(ruta + self.AL_REVES)
            self.assertContains(respuesta, 'La fecha de inicio no puede ser posterior a la fecha de fin')
        consultas = [llamada.args[0] for llamada in select.call_args_list]
        self.assertNotIn('reporte', consultas)
        self.assertNotIn('alerta', consultas)

    @patch('App.views_reportes.supabase_client.select', return_value=[])
    def test_filtro_por_fechas_en_alertas_y_reportes(self, select):
        self.client.force_login(self.admin)
        self.client.get(reverse('alertas_historial') + '?desde=2026-10-05&hasta=2026-10-15')
        self.client.get(reverse('admin_reportes') + '?desde=2026-10-05&hasta=2026-10-15')
        filtros = {llamada.args[0]: llamada.args[2] for llamada in select.call_args_list
                   if llamada.args[0] in ('alerta', 'reporte')}
        self.assertIn('fecha_creacion.gte.2026-10-05', filtros['alerta']['and'])
        self.assertIn('fecha_reporte.lt.2026-10-16', filtros['reporte']['and'])

    @patch('App.views_reportes.supabase_client.select', return_value=[])
    def test_exportes_con_fechas_al_reves_no_descargan(self, select):
        self.client.force_login(self.admin)
        for ruta in (reverse('admin_reportes_export'), reverse('alertas_export')):
            self.assertEqual(self.client.get(ruta + self.AL_REVES).status_code, 302)
        select.assert_not_called()


class CambiarEstadoAlertaTests(_ConUsuarios):
    def _url(self, alerta_id=7):
        return reverse('alerta_cambiar_estado', args=[alerta_id])

    @patch('App.views_reportes.supabase_client.update', return_value={'id_alerta': 7, 'estado_confirmacion': 'resuelta'})
    def test_el_admin_cambia_pendiente_a_resuelta(self, update):
        self.client.force_login(self.admin)
        respuesta = self.client.post(self._url(), {'estado': 'resuelta', 'volver': 'estado=pendiente&x=1'})
        update.assert_called_once_with('alerta', {'estado_confirmacion': 'resuelta'}, {'id_alerta': 'eq.7'},
                                       return_representation=True)
        self.assertRedirects(respuesta, reverse('alertas_historial') + '?estado=pendiente',
                             fetch_redirect_response=False)

    @patch('App.views_reportes.supabase_client.update')
    def test_estado_fuera_de_la_lista_no_se_guarda(self, update):
        self.client.force_login(self.admin)
        self.client.post(self._url(), {'estado': 'borrada'})
        update.assert_not_called()

    @patch('App.views_reportes.supabase_client.update')
    def test_un_usuario_comun_no_puede_cambiarlo(self, update):
        self.client.force_login(self.usuario)
        self.client.post(self._url(), {'estado': 'resuelta'})
        update.assert_not_called()

    def test_solo_acepta_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    @patch('App.views_reportes.supabase_client.select', return_value=[
        {'id_alerta': 7, 'tipo_alerta': 'fuga', 'mensaje': 'Posible fuga', 'prioridad': 'alta',
         'estado_confirmacion': 'resuelta', 'fecha_creacion': '2026-11-26T10:00:00+00:00'},
    ])
    def test_el_historial_muestra_el_estado_y_el_selector_solo_al_admin(self, select):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('alertas_historial'))
        self.assertContains(respuesta, 'Resuelta')
        self.assertContains(respuesta, self._url())


class RegistroManualTests(_ConUsuarios):
    def test_valida_campos_obligatorios_y_rangos(self):
        _, errores = views_registro_manual.validar({})
        self.assertEqual(set(errores), {'consumo_litros', 'nivel_pct'})
        _, errores = views_registro_manual.validar({'consumo_litros': '-3', 'nivel_pct': '120'})
        self.assertEqual(set(errores), {'consumo_litros', 'nivel_pct'})
        _, errores = views_registro_manual.validar({'consumo_litros': 'abc', 'nivel_pct': 'nan'})
        self.assertEqual(set(errores), {'consumo_litros', 'nivel_pct'})
        limpios, errores = views_registro_manual.validar({'consumo_litros': '12,5', 'nivel_pct': '80'})
        self.assertEqual(errores, {})
        self.assertEqual((limpios['consumo_litros'], limpios['nivel_pct']), (Decimal('12.50'), Decimal('80.00')))
        _, errores = views_registro_manual.validar({'consumo_litros': '1', 'nivel_pct': '1',
                                                    'fecha_medicion': '2999-01-01T10:00'})
        self.assertIn('fecha_medicion', errores)

    def test_falta_un_campo_muestra_error_y_no_guarda(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse('registro_manual'), {'consumo_litros': '', 'nivel_pct': '50'})
        self.assertContains(respuesta, 'El consumo es obligatorio.')
        self.assertEqual(RegistroManual.objects.count(), 0)

    def test_guarda_y_confirma(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse('registro_manual'),
                                     {'consumo_litros': '35.2', 'nivel_pct': '64', 'ubicacion': 'Tanque principal'},
                                     follow=True)
        registro = RegistroManual.objects.get()
        self.assertEqual((registro.consumo_litros, registro.nivel_pct), (Decimal('35.20'), Decimal('64.00')))
        self.assertEqual(registro.id_usuario, 99)
        self.assertContains(respuesta, 'Registro guardado')
        self.assertContains(respuesta, 'Tanque principal')

    def test_solo_el_admin_entra(self):
        self.client.force_login(self.usuario)
        self.assertRedirects(self.client.get(reverse('registro_manual')), reverse('dashboard'),
                             fetch_redirect_response=False)
        self.client.post(reverse('registro_manual'), {'consumo_litros': '1', 'nivel_pct': '1'})
        self.assertEqual(RegistroManual.objects.count(), 0)


# ── PB042: cortes de agua (duración y zona) ────────────────────────────────

from datetime import timedelta as _td  # noqa: E402

from django.utils import timezone as _tz  # noqa: E402

from . import interrupciones  # noqa: E402
from .models import InterrupcionServicio  # noqa: E402


def _reporte(id_reporte, tipo='sin_agua', estado='pendiente', hace_horas=3, ubicacion='', id_usuario=11, atendido=None):
    inicio = _tz.now() - _td(hours=hace_horas)
    return {
        'id_reporte': id_reporte, 'tipo_problema': tipo, 'estado': estado, 'prioridad': 'media',
        'fecha_reporte': inicio.isoformat(), 'ubicacion': ubicacion, 'id_usuario': id_usuario,
        'descripcion': 'No hay agua desde la mañana', 'fecha_atencion': atendido.isoformat() if atendido else None,
    }


class InterrupcionesCalculoTests(TestCase):
    def test_zona_del_texto_o_de_la_vivienda(self):
        self.assertEqual(interrupciones.zona_de_texto('[zona: Norte] calle 3'), 'Norte')
        self.assertIsNone(interrupciones.zona_de_texto('calle 3'))
        self.assertEqual(interrupciones.zona_del_reporte(_reporte(1, ubicacion='casa'), {11: 'Zona Sur'}), 'Zona Sur')
        self.assertEqual(interrupciones.zona_del_reporte(_reporte(1, id_usuario=5), {}), interrupciones.SIN_ZONA)

    def test_duracion_con_hora_anotada_y_aproximada(self):
        reporte = _reporte(1, hace_horas=2)
        inicio = _tz.now() - _td(hours=2)
        segundos, aprox = interrupciones.duracion(reporte, inicio + _td(minutes=90))
        self.assertAlmostEqual(segundos, 90 * 60, delta=2)
        self.assertFalse(aprox)
        resuelto = _reporte(2, estado='resuelto', hace_horas=2, atendido=_tz.now())
        segundos, aprox = interrupciones.duracion(resuelto, None)
        self.assertAlmostEqual(segundos, 2 * 3600, delta=2)
        self.assertTrue(aprox)
        self.assertEqual(interrupciones.duracion(_reporte(3), None), (None, False))

    def test_resumen_promedio_y_zona_mas_afectada(self):
        lista = [
            _reporte(1, ubicacion='[zona: Norte]'), _reporte(2, ubicacion='[zona: Norte]'),
            _reporte(3, ubicacion='[zona: Sur]'), _reporte(4, tipo='fuga', ubicacion='[zona: Sur]'),
        ]
        ahora = _tz.now()
        InterrupcionServicio.objects.create(id_reporte=1, restablecido=ahora - _td(hours=2))  # 1 h
        InterrupcionServicio.objects.create(id_reporte=3, restablecido=ahora)  # 3 h
        interrupciones.completar(lista, zonas_usuario={})
        resumen = interrupciones.resumen(lista)
        self.assertEqual(resumen['total'], 3)
        self.assertEqual(resumen['en_curso'], 1)
        self.assertAlmostEqual(resumen['promedio_seg'], 2 * 3600, delta=3)
        self.assertEqual(resumen['zona_mas_afectada'], 'Norte')
        self.assertEqual(resumen['cortes_zona_mas_afectada'], 2)

    def test_hora_futura_o_antes_del_reporte_se_rechaza(self):
        reporte = _reporte(1, hace_horas=2)
        futuro = (_tz.localtime() + _td(hours=1)).strftime('%Y-%m-%dT%H:%M')
        antes = (_tz.localtime() - _td(hours=5)).strftime('%Y-%m-%dT%H:%M')
        self.assertIsNotNone(interrupciones.leer_hora(futuro, reporte)[1])
        self.assertIsNotNone(interrupciones.leer_hora(antes, reporte)[1])
        self.assertIsNotNone(interrupciones.leer_hora('ayer', reporte)[1])
        hora, error = interrupciones.leer_hora((_tz.localtime() - _td(hours=1)).strftime('%Y-%m-%dT%H:%M'), reporte)
        self.assertIsNone(error)
        self.assertIsNotNone(hora)


@patch('App.interrupciones.zonas_por_usuario', return_value={})
class InterrupcionesPantallaTests(_ConUsuarios):
    def _select(self, lista):
        def select(tabla, *args, **kwargs):
            return [dict(r) for r in lista] if tabla == 'reporte' else []
        return select

    def test_filtro_de_zona_y_resumen(self, _zonas):
        lista = [_reporte(1, ubicacion='[zona: Norte]'), _reporte(2, ubicacion='[zona: Sur]')]
        InterrupcionServicio.objects.create(id_reporte=1, restablecido=_tz.now() - _td(hours=1))
        self.client.force_login(self.admin)
        with patch('App.views_reportes.supabase_client.select', side_effect=self._select(lista)):
            respuesta = self.client.get(reverse('admin_reportes') + '?zona=Norte')
        self.assertContains(respuesta, 'Cortes de agua')
        self.assertContains(respuesta, 'duró 2 h')
        self.assertEqual([r['id_reporte'] for r in respuesta.context['reportes']], [1])
        self.assertEqual(respuesta.context['resumen_cortes']['zona_mas_afectada'], 'Norte')

    def test_csv_trae_zona_y_duracion(self, _zonas):
        lista = [_reporte(1, ubicacion='[zona: Norte]')]
        InterrupcionServicio.objects.create(id_reporte=1, restablecido=_tz.now() - _td(hours=1))
        self.client.force_login(self.admin)
        with patch('App.views_reportes.supabase_client.select', side_effect=self._select(lista)):
            texto = b''.join(self.client.get(reverse('admin_reportes_export'))).decode('utf-8')
        lineas = texto.splitlines()
        self.assertIn('Zona', lineas[0])
        self.assertIn('Duración del corte (min)', lineas[0])
        self.assertIn('Norte', lineas[1])
        self.assertTrue(lineas[1].endswith(',120'))

    def test_marcar_resuelto_guarda_la_hora_y_valida(self, _zonas):
        reporte = _reporte(5, hace_horas=4)
        self.client.force_login(self.admin)
        url = reverse('admin_reporte_update', args=[5])
        with patch('App.views_reportes._cargar_reporte_o_404', return_value=dict(reporte)), \
                patch('App.views_reportes.supabase_client.update') as update, \
                patch('App.views_reportes.supabase_client.insert', return_value={}):
            futuro = (_tz.localtime() + _td(hours=2)).strftime('%Y-%m-%dT%H:%M')
            self.client.post(url, {'estado': 'resuelto', 'prioridad': 'media', 'restablecido': futuro})
            self.assertFalse(InterrupcionServicio.objects.exists())
            update.assert_not_called()
            self.client.post(url, {'estado': 'resuelto', 'prioridad': 'media', 'restablecido': ''})
        guardado = InterrupcionServicio.objects.get(id_reporte=5)
        self.assertLess(abs((guardado.restablecido - _tz.now()).total_seconds()), 10)

    def test_otros_tipos_no_guardan_hora(self, _zonas):
        reporte = _reporte(6, tipo='fuga')
        self.client.force_login(self.admin)
        with patch('App.views_reportes._cargar_reporte_o_404', return_value=dict(reporte)), \
                patch('App.views_reportes.supabase_client.update'), \
                patch('App.views_reportes.supabase_client.insert', return_value={}):
            self.client.post(reverse('admin_reporte_update', args=[6]), {'estado': 'resuelto', 'prioridad': 'media'})
        self.assertFalse(InterrupcionServicio.objects.exists())


# ── PB045: el buscador encuentra la ayuda y hay tutorial ───────────────────

from .search_sections import matching_sections  # noqa: E402


class BuscadorAyudaTests(TestCase):
    def test_manual_aparece_en_la_web(self):
        titulos = [r['titulo'] for r in matching_sections('manual', False)]
        self.assertIn('Manual de usuario', titulos)
        self.assertNotIn('Manual del administrador', titulos)
        admin = [r['titulo'] for r in matching_sections('manual', True)]
        self.assertIn('Manual del administrador', admin)
        self.assertIn('Manual técnico', admin)

    def test_buscar_un_tema_del_manual(self):
        self.assertIn('Manual de usuario', [r['titulo'] for r in matching_sections('preguntas frecuentes', False)])
        self.assertIn('Reportar un problema', [r['titulo'] for r in matching_sections('no llega agua', False)])

    def test_la_app_no_recibe_pantallas_solo_web(self):
        # La app abre cada resultado según su dirección: no se le mandan pantallas que no tiene.
        titulos = [r['titulo'] for r in matching_sections('manual', True, incluir_solo_web=False)]
        # Los manuales sí llegan a la app (los abre en su pantalla de manual); el técnico y el registro manual no.
        self.assertIn('Manual de usuario', titulos)
        self.assertIn('Manual del administrador', titulos)
        self.assertNotIn('Manual técnico', titulos)
        self.assertNotIn('Registro manual', titulos)
        consumo = [r['url'] for r in matching_sections('consumo', True, incluir_solo_web=False)]
        self.assertNotIn('/admin-consumo/', consumo)
        self.assertTrue(matching_sections('presión', False, incluir_solo_web=False))


class TutorialTests(_ConUsuarios):
    def test_usuario_comun_tiene_tutorial(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('manual_usuario'))
        self.assertContains(respuesta, 'tutorial.js')
        self.assertContains(respuesta, 'Ver tutorial')

    def test_admin_no_carga_el_tutorial(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('manual_admin'))
        self.assertNotContains(respuesta, 'tutorial.js')


# ── PB045: buscador dentro del manual, capturas y PDF ──────────────────────

from . import views_manual  # noqa: E402


class BuscadorDelManualTests(_ConUsuarios):
    def test_frase_completa_encuentra_la_seccion(self):
        resultados = views_manual.buscar_en_manual('cómo reportar fuga', views_manual.CONTENIDO_USUARIO)
        self.assertEqual(resultados[0]['id'], 'reportes')
        self.assertEqual(views_manual.buscar_en_manual('xyz', views_manual.CONTENIDO_USUARIO), [])
        self.assertEqual(views_manual.buscar_en_manual('cómo de la', views_manual.CONTENIDO_USUARIO), [])

    def test_manual_web_muestra_resultados_resaltados(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('manual_usuario'), {'q': 'cómo reportar fuga'})
        self.assertContains(respuesta, 'href="#reportes"')
        self.assertContains(respuesta, '#reportes')  # resaltado de la sección
        self.assertContains(respuesta, 'Descargar manual para uso sin internet')
        self.assertContains(respuesta, 'manual-captura')

    def test_manual_de_la_app_busca_sin_sesion(self):
        respuesta = self.client.get(reverse('manual_usuario'), {'app': '1', 'q': 'consumo', 'tema': 'oscuro'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'href="#consumo"')
        # El formulario conserva los datos de la app para seguir dentro de su pantalla.
        self.assertContains(respuesta, 'name="app" value="1"')
        self.assertContains(respuesta, 'name="tema" value="oscuro"')

    def test_sin_resultados_lo_dice(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('manual_usuario'), {'q': 'xyzxyz'})
        self.assertContains(respuesta, 'No encontramos')

    def test_manual_admin_tiene_buscador_sin_pdf(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('manual_admin'), {'q': 'válvula'})
        self.assertContains(respuesta, 'href="#admin-valvulas"')
        self.assertNotContains(respuesta, 'Descargar manual para uso sin internet')

    def test_el_pdf_existe(self):
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find('App/manual/SmartDrop-manual-de-usuario.pdf'))
