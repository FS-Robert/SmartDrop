"""Feature engineering compartido entre entrenamiento e inferencia (Sección 5).

Un único camino de código construye las features tanto para entrenar el
modelo global de consumo como para generar predicciones en producción, de
forma que no haya "train/serve skew".

Manejo de datos faltantes (Sección 8): cada hogar se reindexa a una grilla
horaria continua; huecos cortos (<=3h) se interpolan linealmente, huecos más
largos quedan como NaN y se marcan `sensor_offline=True` en vez de corromper
silenciosamente los agregados/rolling stats.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml_engine.models import ConsumptionAggregate, Home

LAGS_HOURS = (1, 24, 168)  # 1h, mismo-hora-ayer, mismo-hora-semana-pasada
ROLLING_WINDOWS_HOURS = {'7d': 24 * 7, '30d': 24 * 30}
MAX_INTERPOLATION_GAP_HOURS = 3

FEATURE_COLUMNS = [
    'lag_1h', 'lag_24h', 'lag_168h',
    'roll_mean_7d', 'roll_std_7d', 'roll_mean_30d', 'roll_std_30d',
    'presion_media_lag1', 'calidad_media_lag1',
    'hour', 'dow', 'month', 'is_weekend',
    'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos',
    'sensor_offline',
]
CATEGORICAL_COLUMNS = ['home_id', 'cluster_id']


def _load_history_dataframe(home_ids: list[int] | None = None) -> pd.DataFrame:
    qs = ConsumptionAggregate.objects.filter(period='hourly').select_related('home')
    if home_ids is not None:
        qs = qs.filter(home_id__in=home_ids)
    rows = qs.values('home_id', 'period_start', 'litros', 'presion_media', 'calidad_media', 'home__cluster_id')
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return df
    df = df.rename(columns={'home__cluster_id': 'cluster_id'})
    df['period_start'] = pd.to_datetime(df['period_start'], utc=True)
    return df


def _reindex_home(df_home: pd.DataFrame) -> pd.DataFrame:
    df_home = df_home.set_index('period_start').sort_index()
    full_index = pd.date_range(df_home.index.min(), df_home.index.max(), freq='h')
    reindexed = df_home.reindex(full_index)
    reindexed['sensor_offline'] = reindexed['litros'].isna()
    reindexed['litros'] = reindexed['litros'].interpolate(
        method='linear', limit=MAX_INTERPOLATION_GAP_HOURS, limit_area='inside',
    )
    reindexed['presion_media'] = reindexed['presion_media'].ffill(limit=MAX_INTERPOLATION_GAP_HOURS)
    reindexed['calidad_media'] = reindexed['calidad_media'].ffill(limit=MAX_INTERPOLATION_GAP_HOURS)
    reindexed['home_id'] = reindexed['home_id'].ffill().bfill()
    reindexed['cluster_id'] = reindexed['cluster_id'].ffill().bfill()
    return reindexed


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    df['hour'] = idx.hour
    df['dow'] = idx.dayofweek
    df['month'] = idx.month
    df['is_weekend'] = (idx.dayofweek >= 5).astype(int)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['dow_sin'] = np.sin(2 * np.pi * df['dow'] / 7)
    df['dow_cos'] = np.cos(2 * np.pi * df['dow'] / 7)
    return df


def _add_lag_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    for lag in LAGS_HOURS:
        df[f'lag_{lag}h'] = df['litros'].shift(lag)
    shifted = df['litros'].shift(1)  # solo usar info estrictamente anterior a t
    for label, window in ROLLING_WINDOWS_HOURS.items():
        df[f'roll_mean_{label}'] = shifted.rolling(window, min_periods=max(4, window // 20)).mean()
        df[f'roll_std_{label}'] = shifted.rolling(window, min_periods=max(4, window // 20)).std()
    df['presion_media_lag1'] = df['presion_media'].shift(1)
    df['calidad_media_lag1'] = df['calidad_media'].shift(1)
    return df


def build_training_frame(home_ids: list[int] | None = None) -> pd.DataFrame:
    """Construye el dataset completo de entrenamiento (features + target `y`)."""
    raw = _load_history_dataframe(home_ids)
    if raw.empty:
        return raw

    frames = []
    for home_id, df_home in raw.groupby('home_id'):
        reindexed = _reindex_home(df_home.drop(columns=['home_id']).assign(home_id=home_id))
        reindexed = _add_lag_rolling_features(reindexed)
        reindexed = _add_calendar_features(reindexed)
        reindexed['y'] = reindexed['litros']
        frames.append(reindexed)

    full = pd.concat(frames).reset_index().rename(columns={'index': 'period_start'})
    required = FEATURE_COLUMNS + ['y']
    full = full.dropna(subset=[c for c in required if c not in ('sensor_offline',)])
    full['home_id'] = full['home_id'].astype('category')
    full['cluster_id'] = full['cluster_id'].astype('category')
    full['sensor_offline'] = full['sensor_offline'].astype(int)
    return full


def build_inference_frame(home_ids: list[int]) -> pd.DataFrame:
    """Construye una fila de features por hogar para predecir la hora siguiente.

    Usa el historial disponible hasta el último dato conocido y agrega una
    fila "virtual" (t = último_conocido + 1h) para calcular lags/rolling que
    solo dependen de información pasada — igual que en entrenamiento.
    """
    raw = _load_history_dataframe(home_ids)
    if raw.empty:
        return raw

    rows = []
    for home_id, df_home in raw.groupby('home_id'):
        reindexed = _reindex_home(df_home.drop(columns=['home_id']).assign(home_id=home_id))
        next_ts = reindexed.index.max() + pd.Timedelta(hours=1)
        virtual = pd.DataFrame(
            {'litros': [np.nan], 'presion_media': [np.nan], 'calidad_media': [np.nan],
             'sensor_offline': [False], 'home_id': [home_id], 'cluster_id': [reindexed['cluster_id'].iloc[-1]]},
            index=[next_ts],
        )
        extended = pd.concat([reindexed, virtual])
        extended = _add_lag_rolling_features(extended)
        extended = _add_calendar_features(extended)
        last_row = extended.iloc[[-1]].reset_index().rename(columns={'index': 'period_start'})
        rows.append(last_row)

    full = pd.concat(rows).reset_index(drop=True)
    full['home_id'] = full['home_id'].astype('category')
    full['cluster_id'] = full['cluster_id'].astype('category')
    full['sensor_offline'] = full['sensor_offline'].fillna(False).astype(int)
    return full
