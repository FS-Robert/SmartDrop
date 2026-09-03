import json
from datetime import datetime, timedelta
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, UsuarioRegisterForm
from . import supabase_client
from .backends import sync_user_from_supabase
from .mqtt_service import MqttError, publish_command


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
    level = _find_reading(_latest_readings_by_type(lecturas), 'nivel', 'level')
    tank = next((item for item in tanques if str(item.get('id_sensor_nivel')) == str(level.get('id_sensor'))), {}) if level else {}
    nivel, porcentaje, capacidad = _tank_level_data(level, [dict(sensor, **tank) for sensor in sensores])
    litros = round(capacidad * porcentaje / 100, 2)
    context = {
        'tanque': {
            'capacidad':     capacidad,
            'porcentaje':    porcentaje,
            'litros':        litros,
            'bomba':         'Sin datos',
            'autonomia':     'Sin datos',
            'ultima_lectura': level.get('fecha_registro', 'Sin datos') if level else 'Sin datos',
        },
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
            vivienda_filter = f"in.({','.join(vivienda_ids)})"
            rows = supabase_client.select(
                'consumo',
                '*',
                {
                    'id_vivienda': vivienda_filter,
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
    parsed_rows = []
    for row in rows:
        raw_date = row.get('fecha')
        if not raw_date:
            continue
        try:
            row_date = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
            if row_date.tzinfo:
                row_date = timezone.localtime(row_date)
            value = float(row.get('consumo_total') or row.get('consumo_promedio') or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        parsed_rows.append((row_date, value))

    week_start = today - timedelta(days=6)
    month_start = today.replace(day=1)
    week_values = {week_start + timedelta(days=offset): 0 for offset in range(7)}
    month_values = {}
    day_values = []
    for row_date, value in parsed_rows:
        row_day = row_date.date()
        if row_day in week_values:
            week_values[row_day] += value
        if row_day == today:
            day_values.append(value)
        month_key = row_day.replace(day=1)
        month_values[month_key] = month_values.get(month_key, 0) + value

    labels_semana = [day.strftime('%a').capitalize()[:3] for day in week_values]
    datos_semana = [round(value, 2) for value in week_values.values()]
    recent_months = []
    cursor = month_start
    for _ in range(12):
        recent_months.insert(0, cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    labels_mes = [month.strftime('%b').capitalize()[:3] for month in recent_months]
    datos_mes = [round(month_values.get(month, 0), 2) for month in recent_months]
    valor_dia = round(sum(day_values), 2)
    valor_semana = round(sum(datos_semana), 2)
    valor_mes = round(month_values.get(month_start, 0), 2)
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
            'mes': now.strftime('%B'),
            'prom_dia': average_day,
            'dia_alto': highest_day,
            'dia_bajo': lowest_day,
            'datos_semana': json.dumps(datos_semana),
            'labels_semana': json.dumps(labels_semana),
            'datos_dia': json.dumps(day_values),
            'labels_dia': json.dumps([f'{index + 1}' for index in range(len(day_values))]),
            'datos_mes': json.dumps(datos_mes),
            'labels_mes': json.dumps(labels_mes),
            'historial': [
                {'label': f'Semana {index + 1}', 'valor': round(sum(datos_semana[index * 7 // 4:(index + 1) * 7 // 4]), 2)}
                for index in range(4)
            ],
        },
        'viviendas': viviendas,
        'ultima_actualizacion': 'hace unos segundos',
    }
    return render(request, 'App/consumo.html', context)


@login_required(login_url='login')
def retroalimentacion(request):
    retro = None
    try:
        viviendas, consumption_rows = _owned_consumption(request)
        row = consumption_rows[0] if consumption_rows else None
        if row:
            current = _safe_float(row.get('consumo_total') or row.get('consumo_promedio'))
            previous = _safe_float(consumption_rows[1].get('consumo_total') or consumption_rows[1].get('consumo_promedio')) if len(consumption_rows) > 1 else current
            variation = round(((current - previous) / previous) * 100, 2) if previous else 0
            retro = {
                'mensaje':       'Resumen generado con tu consumo registrado.',
                'estado_agua':   'Consumo registrado',
                'consumo_actual': current,
                'variacion_mes': f"{variation}%",
                'tendencia':     'baja' if variation <= 0 else 'alta',
                'fill_y':        max(10, 160 - min(current, 150)),
                'fill_h':        min(current, 150),
                'total_mes':     round(sum(_safe_float(item.get('consumo_total') or item.get('consumo_promedio')) for item in consumption_rows), 2),
                'prom_dia':      round(sum(_safe_float(item.get('consumo_promedio') or item.get('consumo_total')) for item in consumption_rows) / len(consumption_rows), 2),
                'ahorro':        max(0, round(-variation, 2)),
            }
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

    return render(request, 'App/valvulas.html', {
        'valvulas': valvulas_disponibles,
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
        rows = supabase_client.select(
            'valvula',
            'id_valvula,nombre,estado_actual,topic_mqtt_comando',
            {'id_valvula': f'eq.{valvula_id}', 'limit': '1'},
        )
        valvula = rows[0] if rows else None
        if not valvula or not valvula.get('topic_mqtt_comando'):
            raise MqttError('La electroválvula no tiene un topic MQTT configurado.')

        publish_command(valvula['topic_mqtt_comando'], comando)
        try:
            supabase_client.insert('log_valvula', {
                'id_valvula': valvula['id_valvula'],
                'accion': comando,
                'estado_anterior': valvula.get('estado_actual') or 'desconocida',
                'estado_nuevo': 'abierta' if comando == 'abrir' else 'cerrada',
                'tipo_activacion': 'manual',
                'id_usuario': getattr(request.user, 'supabase_id', None) or request.user.id_usuario,
                'fecha_hora': timezone.now().isoformat(),
                'origen_accion': 'web',
            })
        except Exception:
            pass
        return redirect('valvulas')
    except (MqttError, ValueError, TypeError) as exc:
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

    sensores = []
    lecturas = []
    try:
        sensores = supabase_client.select('sensor', '*', {'limit': '1000'})
        lecturas = supabase_client.select('lectura', '*', {'order': 'fecha_registro.desc', 'limit': '2000'})
    except Exception:
        return JsonResponse({'ok': False, 'error': 'db_error'}, status=500)

    # find sensor unit
    unit = ''
    try:
        for s in sensores:
            if str(s.get('id_sensor')) == str(sensor_id):
                unit = s.get('unidad_medida') or s.get('unidad') or ''
                break
    except Exception:
        unit = ''

    filtered = []
    for r in lecturas:
        try:
            if str(r.get('id_sensor')) != str(sensor_id):
                continue
            raw = r.get('fecha_registro')
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(raw)
            except Exception:
                # try slicing microseconds or fallback
                try:
                    ts = datetime.fromisoformat(raw.split('+')[0])
                except Exception:
                    continue
            if ts >= cutoff:
                filtered.append((ts, r))
        except Exception:
            continue

    # sort ascending
    filtered.sort(key=lambda x: x[0])
    labels = [t[0].isoformat() for t in filtered]
    data = []
    for _, r in filtered:
        try:
            data.append(float(r.get('valor') or 0))
        except Exception:
            data.append(0)

    return JsonResponse({'ok': True, 'labels': labels, 'data': data, 'unit': unit})


def logout_view(request):
    logout(request)
    return redirect('login')


    