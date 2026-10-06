"""Aviso automático de posibles fugas: registra la fuga, la alerta y la notificación para cada administrador."""
from __future__ import annotations

import logging
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from App import supabase_client
from ml_engine.models import LeakPrediction
from ml_engine.supabase_out import admin_user_ids

logger = logging.getLogger(__name__)

ORIGIN = 'ml_engine'


def cooldown_hours():
    return float(getattr(settings, 'ML_LEAK_ALERT_COOLDOWN_HOURS', 12))


def should_alert(home, now=None):
    """No repite el aviso de un mismo hogar mientras dure el periodo de enfriamiento."""
    now = now or timezone.now()
    return not LeakPrediction.objects.filter(
        home=home, alert_sent_at__gte=now - timedelta(hours=cooldown_hours()),
    ).exists()


def _fmt(value, unit='', digits=2):
    return 'sin dato' if value is None else f'{value:.{digits}f}{(" " + unit) if unit else ""}'


def build_message(home, prediction):
    meta, f = home.meta or {}, prediction.features or {}
    unit = f.get('unidad_presion') or ''
    onset = f.get('inicio_estimado')
    onset_text = 'no determinado'
    if onset:
        from datetime import datetime
        onset_text = timezone.localtime(datetime.fromisoformat(onset)).strftime('%Y-%m-%d %H:%M')
    lines = [
        f"POSIBLE FUGA en {meta.get('nic', home.etiqueta)} — {meta.get('direccion', 'sin dirección')} ({meta.get('zona', 'sin zona')})",
        f"Probabilidad estimada: {prediction.porcentaje:.0f} % (riesgo {prediction.nivel_riesgo}).",
        f"• Flujo mínimo en las últimas 6 h: {_fmt(f.get('flujo_minimo_6h_lpm'), 'L/min', 3)} "
        f"(normal: {_fmt(f.get('flujo_minimo_normal_lpm'), 'L/min', 3)}) → pérdida estimada ≈ {_fmt(f.get('perdida_estimada_lph'), 'L/h', 1)}.",
        f"• Inicio estimado: {onset_text} · agua perdida estimada: {_fmt(f.get('litros_perdidos_estimados'), 'L', 1)}.",
        f"• Presión: {_fmt(f.get('presion_actual'), unit)} (normal: {_fmt(f.get('presion_normal'), unit)}, caída: {_fmt(f.get('caida_presion'), unit)}).",
        f"• Tanque: {_fmt(f.get('nivel_tanque_litros'), 'L', 1)} ({_fmt(f.get('nivel_tanque_pct'), '%', 0)} de {_fmt(f.get('capacidad_tanque_litros'), 'L', 1)}).",
        f"• Titular: {meta.get('titular') or 'sin dato'} · Tel.: {meta.get('telefono') or 'sin dato'}.",
    ]
    if prediction.drivers:
        lines.append('Causas detectadas: ' + ' | '.join(prediction.drivers))
    lines.append('Acción sugerida: verificar tuberías y llaves de la vivienda; si el flujo persiste con el uso en cero, cerrar la válvula.')
    return '\n'.join(lines)


def _already_open(sensor_flow_id, meta, since):
    """¿Hay una fuga pendiente reciente de esta vivienda? Los sensores se comparten, así que se
    identifica la vivienda por su dirección y zona además del sensor de flujo."""
    if not sensor_flow_id:
        return False
    params = {
        'id_sensor_flujo': f'eq.{sensor_flow_id}', 'estado': 'eq.pendiente',
        'fecha_deteccion': f"gte.{since.strftime('%Y-%m-%dT%H:%M:%S')}", 'limit': '1',
    }
    if meta.get('direccion'):
        params['ubicacion_estimada'] = f"eq.{meta['direccion']}"
    if meta.get('zona'):
        params['zona'] = f"eq.{meta['zona']}"
    rows = supabase_client.select('fuga', 'id_fuga', params)
    return bool(rows)


def raise_leak_alert(home, prediction):
    """Crea fuga + alerta + notificaciones a los admins. Devuelve True si se avisó."""
    meta = home.meta or {}
    sensors = meta.get('sensores', {})
    features = prediction.features or {}
    now = timezone.now()
    try:
        if _already_open(sensors.get('flujo'), meta, now - timedelta(hours=cooldown_hours())):
            prediction.alert_sent_at = now
            prediction.save(update_fields=['alert_sent_at'])
            return False

        fuga = supabase_client.insert('fuga', {
            'fecha_deteccion': now.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'ubicacion_estimada': meta.get('direccion'),
            'zona': meta.get('zona'),
            'id_sensor_presion': sensors.get('presion'),
            'id_sensor_flujo': sensors.get('flujo'),
            'caida_presion': features.get('caida_presion'),
            'flujo_anomalo': features.get('flujo_exceso_lpm'),
            'tipo_fuga': 'posible_fuga',
            'metodo_deteccion': 'ml_hibrido',
            'estado': 'pendiente',
        }) or {}
        alerta = supabase_client.insert('alerta', {
            'tipo_alerta': 'fuga',
            'prioridad': 'critica' if prediction.probabilidad >= 0.85 else 'alta',
            'mensaje': build_message(home, prediction),
            'estado_confirmacion': 'pendiente',
            'id_fuga': fuga.get('id_fuga'),
            'fecha_creacion': now.isoformat(),
            'datos_adicionales': {
                'origen': ORIGIN,
                'tipo': 'prediccion_fuga',
                'probabilidad': round(prediction.probabilidad, 4),
                'nivel_riesgo': prediction.nivel_riesgo,
                'metodo': prediction.method,
                'vivienda': {
                    'id_vivienda': home.source_ref_vivienda_id, 'nic': meta.get('nic'),
                    'direccion': meta.get('direccion'), 'zona': meta.get('zona'),
                    'titular': meta.get('titular'), 'telefono': meta.get('telefono'),
                },
                'sensores': sensors,
                'metricas': features,
                'causas': prediction.drivers,
            },
        }) or {}
        for user_id in admin_user_ids():
            supabase_client.insert('notificacion', {
                'id_alerta': alerta['id_alerta'],
                'id_usario_destino': user_id,
                'canal_envio': 'dashboard',
                'estado_visualizacion': 'no_leida',
                'fecha_envio': now.isoformat(),
            })
    except Exception:
        logger.exception('No se pudo registrar la alerta de fuga del hogar %s', home.pk)
        return False

    prediction.fuga_id = fuga.get('id_fuga')
    prediction.alerta_id = alerta.get('id_alerta')
    prediction.alert_sent_at = now
    prediction.save(update_fields=['fuga_id', 'alerta_id', 'alert_sent_at'])
    logger.warning('Alerta de fuga creada: home=%s prob=%.2f alerta=%s', home.pk, prediction.probabilidad, prediction.alerta_id)
    return True
