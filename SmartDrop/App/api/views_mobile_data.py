from datetime import datetime, timedelta

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client
from ..views import _owned_sensor_data, _owned_consumption, _safe_float
from .permissions import IsAuthenticatedUser


PARAMETER_NAMES = {
    'flujo': ('flujo', 'flow'),
    'presion': ('presion', 'pressure'),
    'nivel': ('nivel', 'level'),
}


def _visible_data(request):
    if getattr(request.user, 'rol_id', None) == 2:
        viviendas = supabase_client.select('vivienda', 'id_vivienda,nic,direccion', {'limit': '1000'})
        sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
        lecturas = supabase_client.select(
            'lectura', '*', {'order': 'fecha_registro.desc', 'limit': '2000'}
        )
        return viviendas, sensores, lecturas

    viviendas, sensores, lecturas, _ = _owned_sensor_data(request)
    return viviendas, sensores, lecturas


def _sensor_type(sensor):
    return str(sensor.get('tipo_sensor') or sensor.get('tipo') or '').lower()


def _matching_sensor(sensors, parameter):
    names = PARAMETER_NAMES[parameter]
    return next((sensor for sensor in sensors if any(name in _sensor_type(sensor) for name in names)), None)


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
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
        except ValueError:
            continue
        if since and parsed.replace(tzinfo=None) < since:
            continue
        value = _safe_float(row.get('valor'))
        minimum = sensor.get('rango_min')
        maximum = sensor.get('rango_max')
        outside = (
            minimum is not None and value < _safe_float(minimum)
        ) or (
            maximum is not None and value > _safe_float(maximum)
        )
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
            value = _safe_float(reading.get('valor'))
            minimum = sensor.get('umbral_critico_bajo', sensor.get('rango_min'))
            maximum = sensor.get('umbral_critico_alto', sensor.get('rango_max'))
            outside = (minimum is not None and value < _safe_float(minimum)) or (maximum is not None and value > _safe_float(maximum))
            result = {
                'valor': value,
                'unidad': sensor.get('unidad_medida') or '',
                'estado': 'Alta' if maximum is not None and value > _safe_float(maximum) else ('Baja' if minimum is not None and value < _safe_float(minimum) else 'Normal'),
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
                capacity = _safe_float(sensor.get('capacidad_maxima_litros'), 0)
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

        _, sensors, readings = _visible_data(request)
        since = datetime.utcnow() - timedelta(days=1)
        result = {}
        for parameter in parameters:
            sensor = _matching_sensor(sensors, parameter)
            result[parameter] = {
                'unidad': '%' if parameter == 'nivel' else (sensor or {}).get('unidad_medida', ''),
                'rango_min': (sensor or {}).get('rango_min'),
                'rango_max': (sensor or {}).get('rango_max'),
                'datos': _reading_series(sensor, readings, since),
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
