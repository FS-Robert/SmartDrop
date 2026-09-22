"""Simulador estocástico de consumo por hogar (Sección 4.a del spec).

Modelo de eventos de uso (stochastic end-use): para cada hora del día se
sortea un número de eventos por electrodoméstico vía un proceso de Poisson
cuya tasa depende del arquetipo del hogar y del perfil horario; cada evento
tiene una duración y un caudal aleatorios (lognormal/normal truncada). La
suma de eventos por hora produce litros consumidos "bursty" y realistas.

También simula lecturas auxiliares (presión, calidad) e inyecta anomalías
(fuga, ruptura, ausencia/vacación, incidente de calidad) de forma que el
módulo de anomalías (Sección 7) tenga señal real que detectar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import APPLIANCES, ARCHETYPES, AnomalyInjectionConfig


@dataclass
class HomeAnomalyWindow:
    kind: str
    start_hour_index: int
    duration_hours: int
    magnitude: float


def _normalized_weights(weights: tuple) -> np.ndarray:
    arr = np.asarray(weights, dtype=float)
    return arr / arr.mean()


def _roll_anomaly_windows(n_hours: int, days: int, cfg: AnomalyInjectionConfig, rng: np.random.Generator) -> list[HomeAnomalyWindow]:
    windows: list[HomeAnomalyWindow] = []
    checks = [
        ('leak', cfg.leak_prob_per_home_day, (6, 72), (1.5, 4.0)),
        ('burst', cfg.burst_prob_per_home_day, (1, 3), (30.0, 90.0)),
        ('vacation', cfg.vacation_dip_prob_per_home_day, (72, 240), (0.05, 0.2)),
        ('quality_incident', cfg.quality_incident_prob_per_home_day, (2, 10), (150.0, 500.0)),
    ]
    for day in range(days):
        for kind, prob, dur_range, mag_range in checks:
            if rng.random() < prob:
                start = day * 24 + int(rng.integers(0, 24))
                duration = int(rng.integers(dur_range[0], dur_range[1] + 1))
                duration = min(duration, n_hours - start)
                if duration <= 0:
                    continue
                magnitude = float(rng.uniform(*mag_range))
                windows.append(HomeAnomalyWindow(kind, start, duration, magnitude))
    return windows


def simulate_home_hourly(
    *,
    archetype: str,
    occupants: int,
    start_date: str,
    days: int,
    seed: int,
    anomalies: AnomalyInjectionConfig,
) -> pd.DataFrame:
    """Simula `days` días de lecturas horarias para un hogar sintético.

    Retorna un DataFrame con columnas: ts, litros, presion, calidad,
    anomaly_kind (o '' si ninguna).
    """
    rng = np.random.default_rng(seed)
    n_hours = days * 24
    ts_index = pd.date_range(start=start_date, periods=n_hours, freq='h')

    profile = ARCHETYPES[archetype]
    litros = np.zeros(n_hours)

    for name, mult in profile.appliance_multipliers.items():
        appliance = APPLIANCES[name]
        weights = _normalized_weights(appliance.hourly_weights)
        daily_expected = appliance.uses_per_occupant_day * occupants * mult
        hourly_expected = daily_expected / 24.0 * weights
        for h in range(n_hours):
            hour_of_day = h % 24
            expected = max(hourly_expected[hour_of_day], 1e-6)
            n_events = rng.poisson(expected)
            if n_events <= 0:
                continue
            for _ in range(n_events):
                litros[h] += appliance.liters_per_event(rng)

    # --- Presión y calidad base por hogar ---
    presion_base = rng.uniform(35.0, 55.0)
    calidad_base = rng.uniform(150.0, 320.0)
    max_litros_hora = max(litros.max(), 1.0)
    presion = presion_base - (litros / max_litros_hora) * rng.uniform(2.0, 6.0) + rng.normal(0, 0.8, n_hours)
    calidad = calidad_base + rng.normal(0, 8.0, n_hours)

    anomaly_kind = np.array([''] * n_hours, dtype=object)
    windows = _roll_anomaly_windows(n_hours, days, anomalies, rng)
    for w in windows:
        end = min(w.start_hour_index + w.duration_hours, n_hours)
        idx = slice(w.start_hour_index, end)
        if w.kind == 'leak':
            litros[idx] += w.magnitude * 60.0  # fuga sostenida: L/min * 60
            presion[idx] -= w.magnitude
            anomaly_kind[idx] = 'leak'
        elif w.kind == 'burst':
            litros[idx] += w.magnitude
            presion[idx] -= w.magnitude / 8.0
            anomaly_kind[idx] = 'burst'
        elif w.kind == 'vacation':
            litros[idx] *= w.magnitude
            anomaly_kind[idx] = 'vacation'
        elif w.kind == 'quality_incident':
            calidad[idx] += w.magnitude
            anomaly_kind[idx] = 'quality_incident'

    litros = np.clip(litros, 0, None)
    presion = np.clip(presion, 5.0, None)
    calidad = np.clip(calidad, 0, None)

    return pd.DataFrame({
        'ts': ts_index,
        'litros': litros,
        'presion': presion,
        'calidad': calidad,
        'anomaly_kind': anomaly_kind,
    })


def pick_archetype(rng: np.random.Generator) -> tuple[str, int]:
    names = list(ARCHETYPES.keys())
    archetype = names[rng.integers(0, len(names))]
    lo, hi = ARCHETYPES[archetype].occupants_range
    occupants = int(rng.integers(lo, hi + 1))
    return archetype, occupants
