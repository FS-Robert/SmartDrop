"""Simulación de balance de masa + predicción de desabasto (Sección 6).

No se entrena un modelo que prediga directamente "horas hasta desabasto".
En su lugar:

1. Se construye una trayectoria de consumo por hogar para el horizonte
   (perfil horario histórico escalado por la incertidumbre calibrada del
   modelo de consumo p10/p50/p90 más reciente — ver nota de diseño abajo).
2. Se combina con el inflow conocido/programado de la zona.
3. Se corre el balance de masa hacia adelante vía Monte Carlo, muestreando
   el consumo de cada hogar en cada paso desde su distribución p10/p50/p90.
4. Se calcula el "first-passage time" al umbral crítico para cada
   trayectoria simulada.
5. Se reporta la mediana de horas-hasta-desabasto + intervalo p10-p90.

Nota de diseño / limitación conocida (documentada, no oculta): el modelo
LightGBM del Fase 1 predice un solo paso (t+1h). Para extender a un
horizonte de N horas sin re-entrenar un modelo multi-horizonte, escalamos el
perfil histórico por hora-del-día de cada hogar usando la razón p10/p50/p90
de la predicción más reciente. Es una aproximación razonable para validar
el pipeline end-to-end; el upgrade natural es un modelo de horizonte múltiple
(forecasting recursivo o directo, ver Sección 5 "upgrade path").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from django.utils import timezone as django_tz

from ml_engine.forecasting.predict import predict_consumption_for_homes
from ml_engine.models import (
    ConsumptionAggregate,
    ConsumptionForecast,
    Home,
    SensorReading,
    ShortagePrediction,
    TankTrajectory,
    Zone,
)
from ml_engine.simulation.zone import compute_inflow_liters_per_hour
from ml_engine.simulation.config import InflowScheduleConfig

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_HOURS = 72
DEFAULT_N_PATHS = 500


@dataclass
class HomeHourlyProfile:
    home_id: int
    baseline_by_hour: np.ndarray  # len 24
    ratio_low: float
    ratio_high: float


def _historical_hour_of_day_profile(home_id: int) -> np.ndarray:
    rows = ConsumptionAggregate.objects.filter(home_id=home_id, period='hourly').values('period_start', 'litros')
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return np.full(24, 5.0)
    df['period_start'] = pd.to_datetime(df['period_start'], utc=True)
    df['hour'] = df['period_start'].dt.hour
    profile = df.groupby('hour')['litros'].mean().reindex(range(24)).fillna(df['litros'].mean())
    return profile.to_numpy()


def _latest_forecast_ratio(home_id: int) -> tuple[float, float, float]:
    forecast = ConsumptionForecast.objects.filter(home_id=home_id).order_by('-target_ts').first()
    if forecast is None:
        return 1.0, 0.6, 1.5
    p50 = max(forecast.p50, 1e-3)
    return forecast.p50, max(forecast.p10 / p50, 0.05), max(forecast.p90 / p50, 1.0)


def _build_home_profiles(home_ids: list[int]) -> list[HomeHourlyProfile]:
    profiles = []
    for home_id in home_ids:
        baseline = _historical_hour_of_day_profile(home_id)
        p50_latest, ratio_low, ratio_high = _latest_forecast_ratio(home_id)
        current_hour_baseline = max(baseline[django_tz.now().hour], 1e-3)
        scale = p50_latest / current_hour_baseline
        profiles.append(HomeHourlyProfile(home_id, baseline * scale, ratio_low, ratio_high))
    return profiles


def _sample_two_piece_normal(p10: np.ndarray, p50: np.ndarray, p90: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    z90 = 1.2815515655446004
    sigma_low = np.maximum((p50 - p10) / z90, 1e-6)
    sigma_high = np.maximum((p90 - p50) / z90, 1e-6)
    u = rng.random(p50.shape)
    sample = np.where(
        u < 0.5,
        p50 - np.abs(rng.normal(0, 1, p50.shape)) * sigma_low,
        p50 + np.abs(rng.normal(0, 1, p50.shape)) * sigma_high,
    )
    return np.clip(sample, 0, None)


def run_shortage_prediction(
    zone_id: int,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    n_paths: int = DEFAULT_N_PATHS,
    seed: int = 1234,
) -> ShortagePrediction:
    zone = Zone.objects.get(id=zone_id)
    home_ids = list(zone.homes.filter(activo=True).values_list('id', flat=True))
    if not home_ids:
        raise ValueError(f'La zona {zone_id} no tiene hogares activos.')

    # Asegura que exista una predicción de consumo reciente por hogar.
    predict_consumption_for_homes(home_ids)
    profiles = _build_home_profiles(home_ids)

    rng = np.random.default_rng(seed)
    zone_consumption_paths = np.zeros((n_paths, horizon_hours))
    for profile in profiles:
        p50_series = np.tile(profile.baseline_by_hour, horizon_hours // 24 + 1)[:horizon_hours]
        p10_series = p50_series * profile.ratio_low
        p90_series = p50_series * profile.ratio_high
        for h in range(horizon_hours):
            sampled = _sample_two_piece_normal(
                np.full(n_paths, p10_series[h]),
                np.full(n_paths, p50_series[h]),
                np.full(n_paths, p90_series[h]),
                rng,
            )
            zone_consumption_paths[:, h] += sampled

    inflow_cfg = InflowScheduleConfig(**{
        k: tuple(v) if isinstance(v, list) else v
        for k, v in zone.inflow_schedule.items()
    }) if zone.inflow_schedule else InflowScheduleConfig()
    inflow = compute_inflow_liters_per_hour(horizon_hours, inflow_cfg, rng, pump_failure_prob_per_day=0.0)

    latest_level_reading = SensorReading.objects.filter(zone_id=zone_id, metric='nivel_tanque').order_by('-ts').first()
    initial_level = latest_level_reading.value if latest_level_reading else zone.nivel_actual_litros

    levels = np.zeros((n_paths, horizon_hours))
    current = np.full(n_paths, initial_level)
    for h in range(horizon_hours):
        current = current + inflow[h] - zone_consumption_paths[:, h]
        current = np.clip(current, 0.0, zone.capacidad_maxima_litros)
        levels[:, h] = current

    crossed = levels <= zone.nivel_critico_litros
    first_passage = np.where(crossed.any(axis=1), crossed.argmax(axis=1) + 1, -1)
    reached_mask = first_passage > 0
    probabilidad = float(reached_mask.mean())

    if reached_mask.any():
        hours_reached = first_passage[reached_mask]
        median_h = float(np.median(hours_reached))
        p10_h = float(np.percentile(hours_reached, 10))
        p90_h = float(np.percentile(hours_reached, 90))
    else:
        median_h = p10_h = p90_h = None

    if probabilidad >= 0.5 and median_h is not None and median_h <= 24:
        riesgo = 'alto'
    elif probabilidad >= 0.2:
        riesgo = 'medio'
    else:
        riesgo = 'bajo'

    now = django_tz.now()
    trajectory_rows = [
        TankTrajectory(
            zone_id=zone_id,
            target_ts=now + pd.Timedelta(hours=h + 1),
            p10_litros=float(np.percentile(levels[:, h], 10)),
            p50_litros=float(np.percentile(levels[:, h], 50)),
            p90_litros=float(np.percentile(levels[:, h], 90)),
        )
        for h in range(horizon_hours)
    ]
    TankTrajectory.objects.filter(zone_id=zone_id).delete()
    TankTrajectory.objects.bulk_create(trajectory_rows)

    prediction = ShortagePrediction.objects.create(
        zone_id=zone_id,
        median_hours_to_shortage=median_h,
        p10_hours_to_shortage=p10_h,
        p90_hours_to_shortage=p90_h,
        probabilidad_desabasto_horizonte=probabilidad,
        horizonte_horas=horizon_hours,
        nivel_riesgo=riesgo,
    )
    logger.info('Predicción de desabasto zona=%s riesgo=%s prob=%.2f mediana_h=%s', zone_id, riesgo, probabilidad, median_h)
    return prediction
