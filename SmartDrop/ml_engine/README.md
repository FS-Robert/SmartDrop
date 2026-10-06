# ml_engine — Motor de predicción SmartDrop

Módulo Django **solo para administradores** (`id_rol == 2`) que predice el desabasto de cada tanque y
detecta posibles fugas a partir de las **lecturas reales guardadas en Supabase** (tabla `lectura`).
El entrenamiento y la inferencia corren en local (LightGBM + scikit-learn, CPU).

## Cómo funciona

1. **Sincronización** (`realdata/sync.py`). Cada tanque de Supabase es una `Zone` y cada vivienda un `Home`.
   Un sensor pertenece a la vivienda que registró su lectura más reciente. Las lecturas se copian de forma
   incremental a `ml_timeseries.sqlite3` y se resumen por hora; el nivel (cm) se convierte a litros con la
   altura y la capacidad del tanque. La primera vez se traen los últimos 30 días.
2. **Calidad de datos**. Una vivienda entra al análisis solo si su última lectura tiene menos de 6 h, sus
   lecturas varían y acumula al menos 48 h de historial. Las demás se listan como excluidas, con el motivo.
3. **Modelos** (`training.py`). Se entrenan con las viviendas aptas cuando no existen o tienen más de 7 días:
   LightGBM cuantílico (consumo p10/p50/p90) e Isolation Forest (anomalías). Se guardan en `ml_models/`.
4. **Desabasto** (`shortage/`). Monte Carlo de 72 h por tanque: consumo previsto de la vivienda y una bomba por
   umbral cuyos niveles de arranque y paro se estiman del historial. Si el nivel ya está bajo el umbral de
   arranque y no sube, se asume que la bomba no está recargando. El resultado se guarda también en la tabla
   `prediccion_desabasto` de Supabase.
5. **Fugas** (`leaks/`). Por vivienda se combinan cuatro señales: flujo mínimo sostenido en 6 h, caída de
   presión, flujo inusual para la hora y el score del Isolation Forest. Si la probabilidad supera el umbral se
   crea una fila en `fuga`, una `alerta` con todos los detalles y una `notificacion` para cada administrador.

## Uso desde el panel (solo admin)

- **Predicción Suministro** (`/admin/prediccion/`): botón **Realizar predicciones**. Lanza los cinco pasos en
  segundo plano y muestra una pantalla de carga con el progreso solo si tarda más de 1 s.
- **Predicción de fugas** (`/admin/prediccion/fugas/`): estado por vivienda, avisos y estado del monitor.
- **Avisos automáticos**: la campana del encabezado muestra los avisos sin leer y cada aviso nuevo aparece
  como tarjeta emergente en cualquier página del panel. La app Android los muestra como notificación del sistema.

## Monitor automático de fugas

Al arrancar el servidor (`runserver`, `daphne`, etc.) se inicia un hilo que cada 10 min sincroniza las lecturas
nuevas, reentrena si hace falta y analiza fugas. No repite el aviso de una misma vivienda durante 12 h.

El hilo solo vive mientras el servidor está encendido. Para tenerlo aparte:

```bash
python manage.py ml_run_monitor --loop   # proceso propio, repite cada ML_MONITOR_INTERVAL_MINUTES
python manage.py ml_run_monitor          # una sola pasada (p. ej. desde el Programador de tareas de Windows)
```

Variables opcionales en `.env`:

| Variable | Por defecto | Qué controla |
|---|---|---|
| `ML_MONITOR_ENABLED` | `1` | `0` desactiva el hilo del servidor |
| `ML_MONITOR_INTERVAL_MINUTES` | `10` | Cada cuánto se revisan fugas (mínimo 1) |
| `ML_MONITOR_START_DELAY_SECONDS` | `45` | Espera tras arrancar el servidor |
| `ML_LEAK_ALERT_THRESHOLD` | `0.6` | Probabilidad a partir de la cual se avisa |
| `ML_LEAK_ALERT_COOLDOWN_HOURS` | `12` | Horas sin repetir el aviso de una vivienda |

## Instalación

```bash
pip install -r requirements.txt
python manage.py migrate                        # tablas de Django
python manage.py migrate --database=timeseries  # tablas de ml_engine
```

El alias `timeseries` es por defecto un SQLite local. Para moverlo a Postgres define `ML_TIMESERIES_DB_HOST`,
`_PORT`, `_NAME`, `_USER` y `_PASSWORD` en `.env`, instala `psycopg2-binary`, migra ese alias y, si usas
pg_partman, ejecuta `python manage.py ml_setup_partitioning`.

## Comandos

| Comando | Qué hace |
|---|---|
| `ml_run_full_pipeline` | Lo mismo que el botón Realizar predicciones, desde la consola |
| `ml_run_monitor` | Una pasada del monitor de fugas (`--loop` para repetir) |
| `ml_train_consumption_model` / `ml_train_anomaly_model` | Fuerzan el entrenamiento de cada modelo |
| `ml_predict_consumption` | Pronóstico de la próxima hora por vivienda |
| `ml_run_shortage_prediction` | Predicción de desabasto. Flags: `--zone-id --horizon-hours --n-paths` |
| `ml_run_anomaly_detection` | Registra `AnomalyEvent` con el Isolation Forest |
| `ml_seed_demo_readings` | Escribe en Supabase lecturas demo realistas para las viviendas de `--viviendas 9,10,...` (`--dry-run` para ver antes) |
| `ml_purge_synthetic` | Limpia de una base antigua los hogares sintéticos y los modelos entrenados con ellos |

## API REST (JWT de la app o sesión web, solo admin)

- `POST /v1/ml/predictions/run/` — lanza la ejecución y devuelve el job (`job_id`, `status`, `progress`, `step`)
- `GET /v1/ml/predictions/jobs/{id}/` — progreso y, al terminar, el resultado
- `GET /v1/ml/predictions/status/` — última ejecución manual y estado del monitor
- `GET /v1/ml/zones/summary/` — un tanque con su vivienda por fila
- `GET /v1/ml/zones/{id}/current-status/`, `tank-trajectory/`, `shortage-prediction/`, `consumption-forecast/`, `anomalies/`
- `GET /v1/ml/homes/{id}/consumption-forecast/`, `anomalies/`
- `GET /v1/ml/leaks/` — última predicción de fuga por vivienda
- `GET /v1/ml/leaks/alerts/` — avisos de fuga (`?solo_no_leidas=1`, `?desde_id=N`)
- `POST /v1/ml/leaks/alerts/read/` — `{"ids": [...]}` o `{"todas": true}`

## Pruebas

```bash
python manage.py test ml_engine
```

Usan un Supabase simulado en memoria; no tocan la base real.

## Limitaciones conocidas

- El modelo de consumo predice una hora; el horizonte de 72 h extiende ese pronóstico con el perfil histórico
  por hora del día de cada vivienda.
- Los agregados son horarios: una fuga tarda varias horas de flujo sostenido en superar el umbral.
- Con riesgo bajo no se informa de horas hasta el desabasto: saldrían de unas pocas trayectorias extremas.
- El servidor se asume en un solo proceso (hilo del monitor y ejecución de predicciones).
- SHAP es opcional; si falla, `explanation` queda vacío.
