from rest_framework import serializers

from ml_engine.models import (
    AnomalyEvent,
    ConsumptionForecast,
    ShortagePrediction,
    TankTrajectory,
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
            'horizonte_horas', 'nivel_riesgo', 'details',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.nivel_riesgo == 'bajo':
            # Con riesgo bajo la mediana sale de pocas trayectorias extremas: no es una autonomía real.
            data['median_hours_to_shortage'] = data['p10_hours_to_shortage'] = data['p90_hours_to_shortage'] = None
        return data


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
    nic = serializers.CharField(required=False, allow_blank=True)
    zona = serializers.CharField(required=False, allow_blank=True)
    direccion = serializers.CharField(required=False, allow_blank=True)
    calidad_datos = serializers.CharField(required=False, allow_blank=True)
    probabilidad_desabasto = serializers.FloatField(required=False, allow_null=True)
    horas_hasta_desabasto = serializers.FloatField(required=False, allow_null=True)
    prediccion_generada = serializers.DateTimeField(required=False, allow_null=True)
    probabilidad_fuga = serializers.FloatField(required=False, allow_null=True)
