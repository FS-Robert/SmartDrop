"""Avisos de fuga a los vecinos del sector y a los admins, al instante (PB027).

Cuando el algoritmo confirma una posible fuga, cada residente de esa zona recibe "Fuga detectada en su sector":
queda guardado en Supabase (alerta + notificación, para "Alertas activas" de la web y para la revisión de la
app) y se manda en ese mismo momento por WebSocket a la web y a la app abiertas, que lo muestran como
notificación. El aviso al vecino no dice qué casa es ni datos de contacto: eso solo lo ven los admins.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from App import supabase_client

logger = logging.getLogger(__name__)

ORIGEN_SECTOR = 'sector'
TITULO_SECTOR = 'Fuga detectada en su sector'
GRUPO_ADMINS = 'sensor-readings-admin'
HORAS_ACTIVA = 48


def _enviar(grupo, payload):
    """Manda un evento por WebSocket; si no hay canal disponible no pasa nada."""
    try:
        canal = get_channel_layer()
        if canal is not None:
            async_to_sync(canal.group_send)(grupo, {'type': 'sensor.reading', 'payload': payload})
    except Exception:
        logger.exception('No se pudo enviar el aviso en vivo al grupo %s', grupo)


def avisar_admins_en_vivo(datos):
    """La campana de la web y la app del admin se enteran sin esperar a la siguiente revisión."""
    _enviar(GRUPO_ADMINS, {'event': 'leak_alert', 'para': 'admin', **datos})


def mensaje_sector(zona):
    lugar = f' ({zona})' if zona else ''
    return (f'{TITULO_SECTOR}{lugar}.\nEl equipo de SmartDrop ya está revisando. Mientras tanto, revisa que tus '
            'llaves estén cerradas y avisa si ves agua corriendo en la calle.')


def avisar_sector(zona, id_alerta_origen=None, motivo='Posible Fuga', momento=None):
    """Guarda y envía "Fuga detectada en su sector" a cada residente de la zona. Devuelve un resumen."""
    inicio = time.monotonic()
    if not zona:
        return {'avisados': 0, 'segundos': 0.0}
    momento = momento or timezone.now()
    viviendas = supabase_client.select('vivienda', 'id_vivienda,id_usuario_propietario', {
        'zona': f'eq.{zona}', 'limit': '1000',
    })
    duenos = sorted({v['id_usuario_propietario'] for v in viviendas if v.get('id_usuario_propietario')})
    mensaje = mensaje_sector(zona)
    alerta = {}
    if duenos:
        alerta = supabase_client.insert('alerta', {
            'tipo_alerta': 'fuga',
            'prioridad': 'alta',
            'mensaje': mensaje,
            'estado_confirmacion': 'pendiente',
            'fecha_creacion': momento.isoformat(),
            'datos_adicionales': {
                'origen': ORIGEN_SECTOR, 'tipo': 'fuga_sector', 'titulo': TITULO_SECTOR, 'zona': zona,
                'motivo': motivo, 'id_alerta_origen': id_alerta_origen,
            },
        }) or {}
        ahora = timezone.now().isoformat()
        supabase_client.insert_many('notificacion', [{
            'id_alerta': alerta.get('id_alerta'), 'id_usario_destino': dueno, 'canal_envio': 'dashboard',
            'estado_visualizacion': 'no_leida', 'fecha_envio': ahora,
        } for dueno in duenos])
    payload = {
        'event': 'leak_alert', 'para': 'residente', 'id_alerta': alerta.get('id_alerta'),
        'titulo': TITULO_SECTOR, 'mensaje': mensaje, 'zona': zona, 'prioridad': 'alta',
        'fecha': momento.isoformat(),
    }
    for vivienda in viviendas:
        _enviar(f"sensor-vivienda-{vivienda['id_vivienda']}", payload)
    segundos = round(time.monotonic() - inicio, 3)
    logger.warning('Aviso de fuga al sector %s: %s residentes en %.2f s', zona, len(duenos), segundos)
    return {'avisados': len(duenos), 'id_alerta': alerta.get('id_alerta'), 'segundos': segundos}


def alertas_activas(user_id, horas=HORAS_ACTIVA):
    """Avisos de fuga del sector de este residente que siguen pendientes (para "Alertas activas")."""
    desde = (timezone.now() - timedelta(hours=horas)).isoformat()
    notificaciones = supabase_client.select('notificacion', 'id_alerta,estado_visualizacion', {
        'id_usario_destino': f'eq.{user_id}', 'fecha_envio': f'gte.{desde}', 'order': 'fecha_envio.desc',
        'limit': '50',
    })
    ids = [n['id_alerta'] for n in notificaciones if n.get('id_alerta')]
    if not ids:
        return []
    leidas = {n['id_alerta'] for n in notificaciones if n.get('estado_visualizacion') == 'leida'}
    alertas = supabase_client.select('alerta', 'id_alerta,mensaje,prioridad,fecha_creacion,datos_adicionales', {
        'id_alerta': supabase_client.ids_in(ids), 'datos_adicionales->>origen': f'eq.{ORIGEN_SECTOR}',
        'estado_confirmacion': 'eq.pendiente', 'order': 'id_alerta.desc', 'limit': '10',
    })
    return [{
        'id_alerta': a['id_alerta'],
        'titulo': (a.get('datos_adicionales') or {}).get('titulo') or TITULO_SECTOR,
        'mensaje': (a.get('mensaje') or '').split('\n')[-1],
        'zona': (a.get('datos_adicionales') or {}).get('zona'),
        'prioridad': a.get('prioridad'),
        'fecha': a.get('fecha_creacion'),
        'leida': a['id_alerta'] in leidas,
    } for a in alertas]
