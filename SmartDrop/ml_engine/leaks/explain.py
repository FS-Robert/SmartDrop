"""Textos en lenguaje sencillo para explicar una posible fuga (web, app móvil y avisos).

Todo se arma a partir de las métricas guardadas con la predicción (`features` / `metricas`), así que sirve
tanto para predicciones nuevas como para las ya registradas.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from django.utils import timezone

ACTION = 'Revisar tuberías, llaves y sanitarios de la vivienda. Si el agua sigue corriendo sin uso, cerrar la válvula.'


def _num(metrics, key):
    value = (metrics or {}).get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _local(iso):
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, dt_timezone.utc)
    return timezone.localtime(moment)


def onset_text(metrics):
    """'05/10 12:00' o None si no se pudo estimar el inicio."""
    moment = _local((metrics or {}).get('inicio_estimado'))
    return moment.strftime('%d/%m %H:%M') if moment else None


def plain_summary(metrics):
    """Una frase que explica qué está pasando, p. ej. 'El agua sigue corriendo…: pérdida estimada ≈ 13.2 L por hora…'."""
    loss = _num(metrics, 'perdida_estimada_lph')
    lost = _num(metrics, 'litros_perdidos_estimados')
    since = onset_text(metrics)
    if loss is not None and loss > 0:
        text = f'El agua sigue corriendo aunque no haya consumo: pérdida estimada ≈ {loss:.1f} L por hora'
        if since:
            text += f' desde el {since}'
        if lost:
            text += f' (≈ {lost:.0f} L en total)'
        return text + '.'
    drop = _num(metrics, 'caida_presion')
    if drop is not None and drop > 0:
        return f'La presión bajó {drop:.2f} respecto a lo normal; puede haber una fuga en la tubería.'
    return 'El consumo de la vivienda se comporta distinto a lo habitual.'


def describe_causes(metrics):
    """Lista corta de causas en palabras simples, sin términos técnicos."""
    metrics = metrics or {}
    signals = metrics.get('senales') or {}
    causes = []
    floor_now, floor_normal = _num(metrics, 'flujo_minimo_6h_lpm'), _num(metrics, 'flujo_minimo_normal_lpm')
    if signals.get('floor', 0) >= 0.3 and floor_now is not None:
        normal = f' (lo normal es {floor_normal:.2f})' if floor_normal is not None else ''
        causes.append(f'En las últimas 6 h el flujo nunca bajó de {floor_now:.2f} L/min{normal}: el agua no deja de correr.')
    pressure_now, pressure_normal = _num(metrics, 'presion_actual'), _num(metrics, 'presion_normal')
    if signals.get('pressure', 0) >= 0.3 and pressure_now is not None and pressure_normal is not None:
        causes.append(f'La presión bajó a {pressure_now:.2f} (lo normal es {pressure_normal:.2f}).')
    if signals.get('flow', 0) >= 0.3:
        recent = _num(metrics, 'flujo_reciente_lpm')
        amount = f' ({recent:.2f} L/min)' if recent is not None else ''
        causes.append(f'Se está usando más agua de lo habitual para esta hora{amount}.')
    if signals.get('iforest', 0) >= 0.5:
        causes.append('El consumo reciente no se parece al patrón habitual de la vivienda.')
    return causes


def short_message(meta, metrics, porcentaje):
    """Mensaje breve que se guarda en la alerta de Supabase (qué pasa, a quién llamar y qué hacer)."""
    meta = meta or {}
    place = ', '.join(part for part in (meta.get('direccion'), meta.get('zona')) if part)
    head = f"Posible fuga en {meta.get('nic') or 'una vivienda'}" + (f' — {place}.' if place else '.')
    contact = f"Contacto: {meta.get('titular') or 'sin dato'} · Tel. {meta.get('telefono') or 'sin dato'}."
    return '\n'.join([
        head,
        f'Probabilidad estimada: {porcentaje:.0f} %. {plain_summary(metrics)}',
        contact,
        f'Qué hacer: {ACTION}',
    ])
