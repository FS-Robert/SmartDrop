from rest_framework import serializers

from ml_engine.models import (
    AnomalyEvent,
    ConsumptionForecast,
    ShortagePrediction,
    TankTrajectory,
    Zone,
)


class ConsumptionForecastSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsumptionForecast
        fields = ['target_ts', 'horizon', 'p10', 'p50', 'p90', 'explanation', 'generated_at']


class TankTrajectorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TankTrajectory
        fields = ['target_ts', 'p10_litros', 'p50_litros', 'p90_litros']


class ShortagePredictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShortagePrediction
        fields = [
            'generated_at', 'median_hours_to_shortage', 'p10_hours_to_shortage',
            'p90_hours_to_shortage', 'probabilidad_desabasto_horizonte',
            'horizonte_horas', 'nivel_riesgo',
        ]


class AnomalyEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnomalyEvent
        fields = ['home_id', 'zone_id', 'metric', 'ts', 'detected_at', 'score', 'severity', 'detail']


class ZoneCurrentStatusSerializer(serializers.Serializer):
    zone_id = serializers.IntegerField()
    zone_name = serializers.CharField()
    nivel_actual_litros = serializers.FloatField()
    porcentaje_llenado = serializers.FloatField()
    consumo_actual_lph = serializers.FloatField()
    alert_state = serializers.CharField()
    ultima_prediccion_desabasto = ShortagePredictionSerializer(allow_null=True)
    anomalias_activas_24h = serializers.IntegerField()


class ZoneSummarySerializer(serializers.Serializer):
    """Resumen agregado por zona, pensado para el dashboard (Sección 9)."""

    zone_id = serializers.IntegerField()
    zone_name = serializers.CharField()
    n_homes = serializers.IntegerField()
    nivel_actual_litros = serializers.FloatField()
    porcentaje_llenado = serializers.FloatField()
    consumo_total_lph = serializers.FloatField()
    nivel_riesgo = serializers.CharField()
    anomalias_activas_24h = serializers.IntegerField()
