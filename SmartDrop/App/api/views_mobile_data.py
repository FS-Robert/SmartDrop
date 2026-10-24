import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import preferencias, queries, supabase_client
from ..queries import safe_float
from .permissions import IsAuthenticatedUser
from .throttling import UserApiThrottle, VincularThrottle

logger = logging.getLogger(__name__)


PARAMETER_NAMES = {
    'flujo': ('flujo', 'flow'),
    'presion': ('presion', 'pressure'),
    'nivel': ('nivel', 'level'),
}


def _readings_for_sensors(sensor_ids, since=None, limit=5000):
    """Lecturas (más recientes primero) de los sensores dados, filtradas en Supabase.

    Se usa para el admin: en vez de traer las N lecturas globales más recientes
    (que pueden ser todas de un solo sensor), se piden las de cada sensor por id,
    igual que hace la vista web `sensor_data`.
    """
    ids = [str(sid) for sid in sensor_ids if str(sid).isascii() and str(sid).isdigit()]
    if not ids:
        return []
    params = {
        'id_sensor': supabase_client.ids_in(ids),
        'order': 'fecha_registro.desc',
        'limit': str(limit),
    }
    if since:
        params['fecha_registro'] = f"gte.{since.strftime('%Y-%m-%dT%H:%M:%S')}"
    return supabase_client.select('lectura', 'id_sensor,id_vivienda,fecha_registro,valor', params)


def _matching_sensors(sensors, parameter):
    """Todos los sensores cuyo tipo coincide con el parámetro (puede haber varios)."""
    names = PARAMETER_NAMES[parameter]
    return [s for s in sensors if any(name in _sensor_type(s) for name in names)]


def _visible_data_and_tanks(request):
    """Viviendas, sensores, lecturas y tanques visibles según el rol del usuario.

    Admin (rol_id == 2): todas las lecturas de todos los sensores.
    Usuario normal: solo los datos de sus viviendas.
    """
    if getattr(request.user, 'rol_id', None) == 2:
        viviendas, sensores, tanques = queries.run_parallel(
            lambda: supabase_client.select('vivienda', 'id_vivienda,nic,direccion', {'limit': '1000'}),
            queries.sensors,
            queries.tanks,
        )
        sensor_ids = [s.get('id_sensor') for s in sensores if s.get('id_sensor')]
        lecturas = _readings_for_sensors(sensor_ids)
        return viviendas, sensores, lecturas, tanques

    data = queries.owned_data(request.user)
    return data.viviendas, data.sensores, data.lecturas, data.tanques


def _visible_data(request):
    """Viviendas, sensores y lecturas visibles según el rol del usuario (sin los tanques)."""
    viviendas, sensores, lecturas, _ = _visible_data_and_tanks(request)
    return viviendas, sensores, lecturas


def _level_in_percent(block, sensor, tanques):
    """Pasa a % de llenado (la misma cuenta de la web) el valor, la serie y el rango de un bloque de nivel en cm.

    `fuera_de_rango` y `estado` ya vienen calculados con los cm contra los límites del sensor y no cambian.
    """
    def percent(value):
        return None if value is None else queries.level_percentage(value, sensor, tanques)

    block = {**block, 'unidad': '%', 'rango_min': percent(block.get('rango_min')), 'rango_max': percent(block.get('rango_max'))}
    if 'valor' in block:
        block['valor'] = percent(block['valor'])
    if 'datos' in block:
        block['datos'] = [{**item, 'valor': percent(item['valor'])} for item in block['datos']]
    return block


def _range_status(value, minimum, maximum):
    """'Alta', 'Baja' o 'Normal' según los límites del sensor (None = sin límite)."""
    if maximum is not None and value > safe_float(maximum):
        return 'Alta'
    if minimum is not None and value < safe_float(minimum):
        return 'Baja'
    return 'Normal'


def _sensor_type(sensor):
    return str(sensor.get('tipo_sensor') or sensor.get('tipo') or '').lower()


def _matching_sensor(sensors, parameter):
    names = PARAMETER_NAMES[parameter]
    return next((sensor for sensor in sensors if any(name in _sensor_type(sensor) for name in names)), None)


def _parse_iso(timestamp):
    """Parsea fecha ISO de Supabase a datetime (naive, para comparar)."""
    try:
        return datetime.fromisoformat(str(timestamp).replace('Z', '+00:00')).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_range_param(value):
    """Parsea un parámetro de fecha ISO enviado por la app; None si no es válido."""
    if not value:
        return None
    return _parse_iso(value)


def _reading_series(sensor, readings, since=None):
    if not sensor:
        return []
    sensor_id = str(sensor.get('id_sensor'))
    result = []
    for row in readings:
        if str(row.get('id_sensor')) != sensor_id:
            continue
        timestamp = row.get('fecha_registro')
        if not timestamp:
            continue
        parsed = _parse_iso(timestamp)
        if parsed is None:
            continue
        if since and parsed < since:
            continue
        value = safe_float(row.get('valor'))
        estado = _range_status(value, sensor.get('rango_min'), sensor.get('rango_max'))
        if estado == 'Baja' and 'flujo' in _sensor_type(sensor):
            estado = 'Normal'  # Flujo bajo o cero = no hay consumo en ese momento; no es una alerta.
        result.append({
            'fecha': timestamp, 'valor': value, 'fuera_de_rango': estado != 'Normal',
            'estado': estado, 'id_vivienda': row.get('id_vivienda'),
        })
    result.reverse()
    return result


class MobileViviendasView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        rows = supabase_client.select(
            'vivienda', '*',
            {'id_usuario_propietario': f'eq.{request.user.id_usuario}', 'limit': '1000'},
        )
        return Response({'viviendas': rows})


class MobileVincularViviendaView(APIView):
    permission_classes = [IsAuthenticatedUser]
    limite_alcance = 'vincular'
    limite_por = 'usuario'

    def post(self, request):
        try:
            vivienda = queries.link_vivienda(
                request.user,
                request.data.get('numero_cuenta', ''),
                request.data.get('nombre_completo_titular', ''),
            )
        except queries.LinkViviendaError as exc:
            return Response({'error': exc.message}, status=exc.status)
        except supabase_client.SupabaseError:
            return Response(
                {'error': 'No se pudo vincular la vivienda en este momento. Inténtalo de nuevo.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({'mensaje': 'Vivienda vinculada exitosamente.', 'vivienda': vivienda})


class MobileEstadoAguaView(APIView):
    permission_classes = [IsAuthenticatedUser]

    @staticmethod
    def _liters_today(request):
        if getattr(request.user, 'rol_id', None) == 2:
            return 0
        today = timezone.localdate()
        try:
            _, rows = queries.owned_consumption(request.user, today, today)
        except Exception:
            return 0
        return queries.consumption_on(rows, today)

    def get(self, request):
        viviendas, sensors, readings, tanques = _visible_data_and_tanks(request)
        latest = {}
        for reading in readings:
            sensor = next((item for item in sensors if str(item.get('id_sensor')) == str(reading.get('id_sensor'))), {})
            kind = _sensor_type(sensor)
            if kind and kind not in latest:
                latest[kind] = (sensor, reading)

        def metric(names):
            pair = next((pair for kind, pair in latest.items() if any(name in kind for name in names)), None)
            if not pair:
                return None
            sensor, reading = pair
            value = safe_float(reading.get('valor'))
            estado = _range_status(
                value,
                sensor.get('umbral_critico_bajo', sensor.get('rango_min')),
                sensor.get('umbral_critico_alto', sensor.get('rango_max')),
            )
            outside = estado != 'Normal'
            result = {
                'valor': value,
                'unidad': sensor.get('unidad_medida') or '',
                'estado': estado,
                'descripcion': 'Valor fuera de rango.' if outside else 'Valor dentro del rango normal.',
                'color': 'rojo' if outside else 'verde',
                'fecha': reading.get('fecha_registro'),
            }
            if any(name in _sensor_type(sensor) for name in ('tds', 'calidad', 'ph')):
                result.update({
                    'valor_ppm': value,
                    'estrellas': 5 if value <= 100 else (4 if value <= 200 else (3 if value <= 300 else 2)),
                    'texto_anomalias': 'Revisar calidad del agua.' if outside else 'Sin anomalías recientes.',
                })
            if any(name in _sensor_type(sensor) for name in ('nivel', 'level')):
                # El sensor mide cm de agua: igual que el dashboard web, se promedian las últimas lecturas
                # y se pasan a % y litros con la altura y capacidad del tanque.
                recientes = queries.recent_values(readings, sensor.get('id_sensor'))
                promedio = round(sum(recientes) / len(recientes), 2) if recientes else value
                liters, percentage, capacity = queries.tank_summary(
                    {'id_sensor': sensor.get('id_sensor'), 'valor': promedio}, sensors, tanques,
                )
                result.update({
                    'porcentaje': percentage,
                    'litros_disponibles': round(liters, 1),
                    'capacidad_maxima_litros': capacity,
                    'barriles_disponibles': round(liters / 158.987, 2),
                    'capacidad_maxima_barriles': round(capacity / 158.987, 2),
                    'bomba_estado': None,
                    'autonomia_estimada_texto': None,
                })
            return result

        pressure = metric(('presion', 'pressure'))
        quality = metric(('tds', 'calidad', 'ph'))
        level = metric(('nivel', 'level'))
        parts = [item for item in (pressure, quality, level) if item]
        has_alert = any(item['color'] == 'rojo' for item in parts)
        return Response({
            'vivienda': viviendas[0] if viviendas else None,
            'resumen_general': {
                'mensaje': 'Revisar parámetros del agua.' if has_alert else 'Agua segura, todo funciona con normalidad.',
                'color': 'rojo' if has_alert else 'verde',
            },
            'presion': pressure,
            'calidad': quality,
            'nivel': level,
            'consumo': {'litros_hoy': self._liters_today(request)},
        })


def _range_key(window):
    """Rango de la serie agregada ('1h','1d','1w','1m') más cercano al intervalo pedido por la app."""
    if window <= timedelta(hours=2):
        return '1h'
    if window <= timedelta(days=2):
        return '1d'
    if window <= timedelta(days=8):
        return '1w'
    return '1m'


class MobileGraficasView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        parameters = [item.strip() for item in request.query_params.get('parametros', 'flujo').split(',')]
        invalid = set(parameters) - set(PARAMETER_NAMES)
        if invalid:
            return Response({'error': f'Parámetros no válidos: {invalid}.'}, status=status.HTTP_400_BAD_REQUEST)

        es_admin = getattr(request.user, 'rol_id', None) == 2

        # Rango pedido por la app (desde/hasta). Por defecto, últimas 24 h.
        desde = _parse_range_param(request.query_params.get('desde'))
        hasta = _parse_range_param(request.query_params.get('hasta'))
        if desde is None:
            desde = datetime.now(dt_timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        if hasta is None:
            hasta = datetime.now(dt_timezone.utc).replace(tzinfo=None)

        if es_admin:
            # Admin: los sensores se comparten entre todas las viviendas; cada punto es el promedio de
            # todas las lecturas registradas en ese intervalo (igual que la gráfica web del sensor).
            range_key = _range_key(hasta - desde)
            sensors, tanques = queries.run_parallel(queries.sensors, queries.tanks)
            result = {}
            chosen = {}
            for parameter in parameters:
                matched = _matching_sensors(sensors, parameter)
                chosen[parameter] = matched[0] if matched else None
            all_series = queries.run_parallel(*[
                (lambda s=sensor: queries.sensor_series(str(s['id_sensor']), range_key) if s else {})
                for sensor in chosen.values()
            ])
            for parameter, series in zip(chosen, all_series):
                sensor = chosen[parameter]
                datos = []
                for key in sorted(series):
                    value = series[key]
                    datos.append({
                        'fecha': datetime.fromtimestamp(key, dt_timezone.utc).isoformat(),
                        'valor': value,
                        'fuera_de_rango': _range_status(value, None if parameter == 'flujo' else sensor.get('rango_min'),
                                                        sensor.get('rango_max')) != 'Normal',
                    })
                logger.info('Graficas admin: parametro=%s rango=%s puntos=%s', parameter, range_key, len(datos))
                result[parameter] = {
                    'unidad': (sensor or {}).get('unidad_medida', ''),
                    'rango_min': (sensor or {}).get('rango_min'),
                    'rango_max': (sensor or {}).get('rango_max'),
                    'datos': datos,
                }
                if parameter == 'nivel' and sensor:
                    result[parameter] = _level_in_percent(result[parameter], sensor, tanques)
            return Response(result)

        # Usuario normal: solo los datos de su(s) vivienda(s).
        _, sensors, readings, tanques = _visible_data_and_tanks(request)
        result = {}
        for parameter in parameters:
            sensor = _matching_sensor(sensors, parameter)
            result[parameter] = {
                'unidad': (sensor or {}).get('unidad_medida', ''),
                'rango_min': (sensor or {}).get('rango_min'),
                'rango_max': (sensor or {}).get('rango_max'),
                'datos': _reading_series(sensor, readings, desde),
            }
            if parameter == 'nivel':
                result[parameter] = _level_in_percent(result[parameter], sensor, tanques)
        return Response(result)


class MobileResumenView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        viviendas, sensors, readings, tanques = _visible_data_and_tanks(request)
        nic_by_id = {row.get('id_vivienda'): row.get('nic') for row in viviendas}
        prefs = preferencias.preferencias_de(request.user)
        response = {}
        for parameter in ('flujo', 'presion', 'nivel'):
            sensor = _matching_sensor(sensors, parameter)
            series = _reading_series(sensor, readings)
            if series:
                last = series[-1]
                response[parameter] = {
                    'valor': last['valor'],
                    'unidad': (sensor or {}).get('unidad_medida', ''),
                    'fecha': last['fecha'],
                    # Las categorías que el usuario desactivó en su perfil no cuentan como alerta.
                    'fuera_de_rango': last['fuera_de_rango'] and preferencias.sensor_habilitado(prefs, parameter),
                    # Para explicar la alerta sin abrir la gráfica: alto/bajo, rango normal y vivienda.
                    'estado': last['estado'],
                    'rango_min': (sensor or {}).get('rango_min'),
                    'rango_max': (sensor or {}).get('rango_max'),
                    'nic': nic_by_id.get(last.get('id_vivienda')),
                }
                if parameter == 'nivel':
                    response[parameter] = _level_in_percent(response[parameter], sensor, tanques)
        response['total_alertas'] = sum(1 for key in ('flujo', 'presion', 'nivel') if response.get(key, {}).get('fuera_de_rango'))
        return Response(response)
