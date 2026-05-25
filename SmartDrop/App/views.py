import json
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm, UsuarioRegisterForm


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
    context = {
        'retro': {
            'mensaje':       '¡Buen trabajo, ahorrando agua!',
            'estado_agua':   'Agua segura',
            'consumo_actual': 1,
            'variacion_mes': '-15%',
            'tendencia':     'baja',
            'fill_y':        90,    # posición Y del relleno en la gota (SVG)
            'fill_h':        70,    # altura del relleno (SVG)
            'total_mes':     150,
            'prom_dia':       5,
            'ahorro':        30,
        },
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
        form.save()
        return redirect('login')

    return render(request, 'App/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data['user']
        login(request, user)
        return redirect('dashboard')

    return render(request, 'App/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')