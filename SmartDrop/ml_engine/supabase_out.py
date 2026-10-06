"""Escritura de resultados del ML hacia Supabase (tablas prediccion_desabasto, fuga, alerta, notificacion)."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from App import supabase_client

logger = logging.getLogger(__name__)

SHORTAGE_MODEL_NAME = 'lightgbm_cuantilico'


def admin_user_ids():
    rows = supabase_client.select('usuario', 'id_usuario', {'id_rol': 'eq.2', 'limit': '200'})
    return [row['id_usuario'] for row in rows]


def save_shortage_prediction(zone, prediction):
    """Guarda la predicción de desabasto en `prediccion_desabasto` (la anterior queda como 'reemplazada')."""
    if not zone.source_ref_tanque_id:
        return None
    details = prediction.details or {}
    # Con riesgo bajo la mediana viene de un puñado de trayectorias extremas: no es una autonomía real.
    median = prediction.median_hours_to_shortage if prediction.nivel_riesgo != 'bajo' else None
    horizon = prediction.horizonte_horas
    spread = None
    if prediction.p10_hours_to_shortage is not None and prediction.p90_hours_to_shortage is not None:
        spread = (prediction.p90_hours_to_shortage - prediction.p10_hours_to_shortage) / max(horizon, 1)
    confidence = round(max(0.0, min(1.0, 1.0 - spread)), 3) if spread is not None else 0.9
    level = float(details.get('nivel_actual_litros', zone.nivel_actual_litros))
    now = timezone.now()

    supabase_client.update(
        'prediccion_desabasto', {'estado': 'reemplazada'},
        {'id_tanque': f'eq.{zone.source_ref_tanque_id}', 'estado': 'eq.activa'},
    )
    row = supabase_client.insert('prediccion_desabasto', {
        'id_tanque': zone.source_ref_tanque_id,
        'fecha_prediccion': now.isoformat(),
        'nivel_actual_litros': round(level, 3),
        'fecha_desabasto_estimada': (now + timedelta(hours=median)).isoformat() if median is not None else None,
        'porcentaje_llenado': round(100.0 * level / zone.capacidad_maxima_litros, 2) if zone.capacidad_maxima_litros else 0,
        'consumo_promedio_ldia': round(float(details.get('consumo_p50_lph', 0)) * 24, 3),
        'velocidad_vaciado_actual_1h': details.get('vaciado_ultima_hora_lph'),
        'horas_autonomia_estimadas': round(median if median is not None else float(horizon), 2),
        'confianza_prediccion': confidence,
        'modelo_usado': SHORTAGE_MODEL_NAME,
        'datos_entrada': details,
        'estado': 'activa',
        'nivel_riesgo': prediction.nivel_riesgo,
        'valor_probabilidad': round(prediction.probabilidad_desabasto_horizonte, 4),
    })
    return row
