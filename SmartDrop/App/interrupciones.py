"""Cortes de agua reportados por los usuarios (PB042).

Un reporte de tipo "No llega agua" es un corte del servicio. Aquí se calcula:
- en qué zona fue (la que escribió el usuario o la de su vivienda),
- cuánto duró (desde que se reportó hasta que el administrador anotó que volvió el agua),
- y un resumen con el promedio de duración y la zona con más cortes.

Solo lee reportes y viviendas: no toca sensores, lecturas ni la IA.
"""

import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import supabase_client
from .models import InterrupcionServicio

logger = logging.getLogger(__name__)

TIPO_CORTE = 'sin_agua'
SIN_ZONA = 'Sin zona'
_ZONA_EN_TEXTO = re.compile(r'^\s*\[zona:\s*([^\]]+)\]', re.IGNORECASE)
# Margen para que la hora del navegador y la del servidor no choquen.
_MARGEN_FUTURO = timedelta(minutes=5)


def _fecha(valor):
    """Texto ISO de Supabase -> fecha con zona horaria (o None)."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        fecha = valor
    else:
        try:
            fecha = parse_datetime(str(valor).strip())
        except ValueError:
            return None
    if fecha and timezone.is_naive(fecha):
        fecha = timezone.make_aware(fecha)
    return fecha


def zona_de_texto(ubicacion):
    """'[zona: Norte] calle 3' -> 'Norte'. None si el texto no trae zona."""
    encontrada = _ZONA_EN_TEXTO.match(ubicacion or '')
    return encontrada.group(1).strip() if encontrada else None


def zonas_por_usuario():
    """{id_usuario: zona} según la vivienda de cada usuario. Si algo falla, devuelve {}."""
    try:
        from ml_engine.models import Home

        zona_vivienda = {
            h.source_ref_vivienda_id: (h.meta or {}).get('zona') or h.zone.name
            for h in Home.objects.filter(source='real', source_ref_vivienda_id__isnull=False).select_related('zone')
        }
        viviendas = supabase_client.select(
            'vivienda', 'id_vivienda,id_usuario_propietario', {'limit': '2000'},
        )
    except Exception:  # sin zona no se rompe la pantalla, solo sale "Sin zona"
        logger.exception('No se pudieron leer las zonas de las viviendas')
        return {}
    zonas = {}
    for vivienda in viviendas:
        dueno, zona = vivienda.get('id_usuario_propietario'), zona_vivienda.get(vivienda.get('id_vivienda'))
        if dueno and zona and dueno not in zonas:
            zonas[dueno] = zona
    return zonas


def zona_del_reporte(reporte, zonas_usuario):
    return zona_de_texto(reporte.get('ubicacion')) or zonas_usuario.get(reporte.get('id_usuario')) or SIN_ZONA


def restablecidos(ids_reporte):
    """{id_reporte: hora en que volvió el agua} de los reportes indicados."""
    ids = [i for i in ids_reporte if i is not None]
    if not ids:
        return {}
    return dict(InterrupcionServicio.objects.filter(id_reporte__in=ids).values_list('id_reporte', 'restablecido'))


def duracion(reporte, restablecido):
    """(segundos, aproximada) del corte, o (None, False) si el agua aún no vuelve.

    Si el administrador no anotó la hora en que volvió el agua pero el reporte está resuelto,
    se usa la fecha en que lo atendió y se marca como aproximada.
    """
    inicio = _fecha(reporte.get('fecha_reporte'))
    if not inicio:
        return None, False
    fin, aproximada = restablecido, False
    if fin is None and reporte.get('estado') == 'resuelto':
        fin, aproximada = _fecha(reporte.get('fecha_atencion')), True
    if fin is None or fin < inicio:
        return None, False
    return int((fin - inicio).total_seconds()), aproximada


def completar(reportes, zonas_usuario=None):
    """Agrega 'zona', 'es_corte', 'restablecido', 'duracion_seg' y 'duracion_aprox' a cada reporte."""
    if zonas_usuario is None:
        zonas_usuario = zonas_por_usuario()
    horas = restablecidos(r.get('id_reporte') for r in reportes if r.get('tipo_problema') == TIPO_CORTE)
    for reporte in reportes:
        reporte['zona'] = zona_del_reporte(reporte, zonas_usuario)
        reporte['es_corte'] = reporte.get('tipo_problema') == TIPO_CORTE
        reporte['restablecido'] = horas.get(reporte.get('id_reporte'))
        if reporte['es_corte']:
            reporte['duracion_seg'], reporte['duracion_aprox'] = duracion(reporte, reporte['restablecido'])
        else:
            reporte['duracion_seg'], reporte['duracion_aprox'] = None, False
    return reportes


def resumen(reportes):
    """Resumen de los cortes de la lista (ya completada con `completar`)."""
    cortes = [r for r in reportes if r.get('es_corte')]
    duraciones = [r['duracion_seg'] for r in cortes if r.get('duracion_seg') is not None]
    por_zona = Counter(r['zona'] for r in cortes if r.get('zona') != SIN_ZONA)
    zona, veces = por_zona.most_common(1)[0] if por_zona else (None, 0)
    return {
        'total': len(cortes),
        # Sin agua todavía: no tiene hora de regreso y no está resuelto.
        'en_curso': sum(1 for r in cortes if r.get('duracion_seg') is None and r.get('estado') != 'resuelto'),
        'con_duracion': len(duraciones),
        'promedio_seg': int(sum(duraciones) / len(duraciones)) if duraciones else None,
        'mas_largo_seg': max(duraciones) if duraciones else None,
        'zona_mas_afectada': zona,
        'cortes_zona_mas_afectada': veces,
    }


def leer_hora(texto, reporte, ahora=None):
    """Valida la hora de 'volvió el agua' del formulario. Devuelve (fecha, error)."""
    texto = (texto or '').strip()[:20]
    if not texto:
        return None, None
    try:
        fecha = timezone.make_aware(datetime.strptime(texto, '%Y-%m-%dT%H:%M'))
    except ValueError:
        return None, 'La hora en que volvió el agua no es válida.'
    ahora = ahora or timezone.now()
    if fecha > ahora + _MARGEN_FUTURO:
        return None, 'La hora en que volvió el agua no puede estar en el futuro.'
    inicio = _fecha(reporte.get('fecha_reporte'))
    if inicio and fecha < inicio.replace(second=0, microsecond=0):
        return None, 'La hora en que volvió el agua no puede ser antes del reporte.'
    return fecha, None


def guardar(id_reporte, fecha, id_usuario=None):
    InterrupcionServicio.objects.update_or_create(
        id_reporte=int(id_reporte), defaults={'restablecido': fecha, 'id_usuario': id_usuario},
    )
