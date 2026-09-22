"""Orquesta la generación sintética completa y la persiste vía el ORM.

Crea zonas y hogares sintéticos, simula consumo por hogar, agrega por zona,
corre el balance de masa del tanque, e inserta lecturas/agregados. Diseñado
para poder correr en SQLite (desarrollo) o Postgres (producción) sin cambios.
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from django.db import transaction
from django.utils import timezone as django_tz

from ml_engine.models import (
    ConsumptionAggregate,
    Home,
    SensorReading,
    SourceType,
    Zone,
)

from .config import ARCHETYPES, SimulationConfig
from .household import pick_archetype, simulate_home_hourly
from .zone import simulate_zone_tank

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000
_ARCHETYPE_CLUSTER_ID = {name: idx for idx, name in enumerate(ARCHETYPES.keys())}


def _make_aware(ts: pd.Timestamp) -> datetime:
    naive = ts.to_pydatetime()
    if django_tz.is_naive(naive):
        return django_tz.make_aware(naive, django_tz.get_default_timezone())
    return naive


def generate_dataset(config: SimulationConfig) -> dict:
    """Genera el dataset sintético completo según `config`. Devuelve un resumen."""
    rng = np.random.default_rng(config.seed)
    summary = {'zones': 0, 'homes': 0, 'readings': 0, 'aggregates': 0}

    for zone_idx in range(config.n_zones):
        with transaction.atomic():
            zone = Zone.objects.create(
                name=f'Zona sintética {zone_idx + 1}',
                source=SourceType.SYNTHETIC,
                capacidad_maxima_litros=config.tank_capacity_litros,
                nivel_critico_litros=config.tank_critical_litros,
                inflow_schedule={
                    'kind': config.inflow.kind,
                    'constant_lpm': config.inflow.constant_lpm,
                    'pump_hours': list(config.inflow.pump_hours),
                    'pump_rate_lpm': config.inflow.pump_rate_lpm,
                },
                nivel_actual_litros=config.tank_capacity_litros * 0.7,
            )
        summary['zones'] += 1

        home_frames: list[pd.DataFrame] = []
        for home_idx in range(config.homes_per_zone):
            home_seed = config.seed * 1000 + zone_idx * 100 + home_idx
            home_rng = np.random.default_rng(home_seed)
            archetype, occupants = pick_archetype(home_rng)
            home = Home.objects.create(
                zone=zone,
                source=SourceType.SYNTHETIC,
                archetype=archetype,
                ocupantes=occupants,
                cluster_id=_ARCHETYPE_CLUSTER_ID[archetype],
                etiqueta=f'Zona{zone_idx + 1}-Home{home_idx + 1} ({archetype})',
            )
            summary['homes'] += 1

            df = simulate_home_hourly(
                archetype=archetype,
                occupants=occupants,
                start_date=config.start_date,
                days=config.days,
                seed=home_seed,
                anomalies=config.anomalies,
            )
            df['home_id'] = home.id
            home_frames.append(df)
            _persist_home_readings(home.id, df)
            summary['readings'] += len(df) * 3
            summary['aggregates'] += len(df)

        # --- Balance de masa del tanque a nivel de zona ---
        zone_consumo = np.sum([f['litros'].to_numpy() for f in home_frames], axis=0)
        trajectory = simulate_zone_tank(
            zone_consumption_liters_per_hour=zone_consumo,
            initial_level_litros=zone.nivel_actual_litros,
            capacidad_maxima_litros=zone.capacidad_maxima_litros,
            inflow_cfg=config.inflow,
            seed=config.seed * 7919 + zone_idx,
        )
        ts_index = pd.date_range(start=config.start_date, periods=len(trajectory), freq='h')
        trajectory['ts'] = ts_index
        _persist_zone_tank_readings(zone.id, trajectory)
        zone.nivel_actual_litros = float(trajectory['nivel_litros'].iloc[-1])
        zone.save(update_fields=['nivel_actual_litros'])
        summary['readings'] += len(trajectory)

    return summary


def _persist_home_readings(home_id: int, df: pd.DataFrame) -> None:
    readings = []
    aggregates = []
    for row in df.itertuples(index=False):
        ts_aware = _make_aware(row.ts)
        readings.append(SensorReading(home_id=home_id, metric='flujo', ts=ts_aware, value=row.litros))
        readings.append(SensorReading(home_id=home_id, metric='presion', ts=ts_aware, value=row.presion))
        readings.append(SensorReading(home_id=home_id, metric='calidad', ts=ts_aware, value=row.calidad))
        aggregates.append(ConsumptionAggregate(
            home_id=home_id, period='hourly', period_start=ts_aware,
            litros=row.litros, presion_media=row.presion, calidad_media=row.calidad,
        ))
    SensorReading.objects.bulk_create(readings, batch_size=BATCH_SIZE)
    ConsumptionAggregate.objects.bulk_create(aggregates, batch_size=BATCH_SIZE, ignore_conflicts=True)


def _persist_zone_tank_readings(zone_id: int, trajectory: pd.DataFrame) -> None:
    readings = [
        SensorReading(zone_id=zone_id, metric='nivel_tanque', ts=_make_aware(row.ts), value=row.nivel_litros)
        for row in trajectory.itertuples(index=False)
    ]
    SensorReading.objects.bulk_create(readings, batch_size=BATCH_SIZE)
