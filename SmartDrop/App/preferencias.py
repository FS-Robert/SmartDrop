"""Preferencias del usuario (notificaciones, modo oscuro, idioma y reportes semanales).

La misma lógica la usan la web y la app móvil: las preferencias se guardan en el backend con el
id_usuario de Supabase, así que un cambio en un lado se ve en el otro.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from . import supabase_client
from .models import PreferenciasUsuario
from .queries import consumption_date, owned_consumption, owner_id, safe_float

logger = logging.getLogger(__name__)

BOOLEAN_FIELDS = ('alertas_nivel', 'suministro', 'calidad', 'consumo_elevado', 'modo_oscuro', 'reportes_semanales')
IDIOMAS = ('es', 'en')
WEEKLY_REPORT_DAYS = 7
WEEKLY_REPORT_ORIGIN = 'reporte_semanal'

# Categoría de notificación de cada tipo de alerta (por subcadena del tipo). Los tipos sin categoría
# (p. ej. fugas o el reporte semanal) se muestran siempre.
ALERT_CATEGORIES = (
    ('nivel', 'alertas_nivel'),
    ('tanque', 'alertas_nivel'),
    ('presion', 'suministro'),
    ('desabasto', 'suministro'),
    ('sin_agua', 'suministro'),
    ('suministro', 'suministro'),
    ('calidad', 'calidad'),
    ('tds', 'calidad'),
    ('consumo', 'consumo_elevado'),
)
# Parámetro de sensor -> categoría (alertas de valores fuera de rango).
SENSOR_CATEGORIES = {'nivel': 'alertas_nivel', 'presion': 'suministro', 'flujo': 'suministro', 'calidad': 'calidad'}


def preferencias_de(user_or_id):
    """PreferenciasUsuario del usuario (web, JWT de la app o id); se crea con los valores por defecto."""
    user_id = user_or_id if isinstance(user_or_id, int) else owner_id(user_or_id)
    prefs, _ = PreferenciasUsuario.objects.get_or_create(id_usuario=user_id)
    return prefs


def como_dict(prefs):
    return {
        **{field: getattr(prefs, field) for field in BOOLEAN_FIELDS},
        'idioma': prefs.idioma,
    }


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('true', '1', 'si', 'sí', 'on', 'yes'):
        return True
    if text in ('false', '0', 'no', 'off'):
        return False
    raise ValueError(value)


def actualizar(user, data):
    """Aplica los campos válidos de `data`. Devuelve (prefs, errores)."""
    prefs = preferencias_de(user)
    errores = {}
    changed = []
    for field in BOOLEAN_FIELDS:
        if field in data:
            try:
                setattr(prefs, field, _as_bool(data[field]))
                changed.append(field)
            except ValueError:
                errores[field] = 'Debe ser verdadero o falso.'
    if 'idioma' in data:
        idioma = str(data['idioma']).strip().lower()[:2]
        if idioma in IDIOMAS:
            prefs.idioma = idioma
            changed.append('idioma')
        else:
            errores['idioma'] = 'Idioma no válido.'
    if changed:
        prefs.save()
    return prefs, errores


def categoria_alerta(tipo):
    tipo = str(tipo or '').lower()
    return next((categoria for clave, categoria in ALERT_CATEGORIES if clave in tipo), None)


def alerta_habilitada(prefs, tipo):
    categoria = categoria_alerta(tipo)
    return categoria is None or getattr(prefs, categoria)


def filtrar_alertas(alertas, prefs):
    """Quita las alertas de las categorías que el usuario desactivó."""
    return [alerta for alerta in alertas if alerta_habilitada(prefs, alerta.get('tipo_alerta'))]


def sensor_habilitado(prefs, parametro):
    categoria = SENSOR_CATEGORIES.get(parametro)
    return categoria is None or getattr(prefs, categoria)


# ── Reporte semanal ──────────────────────────────────────────────────────────

def _resumen_semanal(user):
    """Consumo de los últimos 7 días frente a los 7 anteriores (litros)."""
    today = timezone.localdate()
    since = today - timedelta(days=2 * WEEKLY_REPORT_DAYS - 1)
    _, rows = owned_consumption(user, since, today)
    actual = anterior = 0.0
    inicio_semana = today - timedelta(days=WEEKLY_REPORT_DAYS - 1)
    for row in rows:
        try:
            day = consumption_date(row['fecha'])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        value = safe_float(row.get('consumo_total') or row.get('consumo_promedio'))
        if inicio_semana <= day <= today:
            actual += value
        elif since <= day < inicio_semana:
            anterior += value
    variacion = round((actual - anterior) / anterior * 100, 1) if anterior else None
    if variacion is None:
        tendencia = 'Primera semana con datos de consumo.' if actual else 'No se registró consumo esta semana.'
    elif variacion > 0:
        tendencia = f'Consumiste {variacion}% más que la semana anterior.'
    elif variacion < 0:
        tendencia = f'Consumiste {abs(variacion)}% menos que la semana anterior. ¡Buen ahorro!'
    else:
        tendencia = 'Tu consumo fue igual al de la semana anterior.'
    return {
        'desde': inicio_semana.isoformat(),
        'hasta': today.isoformat(),
        'total_litros': round(actual, 1),
        'semana_anterior_litros': round(anterior, 1),
        'variacion_porcentual': variacion,
        'mensaje': f'Resumen semanal: {round(actual, 1)} L consumidos del {inicio_semana:%d/%m} al {today:%d/%m}. {tendencia}',
    }


def reporte_semanal(user, prefs=None):
    """Genera (como mucho una vez por semana) el reporte semanal del usuario si lo tiene activado.

    Se registra como alerta + notificación en Supabase para que aparezca en el historial y llegue
    como aviso a la app. Devuelve los datos del último reporte, o None si está desactivado.
    """
    prefs = prefs or preferencias_de(user)
    if not prefs.reportes_semanales:
        return None
    today = timezone.localdate()
    if prefs.ultimo_reporte_semanal and today - prefs.ultimo_reporte_semanal < timedelta(days=WEEKLY_REPORT_DAYS):
        return prefs.ultimo_reporte_datos
    try:
        datos = _resumen_semanal(user)
        user_id = owner_id(user)
        alerta = supabase_client.insert('alerta', {
            'tipo_alerta': WEEKLY_REPORT_ORIGIN,
            'prioridad': 'baja',
            'mensaje': datos['mensaje'],
            'estado_confirmacion': 'confirmada',
            'fecha_creacion': timezone.now().isoformat(),
            'datos_adicionales': {'origen': WEEKLY_REPORT_ORIGIN, 'id_usuario': user_id, **datos},
        }) or {}
        if alerta.get('id_alerta'):
            supabase_client.insert('notificacion', {
                'id_alerta': alerta['id_alerta'],
                'id_usario_destino': user_id,
                'canal_envio': 'dashboard',
                'estado_visualizacion': 'no_leida',
                'fecha_envio': timezone.now().isoformat(),
            })
    except Exception:
        logger.warning('No se pudo generar el reporte semanal del usuario %s', owner_id(user), exc_info=True)
        return prefs.ultimo_reporte_datos
    prefs.ultimo_reporte_semanal = today
    prefs.ultimo_reporte_datos = datos
    prefs.save(update_fields=['ultimo_reporte_semanal', 'ultimo_reporte_datos', 'actualizado'])
    return datos


def es_reporte_ajeno(alerta, user_id):
    """Los reportes semanales son personales: cada usuario solo ve los suyos."""
    extra = alerta.get('datos_adicionales') or {}
    return extra.get('origen') == WEEKLY_REPORT_ORIGIN and extra.get('id_usuario') != user_id
