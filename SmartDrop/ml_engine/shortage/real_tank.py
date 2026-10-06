"""Simulación de nivel de un tanque real con bomba por umbral (en vez de un inflow programado).

El tanque real se recarga cuando su nivel baja de cierto umbral y deja de hacerlo al llegar a otro. Esos
umbrales y la velocidad de llenado se estiman del historial de nivel. Como la causa típica de un desabasto
es que la bomba deje de recargar, cada trayectoria Monte Carlo puede sufrir cortes de recarga aleatorios, y si
en este momento el nivel ya está por debajo del umbral de arranque sin subir, se asume que la bomba falló ahora.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from django.utils import timezone as django_tz

from ml_engine.models import SensorReading

HISTORY_DAYS = 14
RISE_THRESHOLD_FRACTION = 0.01  # subida mínima (fracción de capacidad) para considerar que la bomba recarga
PUMP_FAIL_PROB_PER_HOUR = 0.002
MEAN_OUTAGE_HOURS = 4.0
RECENT_RISE_WINDOW_MINUTES = 40


@dataclass
class PumpModel:
    on_level: float
    off_level: float
    rise_events: int
    pump_failed_now: bool
    last_rise_hours_ago: float | None
    drain_lph: float | None


def estimate_pump_model(zone) -> PumpModel:
    capacity = zone.capacidad_maxima_litros
    now = django_tz.now()
    since = now - django_tz.timedelta(days=HISTORY_DAYS)
    rows = SensorReading.objects.filter(zone=zone, metric='nivel_tanque', ts__gte=since).order_by('ts').values('ts', 'value')
    frame = pd.DataFrame.from_records(rows)
    defaults = PumpModel(0.55 * capacity, 0.92 * capacity, 0, False, None, None)
    if len(frame) < 30:
        return defaults

    frame['ts'] = pd.to_datetime(frame['ts'], utc=True)
    series = frame.set_index('ts')['value'].resample('10min').mean().interpolate(limit=3).dropna()
    delta = series.diff()
    rising = delta > capacity * RISE_THRESHOLD_FRACTION

    starts, ends = [], []
    previous = False
    for index in range(len(series)):
        is_rising = bool(rising.iloc[index])
        if is_rising and not previous:
            starts.append(series.iloc[max(index - 1, 0)])
        if previous and not is_rising:
            ends.append(series.iloc[index - 1])
        previous = is_rising

    on_level = float(np.median(starts)) if starts else defaults.on_level
    off_level = float(np.median(ends)) if ends else defaults.off_level
    if off_level <= on_level:
        on_level, off_level = defaults.on_level, defaults.off_level

    rise_times = series.index[rising.to_numpy()]
    last_rise_hours = float((now - rise_times[-1]).total_seconds() / 3600) if len(rise_times) else None
    current = float(series.iloc[-1])
    recently_rose = last_rise_hours is not None and last_rise_hours * 60 <= RECENT_RISE_WINDOW_MINUTES
    pump_failed_now = current <= on_level and not recently_rose

    last_hour = series[series.index >= series.index[-1] - pd.Timedelta(hours=1)]
    drain = None
    if len(last_hour) >= 2 and not recently_rose:
        drain = float(max(last_hour.iloc[0] - last_hour.iloc[-1], 0.0) / max((last_hour.index[-1] - last_hour.index[0]).total_seconds() / 3600, 1e-3))
    return PumpModel(on_level, off_level, len(starts), pump_failed_now, last_rise_hours, drain)


def simulate_levels(pump: PumpModel, initial_level, critical_level, capacity, consumption_paths, rng):
    """Niveles por trayectoria (n_paths x horizonte) con bomba por umbral y cortes de recarga."""
    n_paths, horizon = consumption_paths.shape
    levels = np.zeros((n_paths, horizon))
    current = np.full(n_paths, float(initial_level))
    outage_left = np.zeros(n_paths)
    if pump.pump_failed_now:
        outage_left = rng.exponential(MEAN_OUTAGE_HOURS, n_paths) + 1.0

    for hour in range(horizon):
        new_outage = (outage_left <= 0) & (rng.random(n_paths) < PUMP_FAIL_PROB_PER_HOUR)
        outage_left = np.where(new_outage, rng.exponential(MEAN_OUTAGE_HOURS, n_paths) + 1.0, outage_left)
        pump_available = outage_left <= 0
        outage_left = np.maximum(outage_left - 1.0, 0.0)

        current = np.clip(current - consumption_paths[:, hour], 0.0, capacity)
        refill = pump_available & (current <= pump.on_level)
        current = np.where(refill, pump.off_level, current)
        levels[:, hour] = current
    return levels
