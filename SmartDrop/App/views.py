import json
from datetime import datetime, timedelta
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, UsuarioRegisterForm
from . import supabase_client
from .backends import sync_user_from_supabase


@login_required(login_url='login')
def dashboard(request):
    context = {
        'presion':  {'estado': 'Normal',  'badge': 'Estable'},
        'calidad':  {'estado': 'Perfecto', 'badge': 'Óptimo'},
        'tanque':   {'porcentaje': 90, 'litros': 540},
        'consumo':  {'hoy': 50},
        'stats': {
            'uptime':        '98%',
            'presion_exacta':'3.2 bar',
            'ph':            '7.2 pH',
            'temperatura':   '22°C',
        },
        'ultima_actualizacion': 'hace 2 min',
    }
    return render(request, 'App/dashboard.html', context)


@login_required(login_url='login')
def tanque(request):
    context = {
        'tanque': {
            'capacidad':     600,
            'porcentaje':    90,
            'litros':        540,
            'bomba':         'Apagada',
            'autonomia':     '2 días',
            'ultima_lectura':'1:55 AM',
        },
        'ultima_actualizacion': 'hace 5 min',
    }
    return render(request, 'App/tanque.html', context)


@login_required(login_url='login')
def calidad(request):
    context = {
        'calidad': {
            'titulo':         '¡AGUA SEGURA!',
            'estado':         'Perfecto',
            'badge':          'Óptimo',
            'estrellas':       5,
            'estrellas_range': range(5),
            'estrellas_vacias':range(0),
            'descripcion':    'Se puede tomar agua con seguridad.',
            'anomalias':      'No se han detectado anomalías en los últimos 30 días.',
            'ultimo_analisis':'11:30 AM',
            'ph':             '7.2',
            'cloro':          '0.3 mg/L',
            'turbidez':       '0.8 NTU',
            'conductividad':  '320 µS/cm',
            'temperatura':    '22°C',
        },
        'ultima_actualizacion': 'hace 1 min',
    }
    return render(request, 'App/calidad.html', context)


@login_required(login_url='login')
def presion(request):
    valor     = 2.9
    max_bar   = 5.0
    pct       = (valor / max_bar) * 100
    # Arco SVG: longitud total del arco ≈ 251px
    gauge_dash = int((valor / max_bar) * 251)

    context = {
        'presion': {
            'estado':     'Normal',
            'valvula':    'ABIERTA',
            'valor':       valor,
            'gauge_dash':  gauge_dash,
            'needle_pos':  int(pct),
            'nota':       'La presión es adecuada para el uso de duchas y lavadoras.',
            'actualizado':'Actualizado hace 10 segundos',
            'min_dia':    2.4,
            'max_dia':    3.1,
            'prom_dia':   2.8,
        },
        'ultima_actualizacion': 'hace 10 seg',
    }
    return render(request, 'App/presion.html', context)


@login_required(login_url='login')
def consumo(request):
    datos_semana  = [8, 12, 7, 15, 10, 9, 5]
    labels_semana = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    datos_dia     = [2, 3, 1, 4, 2, 5, 3, 2, 1, 4, 3, 2]
    labels_dia    = ['6am','7am','8am','9am','10am','11am','12pm','1pm','2pm','3pm','4pm','5pm']
    datos_mes     = [180, 210, 195, 220, 190, 205, 215, 200, 185, 210, 198, 220]
    labels_mes    = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    context = {
        'consumo': {
            'valor_semana':  5,
            'valor_dia':     50,
            'valor_mes':     200,
            'estado_label':  'CONSUMO LIGERAMENTE ELEVADO',
            'variacion':     4.5,
            'mes':           'Noviembre',
            'prom_dia':      7,
            'dia_alto':      15,
            'dia_bajo':      5,
            'datos_semana':  json.dumps(datos_semana),
            'labels_semana': json.dumps(labels_semana),
            'datos_dia':     json.dumps(datos_dia),
            'labels_dia':    json.dumps(labels_dia),
            'datos_mes':     json.dumps(datos_mes),
            'labels_mes':    json.dumps(labels_mes),
            'historial': [
                {'label': 'Semana 1', 'valor': 48},
                {'label': 'Semana 2', 'valor': 52},
                {'label': 'Semana 3', 'valor': 45},
                {'label': 'Semana 4', 'valor': 55},
            ],
        },
        'ultima_actualizacion': 'hace 3 min',
    }
    return render(request, 'App/consumo.html', context)


@login_required(login_url='login')
def retroalimentacion(request):
    # Intentar obtener la última retroalimentación desde Supabase REST
    retro = None
    try:
        row = supabase_client.fetch_latest('retroalimentacion_consumo')
        if row:
            retro = {
                'mensaje':       row.get('mensaje_generado') or '¡Buen trabajo, ahorrando agua!',
                'estado_agua':   'Agua segura',
                'consumo_actual': row.get('diferencia_consumo') or 0,
                'variacion_mes': f"{row.get('diferencia_consumo') or 0}%",
                'tendencia':     'baja',
                'fill_y':        90,
                'fill_h':        70,
                'total_mes':     row.get('consumo_total') or 150,
                'prom_dia':      row.get('consumo_promedio') or 5,
                'ahorro':        row.get('diferencia_consumo') or 30,
            }
    except Exception:
        retro = None

    if not retro:
        retro = {
            'mensaje':       '¡Buen trabajo, ahorrando agua!',
            'estado_agua':   'Agua segura',
            'consumo_actual': 1,
            'variacion_mes': '-15%',
            'tendencia':     'baja',
            'fill_y':        90,
            'fill_h':        70,
            'total_mes':     150,
            'prom_dia':       5,
            'ahorro':        30,
        }

    context = {
        'retro': retro,
        'ultima_actualizacion': 'hace 1 hora',
    }
    return render(request, 'App/retroalimentacion.html', context)


@login_required(login_url='login')
def recomendaciones(request):
    context = {
        'recomendaciones': [
            {
                'titulo':     'Cerrar la llave mientras lavas los dientes',
                'impacto':    'Ahorra hasta 10L diarios',
                'completado':  False,
            },
            {
                'titulo':     'Reducir el tiempo de ducha a 5 minutos',
                'impacto':    'Ahorra hasta 50L diarios',
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
        'ultima_actualizacion': 'hace 1 día',
    }
    return render(request, 'App/recomendaciones.html', context)


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
        return redirect('dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data['user']
        login(request, user)
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


    