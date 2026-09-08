import calendar
import csv
import json
import logging
import math
from datetime import datetime, timedelta
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, UsuarioRegisterForm
from . import supabase_client
from .backends import sync_user_from_supabase
from .mqtt_service import MqttError, publish_command


logger = logging.getLogger(__name__)


def _owned_sensor_data(request):
    """Return only sensor readings belonging to the authenticated user's homes."""
    propietario_id = getattr(request.user, 'supabase_id', None) or request.user.id_usuario
    viviendas = supabase_client.select(
        'vivienda',
        'id_vivienda,nic,direccion',
        {'id_usuario_propietario': f'eq.{propietario_id}', 'limit': '1000'},
    )
    vivienda_ids = [str(row['id_vivienda']) for row in viviendas if row.get('id_vivienda')]
    if not vivienda_ids:
        return viviendas, [], [], []

    lecturas = supabase_client.select(
        'lectura',
        '*',
        {
            'id_vivienda': f"in.({','.join(vivienda_ids)})",
            'order': 'fecha_registro.desc',
            'limit': '2000',
        },
    )
    sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
    try:
        tanques = supabase_client.select(
            'tanque',
            'id_sensor_nivel,capacidad_maxima_litros,altura_total',
            {'limit': '1000'},
        )
    except Exception:
        tanques = []
    sensor_lookup = {str(sensor.get('id_sensor')): sensor for sensor in sensores}
    for lectura in lecturas:
        sensor = sensor_lookup.get(str(lectura.get('id_sensor')), {})
        lectura['tipo_sensor'] = (sensor.get('tipo_sensor') or '').lower()
        lectura['unidad_medida'] = sensor.get('unidad_medida') or ''
    return viviendas, sensores, lecturas, tanques


def _owned_consumption(request):
    propietario_id = getattr(request.user, 'supabase_id', None) or request.user.id_usuario
    viviendas = supabase_client.select(
        'vivienda',
        'id_vivienda,nic,direccion',
        {'id_usuario_propietario': f'eq.{propietario_id}', 'limit': '1000'},
    )
    vivienda_ids = [str(row['id_vivienda']) for row in viviendas if row.get('id_vivienda')]
    if not vivienda_ids:
        return viviendas, []
    rows = supabase_client.select(
        'consumo',
        '*',
        {
            'id_vivienda': f"in.({','.join(vivienda_ids)})",
            'order': 'fecha.desc',
            'limit': '1000',
        },
    )
    return viviendas, rows


def _safe_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_readings_by_type(lecturas):
    latest = {}
    for lectura in lecturas:
        tipo = lectura.get('tipo_sensor', '')
        if tipo and tipo not in latest:
            latest[tipo] = lectura
    return latest


def _find_reading(latest, *names):
    for tipo, lectura in latest.items():
        if any(name in tipo for name in names):
            return lectura
    return None


def _quality_status(tds_value):
    if tds_value is None:
        return 'Sin datos', 'Sin lectura', 0
    if tds_value <= 300:
        return 'Buena', 'Óptima', 5
    if tds_value <= 600:
        return 'Media', 'Aceptable', 3
    return 'Mala', 'Revisar', 1


def _tank_level_data(level, sensors):
    if not level:
        return 0, 0, 0
    value = _safe_float(level.get('valor'))
    sensor = next((item for item in sensors if str(item.get('id_sensor')) == str(level.get('id_sensor'))), {})
    configured_capacity = _safe_float(sensor.get('capacidad_maxima_litros'))
    configured_height = _safe_float(sensor.get('altura_total'))
    sensor_min = _safe_float(sensor.get('rango_min'))
    sensor_max = _safe_float(sensor.get('rango_max'))
    capacity = sensor_max if sensor_max > sensor_min else 100
    percentage = ((value - sensor_min) / (capacity - sensor_min) * 100) if capacity > sensor_min else 0
    if configured_height > 0:
        percentage = value / configured_height * 100
    display_capacity = configured_capacity or capacity
    liters = display_capacity * percentage / 100
    return round(liters, 2), round(min(max(percentage, 0), 100), 2), round(display_capacity, 2)


@login_required(login_url='login')
def dashboard(request):
    if getattr(request.user, 'rol_id', None) == 2:
        return redirect('admin_panel')

    try:
        viviendas, sensores, lecturas, tanques = _owned_sensor_data(request)
        _, consumption_rows = _owned_consumption(request)
    except Exception:
        viviendas, sensores, lecturas, tanques, consumption_rows = [], [], [], [], []
    latest = _latest_readings_by_type(lecturas)
    pressure = _find_reading(latest, 'presion', 'pressure')
    quality = _find_reading(latest, 'tds', 'calidad', 'ph')
    level = _find_reading(latest, 'nivel', 'level')
    tds_value = _safe_float(quality.get('valor')) if quality else None
    quality_state, quality_badge, _ = _quality_status(tds_value)
    tank = next((item for item in tanques if str(item.get('id_sensor_nivel')) == str(level.get('id_sensor'))), {}) if level else {}
    level_value, level_percentage, level_capacity = _tank_level_data(level, [dict(sensor, **tank) for sensor in sensores])
    consumption = _safe_float(
        consumption_rows[0].get('consumo_total') or consumption_rows[0].get('consumo_promedio')
    ) if consumption_rows else 0

    context = {
        'presion':  {'estado': 'Normal' if pressure else 'Sin datos', 'badge': 'Estable' if pressure else 'Sin lectura'},
        'calidad':  {'estado': quality_state, 'badge': quality_badge, 'tds': tds_value or 0},
        'tanque':  {'porcentaje': level_percentage, 'litros': round(level_capacity * level_percentage / 100, 2)},
        'consumo':  {'hoy': consumption},
        'stats': {
            'uptime':        'Disponible' if lecturas else 'Sin datos',
            'presion_exacta':f"{_safe_float(pressure.get('valor')) if pressure else 0} bar",
            'ph':            f"{_safe_float(_find_reading(latest, 'ph').get('valor')) if _find_reading(latest, 'ph') else 0} pH",
            'temperatura':   f"{_safe_float(_find_reading(latest, 'temper').get('valor')) if _find_reading(latest, 'temper') else 0}°C",
        },
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/dashboard.html', context)


@login_required(login_url='login')
def tanque(request):
    try:
        viviendas, sensores, lecturas, tanques = _owned_sensor_data(request)
    except Exception:
        viviendas, sensores, lecturas, tanques = [], [], [], []

    vivienda_id = request.session.get('vivienda_id')
    viviendas_ids = {str(vivienda.get('id_vivienda')) for vivienda in viviendas}
    if str(vivienda_id) not in viviendas_ids:
        vivienda_id = viviendas[0].get('id_vivienda') if viviendas else None

    autonomia = 'Sin estimación'
    estado_bomba = 'Sin bomba registrada'
    if vivienda_id:
        try:
            predicciones = supabase_client.select(
                'prediccion_desabasto',
                '*',
                {'id_vivienda': f'eq.{vivienda_id}', 'order': 'fecha.desc', 'limit': '1'},
            )
            prediccion = predicciones[0] if predicciones else {}
            horas_restantes = prediccion.get('horas_restantes')
            if horas_restantes is not None:
                horas = float(horas_restantes)
                dias, horas = divmod(int(horas), 24)
                autonomia = (
                    f'{dias} día{"s" if dias != 1 else ""} y {horas} hrs aprox.'
                    if dias else f'{horas} hrs aprox.'
                )
            elif prediccion.get('mensaje'):
                autonomia = str(prediccion['mensaje'])
        except Exception:
            pass

        try:
            bombas = supabase_client.select(
                'bomba',
                '*',
                {'id_vivienda': f'eq.{vivienda_id}', 'limit': '1'},
            )
            bomba = bombas[0] if bombas else {}
            estado = str(bomba.get('estado_actual') or bomba.get('estado') or '').lower()
            if estado in {'encendida', 'encendido', 'activa', 'automatico', 'automatica'}:
                estado_bomba = 'Enciende automáticamente'
            elif bomba:
                estado_bomba = 'Apagada (Standby)'
        except Exception:
            pass

    level = _find_reading(_latest_readings_by_type(lecturas), 'nivel', 'level')
    tank = next((item for item in tanques if str(item.get('id_sensor_nivel')) == str(level.get('id_sensor'))), {}) if level else {}
    nivel, porcentaje, capacidad = _tank_level_data(level, [dict(sensor, **tank) for sensor in sensores])
    litros = round(capacidad * porcentaje / 100, 2)
    context = {
        'tanque': {
            'capacidad':     capacidad,
            'porcentaje':    porcentaje,
            'litros':        litros,
            'ultima_lectura': level.get('fecha_registro', 'Sin datos') if level else 'Sin datos',
        },
        'autonomia': autonomia,
        'estado_bomba': estado_bomba,
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/tanque.html', context)


@login_required(login_url='login')
def calidad(request):
    try:
        viviendas, _, lecturas, _ = _owned_sensor_data(request)
    except Exception:
        viviendas, lecturas = [], []
    latest = _latest_readings_by_type(lecturas)
    tds = _find_reading(latest, 'tds', 'calidad')
    ph = _find_reading(latest, 'ph')
    chlorine = _find_reading(latest, 'cloro', 'chlorine')
    turbidity = _find_reading(latest, 'turbidez', 'turbidity')
    conductivity = _find_reading(latest, 'conductividad', 'conductivity')
    temperature = _find_reading(latest, 'temper')
    tds_value = _safe_float(tds.get('valor')) if tds else None
    quality_state, quality_badge, quality_stars = _quality_status(tds_value)
    context = {
        'calidad': {
            'titulo':         f'CALIDAD {quality_state.upper()}' if tds or ph else 'SIN DATOS DE CALIDAD',
            'estado':         quality_state,
            'badge':          quality_badge,
            'estrellas':       quality_stars,
            'estrellas_range': range(quality_stars),
            'estrellas_vacias':range(5 - quality_stars),
            'descripcion':    f'TDS: {tds_value} ppm. Buena: hasta 300, media: 301-600, mala: más de 600.' if tds else 'No hay lecturas de calidad para tu vivienda.',
            'anomalias':      f'Clasificación TDS: {quality_state}.' if tds else 'No se han recibido lecturas.',
            'ultimo_analisis':(tds or ph or {}).get('fecha_registro', 'Sin datos'),
            'tds':            tds_value or 0,
            'ph':             _safe_float(ph.get('valor')) if ph else 0,
            'cloro':          _safe_float(chlorine.get('valor')) if chlorine else 0,
            'turbidez':       _safe_float(turbidity.get('valor')) if turbidity else 0,
            'conductividad':  _safe_float(conductivity.get('valor')) if conductivity else 0,
            'temperatura':    _safe_float(temperature.get('valor')) if temperature else 0,
        },
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/calidad.html', context)


@login_required(login_url='login')
def presion(request):
    try:
        viviendas, _, lecturas, _ = _owned_sensor_data(request)
    except Exception:
        viviendas, lecturas = [], []
    pressure_rows = [row for row in lecturas if 'presion' in row.get('tipo_sensor', '') or 'pressure' in row.get('tipo_sensor', '')]
    valor = _safe_float(pressure_rows[0].get('valor')) if pressure_rows else 0
    max_bar   = 5.0
    pct       = min(max((valor / max_bar) * 100, 0), 100)
    # Arco SVG: longitud total del arco ≈ 251px
    gauge_dash = int((pct / 100) * 251)

    context = {
        'presion': {
            'estado':     'Normal' if pressure_rows else 'Sin datos',
            'valvula':    'Sin datos',
            'valor':       valor,
            'gauge_dash':  gauge_dash,
            'needle_pos':  int(pct),
            'nota':       'Valor recibido desde el sensor de presión de tu vivienda.' if pressure_rows else 'No hay lecturas de presión para tu vivienda.',
            'actualizado':'Actualizado ahora' if pressure_rows else 'Sin lectura',
            'min_dia':    min((_safe_float(row.get('valor')) for row in pressure_rows), default=0),
            'max_dia':    max((_safe_float(row.get('valor')) for row in pressure_rows), default=0),
            'prom_dia':   round(sum(_safe_float(row.get('valor')) for row in pressure_rows) / len(pressure_rows), 2) if pressure_rows else 0,
        },
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/presion.html', context)


@login_required(login_url='login')
def consumo(request):
    viviendas = []
    rows = []
    try:
        propietario_id = getattr(request.user, 'supabase_id', None) or request.user.id_usuario
        viviendas = supabase_client.select(
            'vivienda',
            'id_vivienda,nic,direccion',
            {
                'id_usuario_propietario': f'eq.{propietario_id}',
                'limit': '1000',
            },
        )
        vivienda_ids = [str(vivienda['id_vivienda']) for vivienda in viviendas if vivienda.get('id_vivienda')]
        if vivienda_ids:
            rows = supabase_client.select(
                'consumo',
                'id_vivienda,fecha,consumo_total',
                {
                    'id_vivienda': f"in.({','.join(vivienda_ids)})",
                    'order': 'fecha.desc',
                    'limit': '1000',
                },
            )
    except Exception:
        # No mostrar datos de otra vivienda si la consulta no está disponible.
        viviendas = []
        rows = []

    now = timezone.localtime()
    today = now.date()
    selected_month = today.replace(day=1)
    requested_month = request.GET.get('mes')
    if requested_month:
        try:
            selected_month = datetime.strptime(requested_month, '%Y-%m').date().replace(day=1)
        except ValueError:
            selected_month = today.replace(day=1)
    parsed_rows = []
    for row in rows:
        raw_date = row.get('fecha')
        raw_value = row.get('consumo_total')
        if not raw_date or raw_value is None:
            continue
        try:
            row_date = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
            if row_date.tzinfo:
                row_date = timezone.localtime(row_date)
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            continue
        parsed_rows.append((row_date.date(), value))

    week_start = today - timedelta(days=6)
    week_values = {week_start + timedelta(days=offset): 0 for offset in range(7)}
    month_values = {}
    day_values = []
    for row_date, value in parsed_rows:
        if row_date in week_values:
            week_values[row_date] += value
        if row_date == today:
            day_values.append(value)
        month_key = row_date.replace(day=1)
        month_values[month_key] = month_values.get(month_key, 0) + value

    etiquetas_dias = ('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')
    dias_espanol = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')
    meses_espanol = (
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
    )
    days_in_month = calendar.monthrange(selected_month.year, selected_month.month)[1]
    month_days = [selected_month + timedelta(days=offset) for offset in range(days_in_month)]
    daily_values = {day: 0 for day in month_days}
    for row_date, value in parsed_rows:
        if row_date in daily_values:
            daily_values[row_date] += value
    historial_dia_semanas = []
    for week_index in range(0, len(month_days), 7):
        week_days = month_days[week_index:week_index + 7]
        items = [
            {
                'key': day.isoformat(),
                'label': f'{dias_espanol[day.weekday()]} {day.day:02d}/{day.month:02d}',
                'detalle': f'{dias_espanol[day.weekday()]} {day.day:02d}/{day.month:02d}',
                'valor': round(daily_values[day], 2),
            }
            for day in week_days
        ]
        historial_dia_semanas.append({
            'label': f'Semana {len(historial_dia_semanas) + 1}',
            'items': items,
        })
    historial_dia = historial_dia_semanas[0]['items'] if historial_dia_semanas else []
    month_week_values = [0, 0, 0, 0]
    for row_date, value in parsed_rows:
        if row_date.year == selected_month.year and row_date.month == selected_month.month:
            week_index = min((row_date.day - 1) // 7, 3)
            month_week_values[week_index] += value
    historial_semana = [
        {
            'key': f'semana-{index + 1}',
            'label': f'Semana {index + 1}',
            'detalle': f'Semana {index + 1} de {meses_espanol[selected_month.month - 1]}',
            'valor': round(value, 2),
        }
        for index, value in enumerate(month_week_values)
    ]
    year_months = [selected_month.replace(month=month) for month in range(1, 13)]
    historial_mes = [
        {
            'key': month.strftime('%Y-%m'),
            'label': meses_espanol[month.month - 1].capitalize(),
            'detalle': f'{meses_espanol[month.month - 1].capitalize()} {month.year}',
            'valor': round(month_values.get(month, 0), 2),
        }
        for month in year_months
    ]
    labels_semana = [item['label'] for item in historial_semana]
    datos_semana = [item['valor'] for item in historial_semana]
    labels_dia = [item['label'] for item in historial_dia]
    datos_dia = [item['valor'] for item in historial_dia]
    labels_mes = [item['label'][:3] for item in historial_mes]
    datos_mes = [item['valor'] for item in historial_mes]
    valor_dia = round(sum(day_values), 2)
    valor_semana = round(sum(datos_semana), 2)
    valor_mes = round(month_values.get(selected_month, 0), 2)
    average_day = round(valor_mes / max(today.day, 1), 2)
    non_zero_values = [value for _, value in parsed_rows if value > 0]
    highest_day = round(max(non_zero_values), 2) if non_zero_values else 0
    lowest_day = round(min(non_zero_values), 2) if non_zero_values else 0
    estado_label = 'SIN DATOS DE CONSUMO' if not parsed_rows else 'CONSUMO REGISTRADO'

    context = {
        'consumo': {
            'valor_semana': valor_semana,
            'valor_dia': valor_dia,
            'valor_mes': valor_mes,
            'estado_label': estado_label,
            'variacion': 0,
            'mes': selected_month.strftime('%B'),
            'mes_key': selected_month.strftime('%Y-%m'),
            'prom_dia': average_day,
            'dia_alto': highest_day,
            'dia_bajo': lowest_day,
            'datos_semana': datos_semana,
            'labels_semana': labels_semana,
            'datos_dia': datos_dia,
            'labels_dia': labels_dia,
            'datos_mes': datos_mes,
            'labels_mes': labels_mes,
            'historial': historial_semana,
            'historial_dia': historial_dia,
            'historial_dia_semanas': historial_dia_semanas,
            'historial_semana': historial_semana,
            'historial_mes': historial_mes,
        },
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/consumo.html', context)


@login_required(login_url='login')
def retroalimentacion(request):
    viviendas = []
    retro = None
    try:
        viviendas, consumption_rows = _owned_consumption(request)
        now = timezone.localtime()
        current_month = now.date().replace(day=1)
        previous_month = (current_month - timedelta(days=1)).replace(day=1)
        parsed_rows = []
        for row in consumption_rows:
            raw_date = row.get('fecha')
            if not raw_date:
                continue
            try:
                row_date = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                if row_date.tzinfo:
                    row_date = timezone.localtime(row_date)
                value = _safe_float(row.get('consumo_total') or row.get('consumo_promedio'))
                parsed_rows.append((row_date.date(), value))
            except (TypeError, ValueError, OverflowError):
                continue

        current_rows = [value for row_date, value in parsed_rows if row_date.replace(day=1) == current_month]
        previous_rows = [value for row_date, value in parsed_rows if row_date.replace(day=1) == previous_month]
        if parsed_rows:
            current = round(sum(current_rows), 2) if current_rows else parsed_rows[0][1]
            comparison_base = round(sum(previous_rows), 2) if previous_rows else round(
                sum(value for row_date, value in parsed_rows if row_date < current_month)
                / max(len([row_date for row_date, _ in parsed_rows if row_date < current_month]), 1),
                2,
            )
            comparison_period = 'mes anterior' if previous_rows else 'promedio histórico'
            variation = round(((current - comparison_base) / comparison_base) * 100, 2) if comparison_base else 0
            if variation < 0:
                trend = 'baja'
                comparison_message = 'Has reducido tu consumo'
                motivational_message = '¡Buen trabajo ahorrando agua!'
                alert_message = ''
            elif variation > 0:
                trend = 'alta'
                comparison_message = 'Has aumentado tu consumo'
                motivational_message = ''
                alert_message = 'Consumo elevado de agua'
            else:
                trend = 'igual'
                comparison_message = 'Tu consumo se mantiene estable'
                motivational_message = '¡Excelente! Mantienes un consumo de agua estable. Sigue así.'
                alert_message = ''
            recommendations = [
                'Revisa fugas en grifos y tuberías.',
                'Cierra la llave mientras te cepillas los dientes.',
                'Reduce el tiempo de ducha y reutiliza agua cuando sea posible.',
            ]
            if trend == 'alta':
                recommendations.insert(0, 'Reduce el tiempo de ducha y evita dejar llaves abiertas.')
            elif trend == 'baja':
                recommendations.insert(0, 'Mantén tus hábitos actuales de ahorro de agua.')
            retro = {
                'mensaje':       comparison_message,
                'estado_agua':   alert_message or 'Consumo registrado',
                'consumo_actual': current,
                'variacion_mes': f"{variation}%",
                'tendencia':     trend,
                'fill_y':        max(10, 160 - min(current, 150)),
                'fill_h':        min(current, 150),
                'total_mes':     round(sum(current_rows), 2) if current_rows else current,
                'prom_dia':      comparison_base,
                'ahorro':        round(max(comparison_base - current, 0), 2),
                'comparacion':   comparison_message,
                'periodo_comparacion': comparison_period,
                'alerta':        alert_message,
                'motivacion':    motivational_message,
                'recomendaciones': recommendations,
            }
            if alert_message:
                try:
                    alert = supabase_client.insert('alerta', {
                        'tipo_alerta': 'consumo_elevado',
                        'prioridad': 'media',
                        'mensaje': alert_message,
                        'estado_confirmacion': 'pendiente',
                        'datos_adicionales': {
                            'consumo_actual': current,
                            'periodo_comparacion': comparison_period,
                            'consumo_comparacion': comparison_base,
                            'variacion_porcentual': variation,
                            'sugerencias': recommendations[:3],
                        },
                    }) or {}
                    if alert.get('id_alerta'):
                        notification_user_id = getattr(request.user, 'supabase_id', None) or request.user.id_usuario
                        supabase_client.insert('notificacion', {
                            'id_alerta': alert['id_alerta'],
                            'id_usario_destino': notification_user_id,
                            'canal_envio': 'dashboard',
                            'estado_visualizacion': 'no_leida',
                            'fecha_envio': timezone.now().isoformat(),
                        })
                except Exception:
                    logger.exception('No se pudo registrar la alerta de consumo elevado')
    except Exception:
        retro = None

    if not retro:
        retro = {
            'mensaje':       'No hay datos de consumo para generar retroalimentación.',
            'estado_agua':   'Sin datos',
            'consumo_actual': 0,
            'variacion_mes': '0%',
            'tendencia':     'baja',
            'fill_y':        160,
            'fill_h':        0,
            'total_mes':     0,
            'prom_dia':       0,
            'ahorro':        0,
            'comparacion':   'Sin datos de consumo',
            'periodo_comparacion': 'mes anterior',
            'alerta':        '',
            'motivacion':    '',
            'recomendaciones': [
                'Registra consumos para recibir recomendaciones personalizadas.',
                'Revisa periódicamente si existen fugas de agua.',
            ],
        }

    context = {
        'retro': retro,
        'viviendas': viviendas if 'viviendas' in locals() else [],
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/retroalimentacion.html', context)


@login_required(login_url='login')
def recomendaciones(request):
    try:
        viviendas, consumption_rows = _owned_consumption(request)
    except Exception:
        viviendas, consumption_rows = [], []
    total_consumption = round(sum(_safe_float(row.get('consumo_total') or row.get('consumo_promedio')) for row in consumption_rows), 2)
    consumption_note = f'Has registrado {total_consumption}L en tus lecturas recientes.' if consumption_rows else 'Aún no hay consumo registrado para tu vivienda.'
    context = {
        'recomendaciones': [
            {
                'titulo':     'Cerrar la llave mientras lavas los dientes',
                'impacto':    'Ahorro estimado: hasta 10L diarios',
                'completado':  False,
            },
            {
                'titulo':     'Reducir el tiempo de ducha a 5 minutos',
                'impacto':    f'Consumo reciente: {total_consumption}L',
                'completado':  False,
            },
            {
                'titulo':     'Evitar dejar el grifo abierto al lavar platos',
                'impacto':    'Reduce el desperdicio innecesario',
                'completado':  False,
            },
            {
                'titulo':     'Evitar lavar vehículos con manguera',
                'impacto':    'Usa un balde para ahorrar más agua',
                'completado':  False,
            },
            {
                'titulo':     'Reutilizar agua de cocción de verduras',
                'impacto':    'Ahorra hasta 5L por comida',
                'completado':  False,
            },
            {
                'titulo':     'Revisar y reparar fugas en grifos',
                'impacto':    'Una fuga puede desperdiciar 30L al día',
                'completado':  False,
            },
        ],
        'consumo_nota': consumption_note,
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/recomendaciones.html', context)


def _admin_only(request):
    return getattr(request.user, 'rol_id', None) == 2


def _supabase_user_id(user):
    """Return the user's real Supabase identifier, synchronizing it when needed."""
    if getattr(user, 'supabase_id', None):
        return user.supabase_id

    remote_user = supabase_client.get_user_by_email(user.email)
    remote_id = remote_user.get('id_usuario') if remote_user else None
    if not remote_id:
        raise MqttError(
            'No se pudo identificar tu cuenta de Supabase; el movimiento no fue enviado.'
        )

    user.supabase_id = remote_id
    user.save(update_fields=['supabase_id'])
    return remote_id


def _parse_valve_timestamp(value):
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if timezone.is_naive(timestamp):
            timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
        return timestamp
    except (TypeError, ValueError):
        return None


def _valve_statistics(movements, now=None):
    now = now or timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_movements = [
        movement for movement in movements
        if (timestamp := _parse_valve_timestamp(movement.get('fecha_hora')))
        and month_start <= timestamp <= now
    ]
    openings = [movement for movement in month_movements if movement.get('accion') == 'abrir']
    automatic = [
        movement for movement in month_movements
        if str(movement.get('tipo_activacion', '')).lower() in {'automatico', 'automático', 'auto'}
    ]
    durations = [
        float(movement.get('duracion_real') or movement.get('duracion_programada'))
        for movement in month_movements
        if movement.get('duracion_real') or movement.get('duracion_programada')
    ]
    week_start = now - timedelta(days=6)
    daily_openings = {(now - timedelta(days=offset)).date(): 0 for offset in range(7)}
    for movement in openings:
        timestamp = _parse_valve_timestamp(movement.get('fecha_hora'))
        if timestamp and timestamp >= week_start:
            daily_openings[timestamp.date()] = daily_openings.get(timestamp.date(), 0) + 1
    total = len(openings)
    automatic_count = sum(1 for movement in openings if movement in automatic)
    manual_count = total - automatic_count
    return {
        'total_aperturas_mes': total,
        'tiempo_promedio_abierta': round(sum(durations) / len(durations) / 3600, 2) if durations else 0,
        'porcentaje_manual': round(manual_count / total * 100) if total else 0,
        'porcentaje_automatico': round(automatic_count / total * 100) if total else 0,
        'labels_dias': [day.strftime('%d/%m') for day in sorted(daily_openings)],
        'aperturas_dias': [daily_openings[day] for day in sorted(daily_openings)],
    }


def _unusual_valve_activity(movements, now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=1)
    return [
        movement for movement in movements
        if (timestamp := _parse_valve_timestamp(movement.get('fecha_hora'))) and timestamp >= cutoff
    ]


@login_required(login_url='login')
def valvulas(request):
    if not _admin_only(request):
        return redirect('dashboard')

    valvulas_disponibles = []
    error = None
    try:
        valvulas_disponibles = supabase_client.select(
            'valvula',
            'id_valvula,nombre,ping_gpio,estado_actual,estado_operativo,topic_mqtt_comando,ultima_conexion_mqtt,ultima_apertura',
            {'order': 'id_valvula.asc', 'limit': '1000'},
        )
    except Exception:
        error = 'No se pudieron cargar las electroválvulas.'

    movimientos = []
    movimientos_filtrados = []
    usuarios = []
    try:
        movimientos = supabase_client.select(
            'log_valvula',
            'id_log_valvula,id_valvula,accion,estado_anterior,estado_nuevo,'
            'tipo_activacion,id_usuario,fecha_hora,razon,origen_accion,ip_dispositivo,'
            'duracion_programada,duracion_real',
            {'order': 'fecha_hora.desc', 'limit': '1000'},
        )
        user_ids = [str(row['id_usuario']) for row in movimientos if row.get('id_usuario')]
        usuarios = supabase_client.select(
            'usuario',
            'id_usuario,nombre,apellido,correo',
            {'id_usuario': f"in.({','.join(user_ids)})", 'limit': '1000'},
        ) if user_ids else []
        nombres_usuarios = {
            str(usuario['id_usuario']): (
                f"{usuario.get('nombre', '')} {usuario.get('apellido', '')}".strip()
                or usuario.get('correo', '')
            )
            for usuario in usuarios
            if usuario.get('id_usuario')
        }
        nombres_valvulas = {
            str(valvula['id_valvula']): valvula.get('nombre') or f"Válvula #{valvula['id_valvula']}"
            for valvula in valvulas_disponibles
            if valvula.get('id_valvula')
        }
        for movimiento in movimientos:
            tipo = str(movimiento.get('tipo_activacion') or '').strip().lower()
            es_automatico = tipo in {'automatico', 'automática', 'automático', 'auto'}
            movimiento['tipo_mostrar'] = 'Automático' if es_automatico else 'Manual'
            movimiento['usuario_mostrar'] = (
                nombres_usuarios.get(str(movimiento.get('id_usuario')))
                or ('Sistema automático' if es_automatico else 'Usuario no identificado')
            )
            movimiento['valvula_mostrar'] = (
                nombres_valvulas.get(str(movimiento.get('id_valvula')))
                or f"Válvula #{movimiento.get('id_valvula', 'desconocida')}"
            )
        movimientos = movimientos[:1000]
        fecha = request.GET.get('fecha', '').strip()
        usuario = request.GET.get('usuario', '').strip().lower()
        origen = request.GET.get('origen', '').strip().lower()
        movimientos_filtrados = [
            movement for movement in movimientos
            if (not fecha or str(movement.get('fecha_hora', '')).startswith(fecha))
            and (not usuario or usuario in movement.get('usuario_mostrar', '').lower())
            and (not origen or origen in str(movement.get('origen_accion', '')).lower())
        ]
    except Exception:
        movimientos = []
        movimientos_filtrados = []
        if not error:
            error = 'No se pudo cargar el historial de movimientos.'

    if request.GET.get('formato') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="historial_valvulas.csv"'
        writer = csv.writer(response)
        writer.writerow(['Fecha', 'Válvula', 'Acción', 'Tipo', 'Usuario', 'Origen', 'IP dispositivo'])
        for movement in movimientos_filtrados:
            writer.writerow([
                movement.get('fecha_hora', ''), movement.get('valvula_mostrar', ''),
                movement.get('accion', ''), movement.get('tipo_mostrar', ''),
                movement.get('usuario_mostrar', ''), movement.get('origen_accion', ''),
                movement.get('ip_dispositivo', ''),
            ])
        return response

    statistics = _valve_statistics(movimientos)
    movimientos_filtrados = movimientos_filtrados[:50]
    unusual_movements = _unusual_valve_activity(movimientos)
    alerta_actividad = None
    if len(unusual_movements) > 20:
        alerta_actividad = f'Actividad inusual detectada - {len(unusual_movements)} cambios en última hora'
        try:
            admin_id = _supabase_user_id(request.user)
            alert = supabase_client.insert('alerta', {
                'tipo_alerta': 'actividad_valvula',
                'prioridad': 'alta',
                'mensaje': alerta_actividad,
                'estado_confirmacion': 'pendiente',
                'datos_adicionales': {'sugerencia': 'Revisar sistema - posible mal funcionamiento'},
            }) or {}
            if alert.get('id_alerta'):
                supabase_client.insert('notificacion', {
                    'id_alerta': alert['id_alerta'],
                    'id_usario_destino': admin_id,
                    'canal_envio': 'dashboard',
                    'estado_visualizacion': 'no_leida',
                    'fecha_envio': timezone.now().isoformat(),
                })
        except Exception:
            logger.exception('No se pudo generar la alerta de actividad inusual de válvulas')

    return render(request, 'App/valvulas.html', {
        'valvulas': valvulas_disponibles,
        'movimientos': movimientos_filtrados,
        'estadisticas': statistics,
        'alerta_actividad': alerta_actividad,
        'filtros': {
            'fecha': request.GET.get('fecha', ''),
            'usuario': request.GET.get('usuario', ''),
            'origen': request.GET.get('origen', ''),
        },
        'error': error,
        'ultima_actualizacion': 'hace unos segundos',
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
def valvula_comando(request, valvula_id):
    if not _admin_only(request):
        return redirect('dashboard')

    comando = (request.POST.get('comando') or '').strip().lower()
    if comando not in {'abrir', 'cerrar'}:
        return redirect('valvulas')

    valvula = None
    try:
        admin_supabase_id = _supabase_user_id(request.user)
        rows = supabase_client.select(
            'valvula',
            'id_valvula,nombre,estado_actual,topic_mqtt_comando',
            {'id_valvula': f'eq.{valvula_id}', 'limit': '1'},
        )
        valvula = rows[0] if rows else None
        if not valvula or not valvula.get('topic_mqtt_comando'):
            raise MqttError('La electroválvula no tiene un topic MQTT configurado.')

        publish_command(valvula['topic_mqtt_comando'], comando)
        estado_nuevo = 'abierta' if comando == 'abrir' else 'cerrada'
        supabase_client.update(
            'valvula',
            {
                'estado_actual': estado_nuevo,
                'ultima_apertura': timezone.now().isoformat() if comando == 'abrir' else None,
            },
            {'id_valvula': f'eq.{valvula_id}'},
        )
        log_payload = {
            'id_valvula': valvula['id_valvula'],
            'accion': comando,
            'estado_anterior': valvula.get('estado_actual') or 'desconocida',
            'estado_nuevo': estado_nuevo,
            'tipo_activacion': 'manual',
            'id_usuario': admin_supabase_id,
            'fecha_hora': timezone.now().isoformat(),
            'ip_dispositivo': request.META.get('REMOTE_ADDR'),
            'origen_accion': 'web',
        }
        log_row = supabase_client.insert('log_valvula', log_payload) or log_payload
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            return JsonResponse({
                'ok': True,
                'id_valvula': valvula['id_valvula'],
                'estado_anterior': valvula.get('estado_actual') or 'desconocida',
                'estado_nuevo': estado_nuevo,
                'accion': comando,
                'tipo_activacion': 'manual',
                'id_usuario': admin_supabase_id,
                'ip_dispositivo': request.META.get('REMOTE_ADDR'),
                'fecha_hora': log_row.get('fecha_hora', log_payload['fecha_hora']),
                'origen_accion': 'web',
                'usuario_mostrar': request.user.get_full_name(),
                'valvula_mostrar': valvula.get('nombre') or f"Válvula #{valvula['id_valvula']}",
            })
        return redirect('valvulas')
    except (MqttError, supabase_client.SupabaseError, ValueError, TypeError) as exc:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
        return render(request, 'App/valvulas.html', {
            'valvulas': [valvula] if valvula else [],
            'error': str(exc),
            'ultima_actualizacion': 'hace unos segundos',
        }, status=502)


@login_required(login_url='login')
def usuario(request):
    context = {
        'usuario': {
            'nombre':        request.user.get_full_name() if request.user.is_authenticated else 'Invitado',
            'direccion':     'Casa #14 · Bloque B, Colonia La Merced, San Miguel',
            'ciudad':        'San Miguel, El Salvador',
            'email':         request.user.email if request.user.is_authenticated else 'usuario@ejemplo.com',
            'telefono':      '+503 7000-0000',
            'miembro_desde': request.user.fecha_registro.strftime('%B %Y') if request.user.is_authenticated else 'Enero 2024',
            'notificaciones': [
                {'nombre': 'Alertas de nivel', 'icono': 'bell',        'activo': True},
                {'nombre': 'Suministro',        'icono': 'droplet',     'activo': True},
                {'nombre': 'Calidad',           'icono': 'shield-check','activo': True},
                {'nombre': 'Consumo elevado',   'icono': 'chart-bar',   'activo': False},
            ],
        },
        'ultima_actualizacion': 'hace 5 min',
    }
    return render(request, 'App/usuario.html', context)


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    

    form = UsuarioRegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except supabase_client.SupabaseError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect('login')

    return render(request, 'App/register.html', {'form': form})


def _user_payload(user):
    return {
        'id': user.id_usuario,
        'email': user.email,
        'nombre': user.nombre,
        'apellido': user.apellido,
        'nombre_completo': user.get_full_name(),
        'rol': user.rol.nombre_rol if user.rol_id else 'user',
    }


@csrf_exempt
@require_http_methods(['POST'])
def api_register(request):
    """Registro JSON para app móvil. Guarda el usuario en Supabase."""
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    form = UsuarioRegisterForm(data)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': form.errors}, status=400)

    try:
        user = form.save()
    except supabase_client.SupabaseError as exc:
        status = exc.status_code or 500
        return JsonResponse({'ok': False, 'error': str(exc)}, status=status)

    return JsonResponse({'ok': True, 'user': _user_payload(user)}, status=201)


@csrf_exempt
@require_http_methods(['POST'])
def api_login(request):
    """Login JSON para app móvil. Valida credenciales contra Supabase."""
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    form = LoginForm(data)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': form.errors}, status=401)

    user = form.cleaned_data['user']
    return JsonResponse({'ok': True, 'user': _user_payload(user)})


@csrf_exempt
@require_http_methods(['POST'])
def api_lectura(request):
    """Valida y envía una lectura del ESP32 a Supabase."""
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    required_fields = {'id_sensor', 'fecha_registro', 'valor'}
    if not isinstance(data, dict) or set(data) != required_fields:
        logger.warning(
            'Intento de lectura IoT con formato no permitido desde %s. Campos recibidos: %s',
            request.META.get('REMOTE_ADDR', 'desconocida'),
            sorted(data.keys()) if isinstance(data, dict) else 'JSON no objeto',
        )
        return JsonResponse(
            {
                'ok': False,
                'error': 'El JSON debe contener únicamente id_sensor, fecha_registro y valor',
            },
            status=400,
        )

    sensor_id = data['id_sensor']
    value = data['valor']
    timestamp = data['fecha_registro']
    if (
        not isinstance(sensor_id, int)
        or isinstance(sensor_id, bool)
        or sensor_id <= 0
        or not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not isinstance(timestamp, str)
    ):
        return JsonResponse({'ok': False, 'error': 'Tipos de datos inválidos'}, status=400)

    try:
        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        return JsonResponse(
            {'ok': False, 'error': 'fecha_registro debe estar en formato ISO 8601'},
            status=400,
        )

    payload = {
        'id_sensor': sensor_id,
        'fecha_registro': timestamp,
        'valor': value,
    }
    try:
        row = supabase_client.insert('lectura', payload)
    except Exception:
        logger.exception('Error al guardar lectura IoT en Supabase')
        return JsonResponse(
            {'ok': False, 'error': 'No se pudo guardar la lectura'},
            status=502,
        )

    return JsonResponse({'ok': True, 'lectura': row}, status=201)


def login_view(request):
    if request.user.is_authenticated:
        if getattr(request.user, 'rol_id', None) == 2:
            return redirect('admin_panel')
        return redirect('dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data['user']
        login(request, user)
        if getattr(user, 'rol_id', None) == 2:
            return redirect('admin_panel')
        return redirect('dashboard')

    return render(request, 'App/login.html', {'form': form})


@login_required(login_url='login')
def admin_panel(request):
    """Panel administrativo: accesible sólo a usuarios con rol 'admin'.

    Muestra lecturas recientes, viviendas y sensores para supervisión global.
    """
    # Authorize only users that have role id == 2 (admin)
    if getattr(request.user, 'rol_id', None) != 2:
        return redirect('dashboard')

    lecturas = []
    viviendas = []
    sensores = []
    try:
        lecturas = supabase_client.select('lectura', '*', {'order': 'fecha_registro.desc', 'limit': '200'})
        viviendas = supabase_client.select('vivienda', '*', {'limit': '1000'})
        sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
    except Exception:
        # En caso de fallo con Supabase, devolver listas vacías y permitir que la plantilla lo muestre
        pass

    # Construir lookup de sensores por id para mostrar tipo/unidad/icono en la UI
    sensor_lookup = {}
    try:
        for s in sensores:
            # normalizar y garantizar claves para la plantilla
            if 'unidad' not in s:
                s['unidad'] = s.get('unidad_medida') or s.get('unidad') or ''
            if 'tipo_sensor' not in s:
                s['tipo_sensor'] = s.get('tipo') or s.get('tipo_sensor') or 'desconocido'
            sid = s.get('id_sensor')
            try:
                sid_key = int(sid)
            except Exception:
                sid_key = sid
            sensor_lookup[sid_key] = s
    except Exception:
        sensor_lookup = {}

    icon_map = {
        'nivel': 'ti ti-droplet',
        'calidad': 'ti ti-test-tube',
        'flujo': 'ti ti-wave-sine',
        'presion': 'ti ti-gauge',
    }

    enriched = []
    try:
        for row in lecturas:
            sid = row.get('id_sensor')
            try:
                sid_key = int(sid)
            except Exception:
                sid_key = sid
            sensor = sensor_lookup.get(sid_key) or {}
            tipo = (sensor.get('tipo_sensor') or sensor.get('tipo') or 'desconocido')
            unidad = sensor.get('unidad_medida') or sensor.get('unidad') or ''
            icon = icon_map.get(tipo, 'ti ti-device')
            newrow = dict(row)
            newrow['sensor_tipo'] = tipo
            newrow['sensor_unidad'] = unidad
            newrow['sensor_icon'] = icon
            enriched.append(newrow)
    except Exception:
        enriched = lecturas

    # Nota: no se construyen arrays de gráfico aquí (limpieza de UI de sensores)

    context = {
        'lecturas': enriched,
        'viviendas': viviendas,
        'sensores': sensores,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/admin_panel.html', context)


@login_required(login_url='login')
def sensor_detail(request, sensor_id):
    # Admin-only
    if getattr(request.user, 'rol_id', None) != 2:
        return redirect('dashboard')

    sensores = []
    lecturas = []
    try:
        sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
        lecturas = supabase_client.select('lectura', '*', {'order': 'fecha_registro.desc', 'limit': '1000'})
    except Exception:
        pass

    # buscar sensor
    sensor = None
    try:
        for s in sensores:
            sid = s.get('id_sensor')
            try:
                if str(sid) == str(sensor_id):
                    sensor = s
                    break
            except Exception:
                continue
    except Exception:
        sensor = None

    # filtrar lecturas para este sensor
    rows = []
    try:
        for r in lecturas:
            try:
                if str(r.get('id_sensor')) == str(sensor_id):
                    rows.append(r)
            except Exception:
                continue
    except Exception:
        rows = []

    # Los datos vienen ordenados desc desde Supabase; invertir para gráfica ascendente
    rows_asc = list(reversed(rows))
    labels = [row.get('fecha_registro') for row in rows_asc]
    data = []
    for row in rows_asc:
        try:
            data.append(float(row.get('valor') or 0))
        except Exception:
            data.append(0)

    context = {
        'sensor': sensor or {'id_sensor': sensor_id, 'tipo_sensor': 'Sensor', 'modelo': '', 'unidad': ''},
        'sensores_comparacion': [s for s in sensores if str(s.get('id_sensor')) != str(sensor_id)],
        'labels': json.dumps(labels),
        'data': json.dumps(data),
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/sensor_detail.html', context)


@login_required(login_url='login')
def sensor_data(request, sensor_id):
    """Return JSON labels/data for a sensor filtered by range GET param.
    range: one of '1h','1d','1w','1m' (defaults to '1d')
    """
    if getattr(request.user, 'rol_id', None) != 2:
        return JsonResponse({'ok': False, 'error': 'unauthorized'}, status=403)

    rng = request.GET.get('range', '1d')
    now = datetime.utcnow()
    if rng == '1h':
        cutoff = now - timedelta(hours=1)
    elif rng == '1w':
        cutoff = now - timedelta(days=7)
    elif rng == '1m':
        cutoff = now - timedelta(days=30)
    else:
        cutoff = now - timedelta(days=1)
    compare_id = request.GET.get('compare_id', '').strip()
    try:
        sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
        lecturas = supabase_client.select('lectura', '*', {'order': 'fecha_registro.desc', 'limit': '2000'})
    except Exception:
        return JsonResponse({'ok': False, 'error': 'db_error'}, status=500)

    sensor = next((s for s in sensores if str(s.get('id_sensor')) == str(sensor_id)), {})
    comparison = next((s for s in sensores if str(s.get('id_sensor')) == compare_id), {}) if compare_id else {}
    filtered = []
    comparison_filtered = []
    for row in lecturas:
        try:
            raw = row.get('fecha_registro')
            if not raw:
                continue
            ts = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if ts.replace(tzinfo=None) < cutoff:
                continue
            if str(row.get('id_sensor')) == str(sensor_id):
                filtered.append((ts, row))
            if comparison and str(row.get('id_sensor')) == compare_id:
                comparison_filtered.append((ts, row))
        except (TypeError, ValueError):
            continue

    filtered.sort(key=lambda item: item[0])
    comparison_filtered.sort(key=lambda item: item[0])
    labels = [item[0].isoformat() for item in filtered]
    data = [_safe_float(item[1].get('valor')) for item in filtered]
    comparison_labels = [item[0].isoformat() for item in comparison_filtered]
    comparison_data = [_safe_float(item[1].get('valor')) for item in comparison_filtered]
    return JsonResponse({
        'ok': True,
        'labels': labels,
        'data': data,
        'unit': sensor.get('unidad_medida') or sensor.get('unidad') or '',
        'sensor_label': sensor.get('tipo_sensor') or 'Sensor',
        'compare': {
            'labels': comparison_labels,
            'data': comparison_data,
            'unit': comparison.get('unidad_medida') or comparison.get('unidad') or '',
            'sensor_label': comparison.get('tipo_sensor') or 'Comparación',
        } if comparison else None,
    })


def logout_view(request):
    logout(request)
    return redirect('login')
