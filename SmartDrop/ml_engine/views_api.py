"""API admin-only del motor de predicción (Sección 9 del spec).

Todos los endpoints requieren rol administrador (id_rol == 2). Se acepta
tanto JWT (app móvil / futuro) como sesión Django (dashboard web), ya que
el resultado debe ser visible ÚNICAMENTE para admins en ambos frontends.
"""
from __future__ import annotations

from django.db.models import Sum
from django.utils import timezone as django_tz
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from App.api.authentication import SupabaseJWTAuthentication
from App.api.permissions import IsAdministrator

from ml_engine.leaks.detect import alert_threshold
from ml_engine.models import (
    AnomalyEvent,
    ConsumptionAggregate,
    ConsumptionForecast,
    Home,
    LeakPrediction,
    ShortagePrediction,
    TankTrajectory,
    Zone,
)
from ml_engine.realdata.sync import data_quality
from ml_engine.serializers import (
    AnomalyEventSerializer,
    ConsumptionForecastSerializer,
    ShortagePredictionSerializer,
    TankTrajectorySerializer,
    ZoneSummarySerializer,
)


class AdminOnlyAPIView(APIView):
    authentication_classes = [SupabaseJWTAuthentication, SessionAuthentication]
    permission_classes = [IsAdministrator]


class HomeConsumptionForecastView(AdminOnlyAPIView):
    """GET /v1/ml/homes/{id}/consumption-forecast"""

    def get(self, request, home_id: int):
        get_object_or_404(Home, id=home_id)
        forecasts = ConsumptionForecast.objects.filter(home_id=home_id).order_by('-target_ts')[:48]
        return Response({
            'home_id': home_id,
            'forecasts': ConsumptionForecastSerializer(forecasts, many=True).data,
        })


class ZoneTankTrajectoryView(AdminOnlyAPIView):
    """GET /v1/ml/zones/{id}/tank-trajectory"""

    def get(self, request, zone_id: int):
        get_object_or_404(Zone, id=zone_id)
        trajectory = TankTrajectory.objects.filter(zone_id=zone_id).order_by('target_ts')
        return Response({
            'zone_id': zone_id,
            'trajectory': TankTrajectorySerializer(trajectory, many=True).data,
        })


class ZoneConsumptionForecastView(AdminOnlyAPIView):
    """GET /v1/ml/zones/{id}/consumption-forecast — suma p10/p50/p90 de hogares."""

    def get(self, request, zone_id: int):
        zone = get_object_or_404(Zone, id=zone_id)
        target_ts = ConsumptionForecast.objects.filter(home__zone=zone).order_by('-target_ts').values_list(
            'target_ts', flat=True,
        ).first()
        if target_ts is None:
            return Response({'zone_id': zone_id, 'consumption_forecast': None})

        totals = ConsumptionForecast.objects.filter(home__zone=zone, target_ts=target_ts).aggregate(
            p10=Sum('p10'), p50=Sum('p50'), p90=Sum('p90'),
        )
        return Response({
            'zone_id': zone_id,
            'consumption_forecast': {
                'target_ts': target_ts,
                'p10': totals['p10'] or 0.0,
                'p50': totals['p50'] or 0.0,
                'p90': totals['p90'] or 0.0,
            },
        })


class ZoneShortagePredictionView(AdminOnlyAPIView):
    """GET /v1/ml/zones/{id}/shortage-prediction — null si aún no se ha predicho este tanque."""

    def get(self, request, zone_id: int):
        get_object_or_404(Zone, id=zone_id)
        prediction = ShortagePrediction.objects.filter(zone_id=zone_id).order_by('-generated_at').first()
        if prediction is None:
            return Response({'zone_id': zone_id, 'shortage_prediction': None})
        return Response({
            'zone_id': zone_id,
            'shortage_prediction': ShortagePredictionSerializer(prediction).data,
        })


class HomeAnomaliesView(AdminOnlyAPIView):
    """GET /v1/ml/homes/{id}/anomalies"""

    def get(self, request, home_id: int):
        get_object_or_404(Home, id=home_id)
        events = AnomalyEvent.objects.filter(home_id=home_id).order_by('-ts')[:100]
        return Response({'home_id': home_id, 'anomalies': AnomalyEventSerializer(events, many=True).data})


class ZoneAnomaliesView(AdminOnlyAPIView):
    """GET /v1/ml/zones/{id}/anomalies"""

    def get(self, request, zone_id: int):
        get_object_or_404(Zone, id=zone_id)
        events = AnomalyEvent.objects.filter(zone_id=zone_id).order_by('-ts')[:100]
        return Response({'zone_id': zone_id, 'anomalies': AnomalyEventSerializer(events, many=True).data})


def _zone_current_status(zone: Zone) -> dict:
    latest_agg_ts = ConsumptionAggregate.objects.filter(home__zone=zone).order_by('-period_start').values_list('period_start', flat=True).first()
    consumo_actual = 0.0
    if latest_agg_ts is not None:
        consumo_actual = ConsumptionAggregate.objects.filter(
            home__zone=zone, period_start=latest_agg_ts,
        ).aggregate(total=Sum('litros'))['total'] or 0.0

    since = django_tz.now() - django_tz.timedelta(hours=24)
    # Posibles fugas: hogares de la zona cuya última evaluación (24 h) supera el umbral de alerta.
    anomalias_24h = _leaking_homes(since, zone=zone)

    prediction = ShortagePrediction.objects.filter(zone=zone).order_by('-generated_at').first()
    alert_state = prediction.nivel_riesgo if prediction else 'sin_datos'

    return {
        'zone_id': zone.id,
        'zone_name': zone.name,
        'nivel_actual_litros': zone.nivel_actual_litros,
        'porcentaje_llenado': round(100.0 * zone.nivel_actual_litros / zone.capacidad_maxima_litros, 1) if zone.capacidad_maxima_litros else 0.0,
        'consumo_actual_lph': consumo_actual,
        'alert_state': alert_state,
        'ultima_prediccion_desabasto': ShortagePredictionSerializer(prediction).data if prediction else None,
        'anomalias_activas_24h': anomalias_24h,
    }


def _leaking_homes(since, zone=None) -> int:
    """Hogares con una predicción de fuga >= umbral en su evaluación más reciente dentro de la ventana."""
    qs = LeakPrediction.objects.filter(generated_at__gte=since)
    if zone is not None:
        qs = qs.filter(zone=zone)
    latest = {}
    for home_id, probability in qs.order_by('generated_at').values_list('home_id', 'probabilidad'):
        latest[home_id] = probability
    return sum(1 for probability in latest.values() if probability >= alert_threshold())


class ZoneCurrentStatusView(AdminOnlyAPIView):
    """GET /v1/ml/zones/{id}/current-status — para uso de dashboard en vivo."""

    def get(self, request, zone_id: int):
        zone = get_object_or_404(Zone, id=zone_id)
        return Response(_zone_current_status(zone))


class ZoneSummaryListView(AdminOnlyAPIView):
    """GET /v1/ml/zones/summary — un tanque (con su vivienda) por fila, para web y app móvil."""

    def get(self, request):
        data = []
        latest_leak = {}
        for home_id, probability in LeakPrediction.objects.order_by('generated_at').values_list('home_id', 'probabilidad'):
            latest_leak[home_id] = probability
        for zone in Zone.objects.filter(homes__activo=True).distinct().order_by('name'):
            status_data = _zone_current_status(zone)
            home = zone.homes.filter(activo=True).first()
            meta = (home.meta if home else {}) or {}
            prediction = status_data['ultima_prediccion_desabasto']
            data.append({
                'zone_id': zone.id,
                'zone_name': zone.name,
                'n_homes': zone.homes.filter(activo=True).count(),
                'nivel_actual_litros': status_data['nivel_actual_litros'],
                'porcentaje_llenado': status_data['porcentaje_llenado'],
                'consumo_total_lph': status_data['consumo_actual_lph'],
                'nivel_riesgo': status_data['alert_state'],
                'anomalias_activas_24h': status_data['anomalias_activas_24h'],
                'nic': meta.get('nic', ''),
                'zona': meta.get('zona', ''),
                'direccion': meta.get('direccion', ''),
                'calidad_datos': 'sin_tanque' if zone.source_ref_tanque_id is None
                else (data_quality(home)['status'] if home else 'sin_datos'),
                'probabilidad_desabasto': prediction['probabilidad_desabasto_horizonte'] if prediction else None,
                'horas_hasta_desabasto': prediction['median_hours_to_shortage'] if prediction else None,
                'prediccion_generada': prediction['generated_at'] if prediction else None,
                'probabilidad_fuga': latest_leak.get(home.id) if home else None,
            })
        return Response({'zonas': ZoneSummarySerializer(data, many=True).data})
