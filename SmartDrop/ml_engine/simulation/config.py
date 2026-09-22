"""Configuración y catálogos del generador sintético (Sección 4 del spec).

El simulador es un modelo de eventos de uso estocástico (enfoque "stochastic
end-use", en la línea de SIMDEUM): cada hogar sintético tiene ocupantes que
generan eventos de uso de agua (WC, ducha, grifo, lavadora, riego) con
frecuencia, duración y caudal aleatorios que varían según la hora del día y
el arquetipo del hogar. La suma de esos eventos por hora produce un consumo
agregado realista y "bursty" (no una curva suavizada).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApplianceProfile:
    """Distribución de un tipo de uso de agua (un "end-use")."""

    name: str
    # Usos esperados por ocupante por día (antes de aplicar el perfil horario).
    uses_per_occupant_day: float
    duration_seconds_mean: float
    duration_seconds_sigma: float  # sigma de la lognormal
    flow_lpm_mean: float
    flow_lpm_sigma: float
    # Peso relativo de probabilidad por hora del día (24 valores, se normaliza solo).
    hourly_weights: tuple = field(default_factory=lambda: tuple([1.0] * 24))

    def liters_per_event(self, rng) -> float:
        duration_s = max(5.0, rng.lognormal(
            mean=_log_mean(self.duration_seconds_mean, self.duration_seconds_sigma),
            sigma=_log_sigma(self.duration_seconds_mean, self.duration_seconds_sigma),
        ))
        flow_lpm = max(0.5, rng.normal(self.flow_lpm_mean, self.flow_lpm_sigma))
        return (duration_s / 60.0) * flow_lpm


def _log_mean(mean: float, sigma: float) -> float:
    import math
    return math.log(mean**2 / math.sqrt(sigma**2 + mean**2))


def _log_sigma(mean: float, sigma: float) -> float:
    import math
    return math.sqrt(math.log(1 + (sigma**2 / mean**2)))


# Picos matutino (6-9h) y vespertino (18-22h), valle nocturno (0-5h).
_MORNING_EVENING_PEAK = (
    0.2, 0.1, 0.1, 0.1, 0.2, 0.6,   # 0-5
    1.8, 2.2, 1.6, 1.0, 0.8, 0.9,   # 6-11
    1.1, 0.9, 0.7, 0.7, 0.8, 1.2,   # 12-17
    1.9, 2.0, 1.5, 1.0, 0.6, 0.3,   # 18-23
)
_DAYTIME_FLAT = (
    0.1, 0.1, 0.1, 0.1, 0.1, 0.3,
    0.8, 1.0, 1.2, 1.3, 1.3, 1.2,
    1.2, 1.2, 1.1, 1.1, 1.0, 0.9,
    0.9, 0.8, 0.6, 0.4, 0.2, 0.1,
)
_IRRIGATION_EARLY = (
    0.0, 0.0, 0.0, 0.0, 2.5, 3.0,
    2.0, 0.5, 0.1, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.2, 1.0,
    2.0, 1.5, 0.3, 0.0, 0.0, 0.0,
)

APPLIANCES: dict[str, ApplianceProfile] = {
    'toilet': ApplianceProfile('toilet', 5.0, 6.0, 2.0, 8.0, 1.0, _MORNING_EVENING_PEAK),
    'shower': ApplianceProfile('shower', 0.9, 420.0, 180.0, 9.0, 1.5, _MORNING_EVENING_PEAK),
    'tap': ApplianceProfile('tap', 8.0, 45.0, 20.0, 4.0, 1.0, _DAYTIME_FLAT),
    'laundry': ApplianceProfile('laundry', 0.35, 1800.0, 400.0, 10.0, 1.0, _DAYTIME_FLAT),
    'dishwasher': ApplianceProfile('dishwasher', 0.3, 1200.0, 300.0, 6.0, 0.8, _DAYTIME_FLAT),
    'irrigation': ApplianceProfile('irrigation', 0.15, 900.0, 300.0, 15.0, 3.0, _IRRIGATION_EARLY),
}


@dataclass(frozen=True)
class ArchetypeProfile:
    """Multiplicadores por arquetipo de hogar sobre los perfiles base."""

    occupants_range: tuple[int, int]
    appliance_multipliers: dict[str, float]


ARCHETYPES: dict[str, ArchetypeProfile] = {
    'single': ArchetypeProfile((1, 1), {'toilet': 0.6, 'shower': 0.7, 'tap': 0.6, 'laundry': 0.5, 'dishwasher': 0.4, 'irrigation': 0.1}),
    'couple': ArchetypeProfile((2, 2), {'toilet': 0.9, 'shower': 0.9, 'tap': 0.8, 'laundry': 0.8, 'dishwasher': 0.7, 'irrigation': 0.3}),
    'family_small': ArchetypeProfile((3, 4), {'toilet': 1.0, 'shower': 1.0, 'tap': 1.0, 'laundry': 1.0, 'dishwasher': 1.0, 'irrigation': 0.4}),
    'family_large': ArchetypeProfile((5, 7), {'toilet': 1.2, 'shower': 1.2, 'tap': 1.3, 'laundry': 1.4, 'dishwasher': 1.3, 'irrigation': 0.5}),
    'irrigation_heavy': ArchetypeProfile((3, 5), {'toilet': 1.0, 'shower': 1.0, 'tap': 1.0, 'laundry': 1.0, 'dishwasher': 0.9, 'irrigation': 3.0}),
    'home_office': ArchetypeProfile((2, 3), {'toilet': 1.1, 'shower': 1.0, 'tap': 1.3, 'laundry': 1.0, 'dishwasher': 1.0, 'irrigation': 0.2}),
    'low_occupancy': ArchetypeProfile((1, 2), {'toilet': 0.3, 'shower': 0.3, 'tap': 0.3, 'laundry': 0.2, 'dishwasher': 0.1, 'irrigation': 0.05}),
}


@dataclass
class AnomalyInjectionConfig:
    """Probabilidad diaria (por hogar/zona) de inyectar cada tipo de anomalía."""

    leak_prob_per_home_day: float = 0.002
    burst_prob_per_home_day: float = 0.0005
    vacation_dip_prob_per_home_day: float = 0.001
    quality_incident_prob_per_home_day: float = 0.0008
    pump_failure_prob_per_zone_day: float = 0.001


@dataclass
class InflowScheduleConfig:
    """Esquema de entrada de agua al tanque de una zona."""

    kind: str = 'scheduled_pump'  # 'constant' | 'scheduled_pump'
    constant_lpm: float = 40.0
    pump_hours: tuple = (5, 6, 13, 14, 19, 20)
    pump_rate_lpm: float = 250.0


@dataclass
class SimulationConfig:
    n_zones: int = 2
    homes_per_zone: int = 15
    start_date: str = '2026-01-01'
    days: int = 60
    seed: int = 42
    anomalies: AnomalyInjectionConfig = field(default_factory=AnomalyInjectionConfig)
    inflow: InflowScheduleConfig = field(default_factory=InflowScheduleConfig)
    tank_capacity_litros: float = 12_000.0
    tank_critical_litros: float = 1_800.0
