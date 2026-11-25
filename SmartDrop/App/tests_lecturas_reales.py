"""Pruebas de la limpieza del 25/11/2026: solo lecturas reales y casas que comparten sensores."""
from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from App import consumo_admin, ingesta_mqtt, queries, views


class LecturasCompartidasTests(SimpleTestCase):
    def test_casa_sin_enlace_usa_sus_propias_lecturas(self):
        self.assertEqual(queries.reading_source_id({'id_vivienda': 1, 'id_vivienda_lecturas': None}), 1)

    def test_casa_enlazada_usa_las_lecturas_de_la_otra(self):
        # NIC-0004 (casa 4) muestra lo que mide el ESP32 de NIC-0001 (casa 1).
        self.assertEqual(queries.reading_source_id({'id_vivienda': 4, 'id_vivienda_lecturas': 1}), 1)

    def test_el_filtro_pide_la_casa_de_origen_sin_repetir(self):
        viviendas = [{'id_vivienda': 4, 'id_vivienda_lecturas': 1}, {'id_vivienda': 1}]
        self.assertEqual(queries._in_filter(viviendas), 'in.(1)')

    def test_admin_ve_el_mismo_consumo_en_las_dos_casas(self):
        dia = timezone.localdate()
        viviendas = [{'id_vivienda': 1}, {'id_vivienda': 4, 'id_vivienda_lecturas': 1}]
        with mock.patch.object(consumo_admin, '_registrado', return_value={(1, dia): 12.5}), \
                mock.patch.object(consumo_admin, '_medido', return_value=({}, {}, None)):
            por_dia, _, _ = consumo_admin._consumo_por_dia([1, 4], dia, dia, consumo_admin._fuentes(viviendas))
        self.assertEqual(por_dia[1][dia], 12.5)
        self.assertEqual(por_dia[4][dia], 12.5)


class LecturasImposiblesTests(SimpleTestCase):
    def test_flujo_de_miles_de_litros_se_descarta(self):
        self.assertTrue(ingesta_mqtt.lectura_imposible('flujo', 2862.6))

    def test_flujo_normal_se_guarda(self):
        self.assertFalse(ingesta_mqtt.lectura_imposible('flujo', 0.13))

    def test_el_nombre_del_sensor_en_supabase_tambien_sirve(self):
        self.assertTrue(ingesta_mqtt.lectura_imposible('Calidad', 5000))
        self.assertFalse(ingesta_mqtt.lectura_imposible('Presion', 9.5))

    def test_mensaje_mqtt_con_flujo_imposible_no_se_guarda(self):
        with mock.patch.object(ingesta_mqtt, '_guardar_lectura') as guardar:
            ingesta_mqtt.procesar_mensaje('smartdrop/1/flujo', '{"id_sensor": 4, "valor": 3174}')
        guardar.assert_not_called()


class UltimaLecturaTests(SimpleTestCase):
    def test_lectura_de_hace_dias_muestra_la_fecha(self):
        hace_una_semana = (timezone.now() - timedelta(days=7)).isoformat()
        self.assertTrue(views._hace_cuanto(hace_una_semana).startswith('el '))

    def test_lectura_reciente(self):
        self.assertEqual(views._hace_cuanto(timezone.now().isoformat()), 'hace unos segundos')
        self.assertEqual(views._hace_cuanto((timezone.now() - timedelta(minutes=5)).isoformat()), 'hace 5 min')

    def test_sin_lecturas(self):
        self.assertIsNone(views._hace_cuanto(None))


class NombreTanqueTests(SimpleTestCase):
    def test_zona_con_la_palabra_zona(self):
        from ml_engine.realdata.sync import nombre_tanque
        self.assertEqual(nombre_tanque('Zona Norte', 'Tanque Principal Vivienda 1'), 'Tanque de la Zona Norte')

    def test_zona_que_es_un_lugar(self):
        from ml_engine.realdata.sync import nombre_tanque
        self.assertEqual(nombre_tanque('San Benito, El Salvador', 'x'), 'Tanque de San Benito')

    def test_sin_zona_deja_el_nombre_de_siempre(self):
        from ml_engine.realdata.sync import nombre_tanque
        self.assertEqual(nombre_tanque('', 'Tanque Principal Vivienda 1'), 'Tanque Principal Vivienda 1')


class PresionEnKpaTests(SimpleTestCase):
    """El sensor manda kPa y se usan los rangos del ESP32: normal de 5 a 20 kPa."""

    def test_presion_de_la_maqueta_es_normal(self):
        self.assertEqual(views._friendly_pressure_message(14.1)[1], 'normal')

    def test_presion_baja_y_alta(self):
        self.assertEqual(views._friendly_pressure_message(3)[1], 'baja')
        self.assertEqual(views._friendly_pressure_message(18)[1], 'normal')
        self.assertEqual(views._friendly_pressure_message(25)[1], 'muy_alta')
        # Aunque pase de la escala sigue siendo muy alta; solo una lectura imposible es fuera de rango.
        self.assertEqual(views._friendly_pressure_message(45)[1], 'muy_alta')
        self.assertEqual(views._friendly_pressure_message(900)[1], 'fuera_rango')
        self.assertEqual(views._friendly_pressure_message(-3)[1], 'fuera_rango')

    def test_aguja_en_escala_de_0_a_30_kpa(self):
        self.assertEqual(views._pressure_pct(15), 50)
        self.assertEqual(views._pressure_pct(40), 100)
        self.assertEqual(views._pressure_pct(None), 0)
