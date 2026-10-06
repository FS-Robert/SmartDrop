"""Endpoints del botón 'Realizar predicciones', la predicción de fugas y los avisos al admin.

Mismos endpoints para la web (sesión) y la app móvil (JWT): solo administradores.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from App import queries, supabase_client
from ml_engine import jobs
from ml_engine.leaks.alerts import ORIGIN, cooldown_hours
from ml_engine.leaks.detect import alert_threshold
from ml_engine.models import Home, LeakPrediction, MonitorState, PredictionJob
from ml_engine.monitor import MONITOR_KEY, interval_seconds
from ml_engine.realdata.sync import data_quality
from ml_engine.views_api import AdminOnlyAPIView

logger = logging.getLogger(__name__)

ALERT_LIMIT = 50


class PredictionRunView(AdminOnlyAPIView):
    """POST /v1/ml/predictions/run — lanza la predicción en segundo plano y devuelve el job."""

    def post(self, request):
        job, created = jobs.start_prediction_job(queries.owner_id(request.user))
        payload = jobs.serialize_job(job)
        payload['ya_en_curso'] = not created
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class PredictionJobView(AdminOnlyAPIView):
    """GET /v1/ml/predictions/jobs/{id} — estado y progreso (se consulta por polling)."""

    def get(self, request, job_id: str):
        return Response(jobs.serialize_job(get_object_or_404(PredictionJob, id=job_id)))


class PredictionStatusView(AdminOnlyAPIView):
    """GET /v1/ml/predictions/status — última ejecución manual y estado del monitor automático."""

    def get(self, request):
        last_job = PredictionJob.objects.first()
        monitor = MonitorState.objects.filter(key=MONITOR_KEY).first()
        return Response({
            'ultima_ejecucion': jobs.serialize_job(last_job) if last_job else None,
            'monitor': {
                'activo': monitor is not None,
                'ultimo_ciclo': monitor.last_finished_at if monitor else None,
                'estado': monitor.last_status if monitor else None,
                'resumen': monitor.last_summary if monitor else None,
                'cada_minutos': round(interval_seconds() / 60, 1),
            },
        })


def _leak_row(home, prediction, quality):
    meta = home.meta or {}
    return {
        'home_id': home.id,
        'nic': meta.get('nic', home.etiqueta),
        'direccion': meta.get('direccion', ''),
        'zona': meta.get('zona', ''),
        'titular': meta.get('titular', ''),
        'telefono': meta.get('telefono', ''),
        'calidad_datos': quality['status'],
        'detalle_datos': quality.get('detail', ''),
        'probabilidad': None if prediction is None else round(prediction.probabilidad, 4),
        'porcentaje': None if prediction is None else prediction.porcentaje,
        'nivel_riesgo': 'sin_datos' if prediction is None else prediction.nivel_riesgo,
        'posible_fuga': prediction is not None and prediction.probabilidad >= alert_threshold(),
        'causas': [] if prediction is None else prediction.drivers,
        'metricas': {} if prediction is None else prediction.features,
        'evaluado': None if prediction is None else prediction.generated_at,
        'alerta_id': None if prediction is None else prediction.alerta_id,
    }


class LeakOverviewView(AdminOnlyAPIView):
    """GET /v1/ml/leaks — última predicción de fuga por vivienda (posibles fugas primero)."""

    def get(self, request):
        latest = {}
        for prediction in LeakPrediction.objects.select_related('home').order_by('generated_at'):
            latest[prediction.home_id] = prediction
        rows = []
        for home in Home.objects.filter(activo=True, source='real').select_related('zone').order_by('etiqueta'):
            rows.append(_leak_row(home, latest.get(home.id), data_quality(home)))
        rows.sort(key=lambda r: (-(r['probabilidad'] if r['probabilidad'] is not None else -1), r['nic']))
        monitor = MonitorState.objects.filter(key=MONITOR_KEY).first()
        return Response({
            'umbral_alerta': alert_threshold(),
            'enfriamiento_horas': cooldown_hours(),
            'posibles_fugas': sum(1 for r in rows if r['posible_fuga']),
            'monitor': {
                'ultimo_ciclo': monitor.last_finished_at if monitor else None,
                'estado': monitor.last_status if monitor else None,
                'cada_minutos': round(interval_seconds() / 60, 1),
            },
            'viviendas': rows,
        })


def _alert_payload(alerta, notificacion):
    extra = alerta.get('datos_adicionales') or {}
    vivienda = extra.get('vivienda') or {}
    return {
        'id_alerta': alerta['id_alerta'],
        'id_notificacion': notificacion.get('id_notificacion') if notificacion else None,
        'fecha': alerta.get('fecha_creacion'),
        'prioridad': alerta.get('prioridad'),
        'mensaje': alerta.get('mensaje'),
        'nic': vivienda.get('nic'),
        'direccion': vivienda.get('direccion'),
        'zona': vivienda.get('zona'),
        'titular': vivienda.get('titular'),
        'telefono': vivienda.get('telefono'),
        'probabilidad': extra.get('probabilidad'),
        'nivel_riesgo': extra.get('nivel_riesgo'),
        'metricas': extra.get('metricas') or {},
        'causas': extra.get('causas') or [],
        'leida': bool(notificacion) and notificacion.get('estado_visualizacion') == 'leida',
    }


class LeakAlertsView(AdminOnlyAPIView):
    """GET /v1/ml/leaks/alerts — avisos de fuga del motor para este admin (con contador de no leídos).

    `?solo_no_leidas=1` limita a los no leídos; `?desde_id=N` devuelve solo alertas con id mayor (para polling).
    """

    def get(self, request):
        user_id = queries.owner_id(request.user)
        params = {
            'tipo_alerta': 'eq.fuga', 'datos_adicionales->>origen': f'eq.{ORIGIN}',
            'order': 'id_alerta.desc', 'limit': str(ALERT_LIMIT),
        }
        if request.query_params.get('desde_id', '').isdigit():
            params['id_alerta'] = f"gt.{request.query_params['desde_id']}"
        try:
            alertas = supabase_client.select('alerta', '*', params)
            notificaciones = {}
            if alertas:
                ids = ','.join(str(a['id_alerta']) for a in alertas)
                rows = supabase_client.select('notificacion', '*', {
                    'id_usario_destino': f'eq.{user_id}', 'id_alerta': f'in.({ids})', 'limit': '500',
                })
                notificaciones = {row['id_alerta']: row for row in rows}
        except supabase_client.SupabaseError:
            logger.exception('No se pudieron leer las alertas de fuga')
            return Response({'error': 'No se pudieron cargar las alertas.'}, status=status.HTTP_502_BAD_GATEWAY)

        items = [_alert_payload(a, notificaciones.get(a['id_alerta'])) for a in alertas]
        if request.query_params.get('solo_no_leidas') == '1':
            items = [item for item in items if not item['leida']]
        return Response({
            'no_leidas': sum(1 for item in items if not item['leida']),
            'alertas': items,
        })


class LeakAlertsReadView(AdminOnlyAPIView):
    """POST /v1/ml/leaks/alerts/read — marca como leídas (body: {"ids": [..]} o {"todas": true})."""

    def post(self, request):
        user_id = queries.owner_id(request.user)
        alert_ids = [int(i) for i in request.data.get('ids', []) if str(i).isdigit()]
        if not alert_ids and not request.data.get('todas'):
            return Response({'error': 'Indica ids o todas=true.'}, status=status.HTTP_400_BAD_REQUEST)
        now = timezone.now().isoformat()
        try:
            if not alert_ids:
                alert_ids = [row['id_alerta'] for row in supabase_client.select('alerta', 'id_alerta', {
                    'tipo_alerta': 'eq.fuga', 'datos_adicionales->>origen': f'eq.{ORIGIN}',
                    'order': 'id_alerta.desc', 'limit': str(ALERT_LIMIT),
                })]
            if not alert_ids:
                return Response({'ok': True})
            mine = {'id_usario_destino': f'eq.{user_id}', 'id_alerta': f"in.({','.join(map(str, alert_ids))})"}
            supabase_client.update(
                'notificacion', {'estado_visualizacion': 'leida', 'fecha_leida': now},
                {**mine, 'estado_visualizacion': 'eq.no_leida'},
            )
            # Un admin dado de alta después del aviso no tiene notificación propia: se le crea ya leída.
            existing = {row['id_alerta'] for row in supabase_client.select('notificacion', 'id_alerta', {**mine, 'limit': '500'})}
            supabase_client.insert_many('notificacion', [{
                'id_alerta': alert_id, 'id_usario_destino': user_id, 'canal_envio': 'dashboard',
                'estado_visualizacion': 'leida', 'fecha_envio': now, 'fecha_leida': now,
            } for alert_id in alert_ids if alert_id not in existing])
        except supabase_client.SupabaseError:
            logger.exception('No se pudieron marcar como leídas las alertas de fuga')
            return Response({'error': 'No se pudieron marcar como leídas.'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'ok': True})
