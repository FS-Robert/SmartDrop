"""Modelos del motor de predicción (ML) de SmartDrop.

Cada `Zone` es un tanque real de Supabase (tabla `tanque`) y cada `Home` una
vivienda real (tabla `vivienda`); `ml_engine/realdata/sync.py` los crea y copia
aquí sus lecturas. `source` conserva el valor 'synthetic' solo por compatibilidad
con bases antiguas: el generador de hogares sintéticos se retiró y el pipeline
únicamente usa los de origen 'real'.
"""
from pathlib import Path, PureWindowsPath

from django.conf import settings
from django.db import models


class SourceType(models.TextChoices):
    SYNTHETIC = 'synthetic', 'Sintético'
    REAL = 'real', 'Dispositivo real (ESP32)'


class HouseholdArchetype(models.TextChoices):
    SINGLE = 'single', 'Ocupante único'
    COUPLE = 'couple', 'Pareja'
    FAMILY_SMALL = 'family_small', 'Familia pequeña (3-4)'
    FAMILY_LARGE = 'family_large', 'Familia grande (5+)'
    IRRIGATION = 'irrigation_heavy', 'Uso intensivo de riego'
    HOME_OFFICE = 'home_office', 'Oficina en casa (uso diurno alto)'
    VACATION = 'low_occupancy', 'Baja ocupación / vacacional'


class Zone(models.Model):
    """Una zona de abastecimiento servida por un tanque."""

    name = models.CharField(max_length=120)
    source = models.CharField(max_length=12, choices=SourceType.choices, default=SourceType.SYNTHETIC)
    source_ref_tanque_id = models.BigIntegerField(
        null=True, blank=True,
        help_text='id_tanque real (tabla tanque) cuando source=real.',
    )

    # Parámetros físicos del tanque (calibración altura->volumen, Sección 6).
    capacidad_maxima_litros = models.FloatField(default=10_000.0)
    nivel_critico_litros = models.FloatField(default=1_500.0)
    forma_geometrica = models.CharField(max_length=20, default='cilindrico')
    altura_total_cm = models.FloatField(default=200.0)
    diametro_cm = models.FloatField(null=True, blank=True)
    largo_cm = models.FloatField(null=True, blank=True)
    ancho_cm = models.FloatField(null=True, blank=True)

    # Sin uso desde que se retiró el generador sintético (la recarga real se estima en shortage/real_tank.py).
    inflow_schedule = models.JSONField(default=dict, blank=True)

    nivel_actual_litros = models.FloatField(default=8_000.0)
    meta = models.JSONField(default=dict, blank=True, help_text='Datos del tanque real (nombre, sensor de nivel, umbrales en cm).')
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ml_zone'
        verbose_name = 'zona (ML)'
        verbose_name_plural = 'zonas (ML)'

    def __str__(self):
        return f'{self.name} ({self.get_source_display()})'


class Home(models.Model):
    """Una vivienda de Supabase. Unidad atómica del pipeline de consumo."""

    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='homes')
    source = models.CharField(max_length=12, choices=SourceType.choices, default=SourceType.SYNTHETIC)
    source_ref_vivienda_id = models.BigIntegerField(
        null=True, blank=True, unique=True,
        help_text='id_vivienda real (tabla vivienda) cuando source=real. Null para hogares sintéticos.',
    )
    archetype = models.CharField(max_length=20, choices=HouseholdArchetype.choices, default=HouseholdArchetype.FAMILY_SMALL)
    ocupantes = models.PositiveSmallIntegerField(default=3)
    cluster_id = models.PositiveSmallIntegerField(
        default=0,
        help_text='Categoría usada como feature del modelo global (no es 1 modelo por hogar).',
    )
    etiqueta = models.CharField(max_length=120, blank=True)
    activo = models.BooleanField(default=True)
    meta = models.JSONField(default=dict, blank=True, help_text='Datos de la vivienda real (NIC, dirección, zona, titular, sensores).')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ml_home'
        verbose_name = 'hogar (ML)'
        verbose_name_plural = 'hogares (ML)'
        indexes = [models.Index(fields=['zone', 'activo'])]

    def __str__(self):
        return self.etiqueta or f'Home #{self.pk}'


class SensorReading(models.Model):
    """Lectura copiada de la tabla `lectura` de Supabase (el nivel del tanque, ya convertido a litros).

    Esta es la tabla de series temporales de alto volumen. En Postgres se
    recomienda particionarla por rango de tiempo con pg_partman (ver
    ml_engine/management/commands/ml_setup_partitioning.py); en SQLite
    (desarrollo local) se usa tal cual, sin particionar.
    """

    class Metric(models.TextChoices):
        FLOW = 'flujo', 'Flujo (L/min)'
        PRESSURE = 'presion', 'Presión'
        QUALITY = 'calidad', 'Calidad (TDS)'
        TANK_LEVEL = 'nivel_tanque', 'Nivel de tanque (litros)'

    home = models.ForeignKey(Home, null=True, blank=True, on_delete=models.CASCADE, related_name='readings')
    zone = models.ForeignKey(Zone, null=True, blank=True, on_delete=models.CASCADE, related_name='readings')
    metric = models.CharField(max_length=20, choices=Metric.choices)
    ts = models.DateTimeField(db_index=True)
    value = models.FloatField()
    is_valid = models.BooleanField(default=True)

    class Meta:
        db_table = 'ml_sensor_reading'
        verbose_name = 'lectura (ML)'
        verbose_name_plural = 'lecturas (ML)'
        indexes = [
            models.Index(fields=['home', 'metric', 'ts']),
            models.Index(fields=['zone', 'metric', 'ts']),
            # Última lectura de un hogar sin importar la métrica (data_quality, sincronización).
            models.Index(fields=['home', 'ts']),
        ]

    def __str__(self):
        target = self.home_id and f'home={self.home_id}' or f'zone={self.zone_id}'
        return f'{self.metric} {target} @ {self.ts.isoformat()} = {self.value}'


class ConsumptionAggregate(models.Model):
    """Consumo agregado por hogar y periodo (input de features + modelo)."""

    class Period(models.TextChoices):
        HOURLY = 'hourly', 'Por hora'
        DAILY = 'daily', 'Diario'

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name='consumo_agregado')
    period = models.CharField(max_length=10, choices=Period.choices, default=Period.HOURLY)
    period_start = models.DateTimeField()
    litros = models.FloatField()
    presion_media = models.FloatField(null=True, blank=True)
    calidad_media = models.FloatField(null=True, blank=True)
    sensor_offline = models.BooleanField(default=False)
    anomaly_kind = models.CharField(
        max_length=32, blank=True, default='',
        help_text='Etiqueta del simulador (leak/burst/…) para entrenar predicción de fugas. Vacío en datos reales.',
    )

    class Meta:
        db_table = 'ml_consumption_aggregate'
        unique_together = [('home', 'period', 'period_start')]
        indexes = [models.Index(fields=['home', 'period', 'period_start'])]

    def __str__(self):
        return f'{self.home_id} {self.period} {self.period_start.isoformat()} = {self.litros}L'


class ModelArtifact(models.Model):
    """Metadatos de un modelo entrenado (los binarios se guardan en disco)."""

    class Kind(models.TextChoices):
        CONSUMPTION_QUANTILE = 'consumption_quantile', 'Consumo (cuantiles)'
        ANOMALY_DETECTOR = 'anomaly_detector', 'Detector de anomalías'
        LEAK_PREDICTOR = 'leak_predictor', 'Predictor de fugas'

    kind = models.CharField(max_length=30, choices=Kind.choices)
    version = models.CharField(max_length=40)
    file_path = models.CharField(max_length=500)
    metrics = models.JSONField(default=dict, blank=True)
    feature_names = models.JSONField(default=list, blank=True)
    trained_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'ml_model_artifact'
        ordering = ['-trained_at']

    def __str__(self):
        return f'{self.kind} v{self.version}'

    @property
    def directory(self) -> Path:
        """Carpeta con los binarios del modelo.

        Si `file_path` se guardó en otra máquina (p. ej. una ruta de Windows y la base se copió al
        servidor Linux), se reconstruye como ML_MODELS_DIR/<tipo>/<versión> con sus dos últimas partes.
        """
        stored = Path(self.file_path)
        if stored.is_dir():
            return stored
        return Path(settings.ML_MODELS_DIR).joinpath(*PureWindowsPath(self.file_path).parts[-2:])


class ConsumptionForecast(models.Model):
    """Pronóstico de consumo por hogar (salida del modelo, p10/p50/p90)."""

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name='forecasts')
    generated_at = models.DateTimeField(auto_now_add=True)
    target_ts = models.DateTimeField(db_index=True)
    horizon = models.CharField(max_length=10, default='hourly')
    p10 = models.FloatField()
    p50 = models.FloatField()
    p90 = models.FloatField()
    model_artifact = models.ForeignKey(ModelArtifact, null=True, blank=True, on_delete=models.SET_NULL)
    explanation = models.JSONField(default=dict, blank=True, help_text='Top SHAP features para esta predicción.')

    class Meta:
        db_table = 'ml_consumption_forecast'
        indexes = [models.Index(fields=['home', 'target_ts'])]
        ordering = ['target_ts']

    def __str__(self):
        return f'home={self.home_id} @ {self.target_ts.isoformat()} p50={self.p50}'


class TankTrajectory(models.Model):
    """Trayectoria simulada (Monte Carlo) de nivel de tanque para una zona."""

    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='trajectories')
    generated_at = models.DateTimeField(auto_now_add=True)
    target_ts = models.DateTimeField(db_index=True)
    p10_litros = models.FloatField()
    p50_litros = models.FloatField()
    p90_litros = models.FloatField()

    class Meta:
        db_table = 'ml_tank_trajectory'
        indexes = [models.Index(fields=['zone', 'target_ts'])]
        ordering = ['target_ts']


class ShortagePrediction(models.Model):
    """Predicción de tiempo-hasta-desabasto (first-passage-time), por zona."""

    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='shortage_predictions')
    generated_at = models.DateTimeField(auto_now_add=True)
    median_hours_to_shortage = models.FloatField(null=True, blank=True)
    p10_hours_to_shortage = models.FloatField(null=True, blank=True)
    p90_hours_to_shortage = models.FloatField(null=True, blank=True)
    probabilidad_desabasto_horizonte = models.FloatField(
        default=0.0, help_text='Fracción de trayectorias Monte Carlo que cruzan el umbral crítico dentro del horizonte.',
    )
    horizonte_horas = models.PositiveIntegerField(default=72)
    nivel_riesgo = models.CharField(max_length=15, default='bajo')
    details = models.JSONField(default=dict, blank=True, help_text='Nivel actual, estado de la bomba y otros datos de la predicción.')

    class Meta:
        db_table = 'ml_shortage_prediction'
        ordering = ['-generated_at']

    def __str__(self):
        return f'zone={self.zone_id} mediana={self.median_hours_to_shortage}h'


class AnomalyEvent(models.Model):
    """Evento de anomalía/fuga detectado sobre residuales de presión/flujo."""

    class Metric(models.TextChoices):
        PRESSURE = 'presion', 'Presión'
        FLOW = 'flujo', 'Flujo'

    home = models.ForeignKey(Home, null=True, blank=True, on_delete=models.CASCADE, related_name='anomalies')
    zone = models.ForeignKey(Zone, null=True, blank=True, on_delete=models.CASCADE, related_name='anomalies')
    metric = models.CharField(max_length=10, choices=Metric.choices)
    detected_at = models.DateTimeField(auto_now_add=True)
    ts = models.DateTimeField()
    score = models.FloatField(help_text='Score de anomalía (más negativo/alto = más anómalo, según método).')
    severity = models.CharField(max_length=10, default='media')
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'ml_anomaly_event'
        indexes = [models.Index(fields=['home', 'ts']), models.Index(fields=['zone', 'ts'])]
        ordering = ['-ts']


class LeakPrediction(models.Model):
    """Probabilidad de fuga en el horizonte (horas siguientes), por hogar."""

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name='leak_predictions')
    zone = models.ForeignKey(Zone, null=True, blank=True, on_delete=models.CASCADE, related_name='leak_predictions')
    generated_at = models.DateTimeField(auto_now_add=True)
    horizonte_horas = models.PositiveIntegerField(default=12)
    probabilidad = models.FloatField(help_text='Probabilidad 0–1 de fuga en el horizonte.')
    nivel_riesgo = models.CharField(max_length=15, default='bajo')
    method = models.CharField(max_length=20, default='heuristic')
    drivers = models.JSONField(default=list, blank=True)
    features = models.JSONField(default=dict, blank=True, help_text='Métricas medidas que sustentan la predicción (para el aviso al admin).')
    evaluated_until = models.DateTimeField(null=True, blank=True, help_text='Timestamp de la última lectura analizada.')
    fuga_id = models.BigIntegerField(null=True, blank=True, help_text='id_fuga creado en Supabase si se generó alerta.')
    alerta_id = models.BigIntegerField(null=True, blank=True, help_text='id_alerta creado en Supabase si se generó alerta.')
    alert_sent_at = models.DateTimeField(null=True, blank=True)
    model_artifact = models.ForeignKey(ModelArtifact, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'ml_leak_prediction'
        indexes = [models.Index(fields=['home', 'generated_at']), models.Index(fields=['zone', 'generated_at'])]
        ordering = ['-generated_at']

    def __str__(self):
        return f'home={self.home_id} riesgo={self.nivel_riesgo} p={self.probabilidad:.2f}'

    @property
    def porcentaje(self) -> float:
        return round(max(0.0, min(1.0, self.probabilidad)) * 100.0, 1)

class SyncState(models.Model):
    """Hasta qué lectura de Supabase se ha sincronizado cada hogar real (sincronización incremental)."""

    home = models.OneToOneField(Home, on_delete=models.CASCADE, related_name='sync_state')
    last_reading_ts = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ml_sync_state'


class PredictionJob(models.Model):
    """Ejecución del botón 'Realizar predicciones'; la web la consulta por polling."""

    class Status(models.TextChoices):
        RUNNING = 'running', 'En curso'
        DONE = 'done', 'Terminada'
        ERROR = 'error', 'Con error'

    id = models.CharField(max_length=32, primary_key=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    step = models.CharField(max_length=120, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.BigIntegerField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        db_table = 'ml_prediction_job'
        ordering = ['-started_at']


class MonitorState(models.Model):
    """Estado del monitor automático de fugas; sirve además de cerrojo entre procesos."""

    key = models.CharField(max_length=40, primary_key=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True)
    last_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'ml_monitor_state'