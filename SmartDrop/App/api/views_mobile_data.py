import logging
from datetime import datetime, timedelta

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import queries, supabase_client
from ..queries import safe_float
from .permissions import IsAuthenticatedUser

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
        'id_sensor': f"in.({','.join(ids)})",
        'order': 'fecha_registro.desc',
        'limit': str(limit),
    }
    if since:
        params['fecha_registro'] = f"gte.{since.strftime('%Y-%m-%dT%H:%M:%S')}"
    return supabase_client.select('lectura', 'id_sensor,fecha_registro,valor', params)


def _matching_sensors(sensors, parameter):
    """Todos los sensores cuyo tipo coincide con el parámetro (puede haber varios)."""
    names = PARAMETER_NAMES[parameter]
    return [s for s in sensors if any(name in _sensor_type(s) for name in names)]


def _visible_data(request):
    """Viviendas, sensores y lecturas visibles según el rol del usuario.

    Admin (rol_id == 2): todas las lecturas de todos los sensores.
    Usuario normal: solo los datos de sus viviendas.
    """
    if getattr(request.user, 'rol_id', None) == 2:
        viviendas, sensores = queries.run_parallel(
            lambda: supabase_client.select('vivienda', 'id_vivienda,nic,direccion', {'limit': '1000'}),
            queries.sensors,
        )
        sensor_ids = [s.get('id_sensor') for s in sensores if s.get('id_sensor')]
        lecturas = _readings_for_sensors(sensor_ids)
        return viviendas, sensores, lecturas

    data = queries.owned_data(request.user)
    return data.viviendas, data.sensores, data.lecturas


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
        outside = _range_status(value, sensor.get('rango_min'), sensor.get('rango_max')) != 'Normal'
        result.append({'fecha': timestamp, 'valor': value, 'fuera_de_rango': outside})
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

    def post(self, request):
        account = str(request.data.get('numero_cuenta', '')).strip()
        holder = str(request.data.get('nombre_completo_titular', '')).strip()
        if not account or not holder:
            return Response({'error': 'Completa todos los campos.'}, status=status.HTTP_400_BAD_REQUEST)

        rows = supabase_client.select('vivienda', '*', {'nic': f'eq.{account}', 'limit': '1'})
        if not rows:
            return Response({'error': 'No encontramos ese número de cuenta.'}, status=status.HTTP_404_NOT_FOUND)
        vivienda = rows[0]
        if str(vivienda.get('nombre_completo_titular', '')).strip().lower() != holder.lower():
            return Response({'error': 'El nombre no coincide con el titular.'}, status=status.HTTP_400_BAD_REQUEST)
        if vivienda.get('id_usuario_propietario') not in (None, request.user.id_usuario):
            return Response({'error': 'Esta vivienda ya está vinculada.'}, status=status.HTTP_409_CONFLICT)

        supabase_client.update(
            'vivienda',
            {'id_usuario_propietario': request.user.id_usuario},
            {'id_vivienda': f"eq.{vivienda['id_vivienda']}"},
        )
        queries.invalidate_user_viviendas(request.user)
        return Response({'mensaje': 'Vivienda vinculada exitosamente.', 'vivienda': vivienda})


class MobileEstadoAguaView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        viviendas, sensors, readings = _visible_data(request)
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
                capacity = safe_float(sensor.get('capacidad_maxima_litros'), 0)
                result.update({
                    'porcentaje': value,
                    'litros_disponibles': round(capacity * value / 100, 1),
                    'capacidad_maxima_litros': capacity,
                    'barriles_disponibles': round(capacity * value / 100 / 158.987, 2),
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
            'consumo': {'litros_hoy': 0},
        })


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
            desde = datetime.utcnow() - timedelta(days=1)
        if hasta is None:
            hasta = datetime.utcnow()

        if es_admin:
            # Admin: todos los sensores del sistema y todas sus lecturas en el rango.
            _, sensors, _ = _visible_data(request)
            result = {}
            for parameter in parameters:
                matched = _matching_sensors(sensors, parameter)
                readings = _readings_for_sensors(
                    [s.get('id_sensor') for s in matched if s.get('id_sensor')],
                    since=desde,
                )
                logger.info(
                    'Graficas admin: parametro=%s sensores=%s lecturas=%s',
                    parameter, len(matched), len(readings),
                )
                sensor = matched[0] if matched else None
                result[parameter] = {
                    'unidad': '%' if parameter == 'nivel' else (sensor or {}).get('unidad_medida', ''),
                    'rango_min': (sensor or {}).get('rango_min'),
                    'rango_max': (sensor or {}).get('rango_max'),
                    'datos': _reading_series(sensor, readings, desde),
                }
            return Response(result)

        # Usuario normal: solo los datos de su(s) vivienda(s).
        _, sensors, readings = _visible_data(request)
        result = {}
        for parameter in parameters:
            sensor = _matching_sensor(sensors, parameter)
            result[parameter] = {
                'unidad': '%' if parameter == 'nivel' else (sensor or {}).get('unidad_medida', ''),
                'rango_min': (sensor or {}).get('rango_min'),
                'rango_max': (sensor or {}).get('rango_max'),
                'datos': _reading_series(sensor, readings, desde),
            }
        return Response(result)


class MobileResumenView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        _, sensors, readings = _visible_data(request)
        response = {}
        for parameter in ('flujo', 'presion', 'nivel'):
            sensor = _matching_sensor(sensors, parameter)
            series = _reading_series(sensor, readings)
            if series:
                response[parameter] = {
                    'valor': series[-1]['valor'],
                    'unidad': '%' if parameter == 'nivel' else (sensor or {}).get('unidad_medida', ''),
                    'fecha': series[-1]['fecha'],
                    'fuera_de_rango': series[-1]['fuera_de_rango'],
                }
        response['total_alertas'] = sum(1 for key in ('flujo', 'presion', 'nivel') if response.get(key, {}).get('fuera_de_rango'))
        return Response(response)
