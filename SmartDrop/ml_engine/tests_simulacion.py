"""Generador de lecturas inventadas SOLO para las pruebas automáticas del motor de predicción.

Nunca escribe en Supabase: las pruebas lo usan con una base de mentira en memoria para tener series con
ciclos diarios, fugas y fallas de bomba conocidas de antemano. Antes vivía en el comando
ml_seed_demo_readings, que llenaba Supabase con datos falsos y se quitó el 25/11/2026.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

STEP_MINUTES = 10

def _daily_profile(hour_fraction, evening_bias):
    """Perfil de uso doméstico 0..1: picos de mañana y de tarde, casi nulo de madrugada."""
    morning = np.exp(-((hour_fraction - 7.0) ** 2) / 2.0)
    evening = np.exp(-((hour_fraction - 19.0) ** 2) / 3.0) * evening_bias
    midday = 0.35 * np.exp(-((hour_fraction - 13.0) ** 2) / 4.0)
    return morning + evening + midday + 0.01


def simulate_home(vivienda_id, tank, start, steps, scenario, rng, initial_level_cm=None):
    """Devuelve arrays flujo (L/min), presion (bar), nivel (cm), tds (ppm) por paso de 10 min.

    `initial_level_cm` continúa la serie desde un nivel conocido (modo --extend).
    """
    capacity = float(tank['capacidad_maxima_litros'])
    height = float(tank['altura_total'])
    critical_l = capacity * float(tank['nivel_critico_cm']) / height
    peak_flow = rng.uniform(0.12, 0.35)
    evening_bias = rng.uniform(0.8, 1.3)
    base_pressure = rng.uniform(2.0, 3.7)
    base_tds = rng.uniform(90, 340)
    pump_lpm = rng.uniform(1.2, 1.8)

    level = capacity * rng.uniform(0.6, 0.9)
    if initial_level_cm is not None:
        level = float(np.clip(initial_level_cm / height * capacity, 0.0, capacity))
    pump_on = False
    tds = base_tds
    leak_start = steps - scenario['leak_steps'] if scenario['leak'] else None
    pump_fail_start = steps - scenario['pump_fail_steps'] if scenario['pump_fail'] else None
    leak_flow = rng.uniform(0.14, 0.26)

    flow = np.zeros(steps)
    pressure = np.zeros(steps)
    level_cm = np.zeros(steps)
    tds_series = np.zeros(steps)
    for i in range(steps):
        moment = start + timedelta(minutes=STEP_MINUTES * i)
        local_hour = (moment.hour - 6) % 24 + moment.minute / 60.0
        weekday_factor = 1.15 if moment.weekday() >= 5 else 1.0
        use = peak_flow * weekday_factor * _daily_profile(local_hour, evening_bias) * rng.uniform(0.6, 1.3)
        use = max(use * (rng.random() < 0.85), 0.0)

        leaking = leak_start is not None and i >= leak_start
        if leaking:
            ramp = min((i - leak_start) / 6.0, 1.0)
            use += leak_flow * ramp * rng.uniform(0.9, 1.1)

        pump_allowed = not (pump_fail_start is not None and i >= pump_fail_start)
        if level <= capacity * 0.55 and pump_allowed:
            pump_on = True
        if level >= capacity * 0.92 or not pump_allowed:
            pump_on = False

        level = level - use * STEP_MINUTES + (pump_lpm * STEP_MINUTES if pump_on else 0.0)
        level = float(np.clip(level, 0.0, capacity))

        drop = 0.0
        if leaking:
            drop = 0.9 * min((i - leak_start) / 6.0, 1.0)
        elif pump_fail_start is not None and i >= pump_fail_start:
            drop = 0.15
        pressure_value = base_pressure - 0.18 * use / max(peak_flow, 1e-6) - drop + rng.normal(0, 0.03)
        if pump_on:
            pressure_value += 0.12
        tds = 0.97 * tds + 0.03 * base_tds + rng.normal(0, 1.2)

        flow[i] = use
        pressure[i] = max(pressure_value, 0.0)
        level_cm[i] = level / capacity * height
        tds_series[i] = max(tds, 0.0)
    return flow, pressure, level_cm, tds_series, critical_l
