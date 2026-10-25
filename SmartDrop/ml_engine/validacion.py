"""Limpieza y validación automática de los datos que usa el motor de predicción (PB055).

Cada vez que el sistema sincroniza lecturas (botón "Realizar predicciones" y el monitor automático):

1. *Duplicados:* borra las lecturas repetidas (mismo hogar o tanque, misma métrica y mismo instante) y deja una.
2. *Fuera de rango:* marca como inválidas las lecturas imposibles para el sensor (por ejemplo flujo negativo o
   presión de 9999 kPa). No se borran, pero dejan de contar en los promedios por hora y en los modelos.
3. *Datos insuficientes:* lista los hogares que no tienen historial suficiente para analizarlos, con un
   mensaje claro que se muestra en la web y en la app.

El resumen de la última revisión queda guardado y lo muestra la página "Predicción de suministro".
"""
from __future__ import annotations

import logging
import math
from collections import Counter

from django.conf import settings
from django.db.models import Count, Min
from django.utils import timezone

from ml_engine.models import MonitorState, SensorReading

logger = logging.getLogger(__name__)

CLAVE_RESUMEN = 'validacion'

# Rangos físicos de cada sensor (lo que el aparato puede medir de verdad). Se pueden cambiar en settings.
RANGOS = {
    'flujo': (0.0, 30.0, 'L/min'),     # YF-S201: hasta 30 L/min
    'presion': (0.0, 700.0, 'kPa'),    # MPX5700AP: 0 a 700 kPa
    'calidad': (0.0, 1000.0, 'ppm'),   # sensor TDS: 0 a 1000 ppm
}
NOMBRES = {'flujo': 'Flujo', 'presion': 'Presión', 'calidad': 'Calidad (TDS)'}
RANGO_NIVEL_CM = (0.0, 450.0)          # ultrasónico JSN-SR04T, lectura cruda en cm
TOLERANCIA_NIVEL = 1.05                # el nivel en litros puede pasar un 5 % la capacidad por redondeo


def rangos():
    return {**RANGOS, **getattr(settings, 'ML_RANGOS_VALIDOS', {})}


def motivo_invalido(metric, valor, zona=None):
    """Por qué una lectura no es válida, o '' si está bien. `valor` ya convertido (el nivel, en litros)."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return 'Valor no numérico'
    if not math.isfinite(valor):
        return 'Valor no numérico'
    if metric == 'nivel_tanque':
        capacidad = getattr(zona, 'capacidad_maxima_litros', None)
        if valor < 0 or (capacidad and valor > capacidad * TOLERANCIA_NIVEL):
            return f'Nivel fuera de rango ({valor:.1f} L; capacidad {capacidad or "?"} L)'
        return ''
    minimo, maximo, unidad = rangos().get(metric, (None, None, ''))
    if minimo is not None and not minimo <= valor <= maximo:
        return f'{NOMBRES.get(metric, metric)} fuera de rango ({valor:g} {unidad}; permitido {minimo:g} a {maximo:g})'
    return ''


def motivo_nivel_crudo(valor_cm):
    """Lectura cruda del ultrasónico (antes de pasarla a litros)."""
    try:
        valor_cm = float(valor_cm)
    except (TypeError, ValueError):
        return 'Valor no numérico'
    if not math.isfinite(valor_cm):
        return 'Valor no numérico'
    minimo, maximo = RANGO_NIVEL_CM
    if not minimo <= valor_cm <= maximo:
        return f'Nivel fuera de rango ({valor_cm:g} cm; permitido {minimo:g} a {maximo:g})'
    return ''


# ── Escenario 1: duplicados ──────────────────────────────────────────────────

def eliminar_duplicados(homes=None):
    """Borra las lecturas repetidas y deja la primera. Devuelve cuántas se borraron por métrica."""
    lecturas = SensorReading.objects.all()
    if homes is not None:
        lecturas = lecturas.filter(home__in=homes)
    grupos = (lecturas.values('home_id', 'zone_id', 'metric', 'ts')
              .annotate(n=Count('id'), primera=Min('id')).filter(n__gt=1))
    borradas = Counter()
    for grupo in grupos.iterator():
        sobran = SensorReading.objects.filter(
            home_id=grupo['home_id'], zone_id=grupo['zone_id'], metric=grupo['metric'], ts=grupo['ts'],
        ).exclude(id=grupo['primera'])
        cantidad, _ = sobran.delete()
        borradas[grupo['metric']] += cantidad
    return dict(borradas)


# ── Escenario 2: fuera de rango ──────────────────────────────────────────────

def marcar_fuera_de_rango(homes=None):
    """Marca como inválidas las lecturas válidas que están fuera de rango. Devuelve cuántas por métrica."""
    lecturas = SensorReading.objects.filter(is_valid=True).select_related('zone', 'home__zone')
    if homes is not None:
        lecturas = lecturas.filter(home__in=homes)
    marcadas = Counter()
    cambiar = []
    for lectura in lecturas.iterator(chunk_size=5000):
        zona = lectura.zone or (lectura.home.zone if lectura.home_id else None)
        motivo = motivo_invalido(lectura.metric, lectura.value, zona)
        if motivo:
            lectura.is_valid = False
            lectura.motivo_invalido = motivo[:120]
            cambiar.append(lectura)
            marcadas[lectura.metric] += 1
    SensorReading.objects.bulk_update(cambiar, ['is_valid', 'motivo_invalido'], batch_size=2000)
    return dict(marcadas), {lectura.home_id for lectura in cambiar if lectura.home_id}


# ── Escenario 3: datos insuficientes ─────────────────────────────────────────

def mensaje_insuficiente(item):
    """Texto de la notificación para un hogar sin datos suficientes."""
    nic = item.get('nic') or 'Una vivienda'
    return f"{nic}: no hay suficientes datos históricos para analizarla. {item.get('detail') or ''}".strip()


def hogares_sin_datos_suficientes(homes=None):
    from ml_engine.models import Home
    from ml_engine.realdata.sync import data_quality

    hogares = homes if homes is not None else Home.objects.filter(activo=True, source='real').order_by('id')
    faltan = []
    for home in hogares:
        calidad = data_quality(home)
        if calidad['status'] in ('insuficiente', 'sin_datos'):
            item = {'home_id': home.id, 'nic': home.etiqueta, **calidad}
            item['mensaje'] = mensaje_insuficiente(item)
            faltan.append(item)
    return faltan


# ── Proceso completo ─────────────────────────────────────────────────────────

def validar(homes=None):
    """Corre la limpieza y validación y guarda el resumen. Devuelve el resumen."""
    from ml_engine.realdata.sync import _rebuild_hourly

    inicio = timezone.now()
    duplicados = eliminar_duplicados(homes)
    invalidas, afectados = marcar_fuera_de_rango(homes)
    if afectados:
        from ml_engine.models import Home
        desde = SensorReading.objects.filter(home_id__in=afectados, is_valid=False).aggregate(t=Min('ts'))['t']
        for home in Home.objects.filter(id__in=afectados):
            _rebuild_hourly(home, desde or inicio)
    insuficientes = hogares_sin_datos_suficientes(homes)
    resumen = {
        'fecha': inicio.isoformat(),
        'duplicados_eliminados': sum(duplicados.values()),
        'duplicados_por_metrica': duplicados,
        'invalidas_marcadas': sum(invalidas.values()),
        'invalidas_por_metrica': invalidas,
        'invalidas_total': SensorReading.objects.filter(is_valid=False).count(),
        'datos_insuficientes': insuficientes,
        'avisos': [item['mensaje'] for item in insuficientes],
    }
    MonitorState.objects.update_or_create(key=CLAVE_RESUMEN, defaults={
        'last_started_at': inicio, 'last_finished_at': timezone.now(), 'last_status': 'ok', 'last_summary': resumen,
    })
    if resumen['duplicados_eliminados'] or resumen['invalidas_marcadas']:
        logger.warning('Validación de datos: %s duplicados borrados, %s lecturas inválidas',
                       resumen['duplicados_eliminados'], resumen['invalidas_marcadas'])
    return resumen


def ultimo_resumen():
    fila = MonitorState.objects.filter(key=CLAVE_RESUMEN).first()
    return fila.last_summary if fila else None


def invalidas_recientes(limite=20):
    """Últimas lecturas marcadas como inválidas (para revisarlas en la página)."""
    filas = SensorReading.objects.filter(is_valid=False).select_related('home', 'zone').order_by('-ts')[:limite]
    return [{
        'nic': lectura.home.etiqueta if lectura.home_id else (lectura.zone.name if lectura.zone_id else ''),
        'metrica': lectura.metric, 'valor': lectura.value, 'fecha': lectura.ts.isoformat(),
        'motivo': lectura.motivo_invalido,
    } for lectura in filas]
