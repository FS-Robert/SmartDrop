"""Simulador de tanque de zona: balance de masa (Sección 4.b / 6 del spec).

tank_level(t+1) = tank_level(t) + inflow(t) - consumption(t)

El inflow puede ser constante o por ciclos de bombeo programados. Se inyectan
fallos de bomba ocasionales para generar escenarios de estrés donde la
demanda supera el suministro el tiempo suficiente para acercarse al umbral
crítico (útil para validar la predicción de desabasto).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import InflowScheduleConfig


def compute_inflow_liters_per_hour(n_hours: int, cfg: InflowScheduleConfig, rng: np.random.Generator, pump_failure_prob_per_day: float = 0.001) -> np.ndarray:
    inflow = np.zeros(n_hours)
    if cfg.kind == 'constant':
        inflow[:] = cfg.constant_lpm * 60.0
    else:  # scheduled_pump
        for h in range(n_hours):
            if (h % 24) in cfg.pump_hours:
                inflow[h] = cfg.pump_rate_lpm * 60.0

    # Fallas de bomba: apagan el inflow durante algunas horas (estrés real).
    days = n_hours // 24
    for day in range(days):
        if rng.random() < pump_failure_prob_per_day:
            start = day * 24 + int(rng.integers(0, 24))
            duration = int(rng.integers(4, 30))
            end = min(start + duration, n_hours)
            inflow[start:end] = 0.0
    return inflow


def simulate_zone_tank(
    *,
    zone_consumption_liters_per_hour: np.ndarray,
    initial_level_litros: float,
    capacidad_maxima_litros: float,
    inflow_cfg: InflowScheduleConfig,
    seed: int,
    pump_failure_prob_per_day: float = 0.001,
) -> pd.DataFrame:
    """Corre el balance de masa hacia adelante y retorna la trayectoria."""
    rng = np.random.default_rng(seed)
    n_hours = len(zone_consumption_liters_per_hour)
    inflow = compute_inflow_liters_per_hour(n_hours, inflow_cfg, rng, pump_failure_prob_per_day)

    level = np.zeros(n_hours)
    current = initial_level_litros
    for h in range(n_hours):
        current = current + inflow[h] - zone_consumption_liters_per_hour[h]
        current = float(np.clip(current, 0.0, capacidad_maxima_litros))
        level[h] = current

    return pd.DataFrame({
        'hour_index': np.arange(n_hours),
        'inflow_litros': inflow,
        'consumo_litros': zone_consumption_liters_per_hour,
        'nivel_litros': level,
    })
