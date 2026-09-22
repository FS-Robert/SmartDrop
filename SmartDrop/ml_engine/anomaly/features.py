"""Features de anomalías: residuales de presión/flujo por hogar (Sección 7).

Independiente del pipeline de forecasting multi-día: usa solo estadística
rodante de corto plazo (7 días) para reaccionar rápido a fugas/rupturas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml_engine.models import ConsumptionAggregate

ROLLING_WINDOW_HOURS = 24 * 7
RESIDUAL_COLUMNS = ['residual_litros', 'residual_presion', 'residual_calidad']


def build_residual_frame(home_ids: list[int] | None = None) -> pd.DataFrame:
    qs = ConsumptionAggregate.objects.filter(period='hourly')
    if home_ids is not None:
        qs = qs.filter(home_id__in=home_ids)
    rows = qs.values('home_id', 'period_start', 'litros', 'presion_media', 'calidad_media')
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return df
    df['period_start'] = pd.to_datetime(df['period_start'], utc=True)

    frames = []
    for home_id, df_home in df.groupby('home_id'):
        df_home = df_home.sort_values('period_start').set_index('period_start')
        for col, out in (('litros', 'residual_litros'), ('presion_media', 'residual_presion'), ('calidad_media', 'residual_calidad')):
            mean = df_home[col].rolling(ROLLING_WINDOW_HOURS, min_periods=24).mean()
            std = df_home[col].rolling(ROLLING_WINDOW_HOURS, min_periods=24).std().replace(0, np.nan)
            df_home[out] = (df_home[col] - mean) / std
        df_home['home_id'] = home_id
        frames.append(df_home.reset_index())

    full = pd.concat(frames).dropna(subset=RESIDUAL_COLUMNS)
    return full
