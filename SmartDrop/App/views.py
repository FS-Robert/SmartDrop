from django.shortcuts import render

def dashboard(request):
    context = {
        'presion': {
            'estado': 'Normal',
            'badge': 'Estable',
            'valor': '3.2 bar',
        },
        'calidad': {
            'estado': 'Perfecto',
            'badge': 'Óptimo',
            'ph': '7.2',
        },
        'tanque': {
            'porcentaje': 90,
            'litros': 540,
        },
        'consumo': {
            'hoy': 50,
        },
        'stats': {
            'uptime': '98%',
            'presion_exacta': '3.2 bar',
            'ph': '7.2 pH',
            'temperatura': '22°C',
        },
        'ultima_actualizacion': 'hace 2 min',
    }
    return render(request, 'App/dashboard.html', context)