# ml_engine — Motor de predicción SmartDrop (Fase 1: Simulación)

Módulo Django **admin-only** (id_rol == 2, ver `App.api.permissions.IsAdministrator`
y `ml_engine/views_web.py`) que implementa la Fase 1 del sistema de forecasting
de consumo, simulación de tanque/predicción de desabasto, y detección de
fugas/anomalías descrito en el spec. Corre 100% con datos sintéticos y
entrenamiento local — sin depender de hardware real ni servicios en la nube.

## Qué NO incluye esta fase (a propósito)

- Ingesta real del ESP32 a este pipeline (Fase 2/3 del spec original).
- Notificaciones push (FCM/APNs) — explícitamente fuera de alcance por decisión del usuario.
- Entrenamiento en la nube / GPU — todo corre local con LightGBM + scikit-learn (CPU).

## Instalación

```bash
pip install -r requirements.txt
python manage.py migrate                      # tablas de Django (auth, sessions, etc.)
python manage.py migrate --database=timeseries # tablas de ml_engine
```

Por defecto el alias de base de datos `timeseries` apunta al mismo SQLite que
usa el resto del proyecto (cero infraestructura adicional para desarrollar y
entrenar localmente). Cuando quieras moverlo a tu Supabase Postgres (con
`pg_partman`, como mencionaste tener habilitado), define en tu `.env`:

```
ML_TIMESERIES_DB_HOST=<host de tu Supabase Postgres>
ML_TIMESERIES_DB_PORT=5432
ML_TIMESERIES_DB_NAME=postgres
ML_TIMESERIES_DB_USER=postgres
ML_TIMESERIES_DB_PASSWORD=<tu password>   # nunca la pegues en el chat
```

No hace falta tocar ninguna línea de `ml_engine`: el router
(`ml_engine/routers.py`) ya envía todos sus modelos a ese alias. Luego, con la
conexión ya apuntando a Postgres:

```bash
pip install psycopg2-binary
python manage.py migrate --database=timeseries
python manage.py ml_setup_partitioning   # registra ml_sensor_reading en pg_partman (particiones mensuales)
```

`ml_setup_partitioning` detecta si el alias no es Postgres y no hace nada en
ese caso (seguro de correr siempre).

## Correr el pipeline completo (Fase 1)

```bash
python manage.py ml_run_full_pipeline
```

Esto: genera datos sintéticos (2 zonas × 15 hogares × 60 días por defecto),
entrena el modelo de consumo (LightGBM cuantílico), genera pronósticos,
entrena el detector de anomalías, corre la detección, y corre la simulación
Monte Carlo de balance de masa + predicción de desabasto por zona.

Comandos individuales (todos con `python manage.py <comando>`):

| Comando | Qué hace |
|---|---|
| `ml_generate_synthetic_data` | Genera hogares/zonas sintéticos + historial horario. Flags: `--zones --homes-per-zone --days --start-date --seed` |
| `ml_train_consumption_model` | Entrena LightGBM cuantílico (p10/p50/p90), split temporal, guarda `ModelArtifact` |
| `ml_predict_consumption` | Genera pronóstico de la próxima hora para todos los hogares activos |
| `ml_train_anomaly_model` | Entrena Isolation Forest sobre residuales de presión/flujo/calidad |
| `ml_run_anomaly_detection` | Corre el detector sobre los datos recientes y registra `AnomalyEvent` |
| `ml_run_shortage_prediction` | Monte Carlo de balance de masa + first-passage-time por zona. Flags: `--zone-id --horizon-hours --n-paths` |
| `ml_setup_partitioning` | (Opcional, solo Postgres) registra la tabla de lecturas en pg_partman |

## Dashboard (solo admin)

- Web: `/admin/prediccion/` y `/admin/prediccion/zona/<id>/` (requiere sesión
  con `rol_id == 2`; usuarios normales son redirigidos al dashboard normal).
- API REST versionada (JWT o sesión, ambas admin-only):
  - `GET /v1/ml/homes/{id}/consumption-forecast/`
  - `GET /v1/ml/zones/{id}/tank-trajectory/`
  - `GET /v1/ml/zones/{id}/shortage-prediction/` (`shortage_prediction: null` si no hay riesgo en el horizonte)
  - `GET /v1/ml/homes/{id}/anomalies/` y `GET /v1/ml/zones/{id}/anomalies/`
  - `GET /v1/ml/zones/{id}/current-status/`
  - `GET /v1/ml/zones/summary/` (agregado multi-zona para dashboards)

## Arquitectura: por qué es agnóstica a real-vs-sintético

`Home` y `Zone` tienen un campo `source` (`synthetic` | `real`) y un
`source_ref_*_id` opcional que apunta a las tablas reales (`vivienda`,
`tanque`) del sistema principal. Todo lo demás — ingestión (`SensorReading`),
features (`ml_engine/features/build_features.py`), el modelo, la simulación
de tanque y la API — no distinguen el origen: leen/escriben exactamente igual
sin importar si los datos vinieron del generador o de un ESP32 real.

### Cómo migrar un hogar sintético a un dispositivo real (Fase 2/3)

1. Crear un `Home` con `source='real'` y `source_ref_vivienda_id=<id_vivienda real>`.
2. Apuntar la ingesta real (hoy vía MQTT + `App/mqtt_service.py`) a insertar
   en `ml_engine.SensorReading` con `home=<ese Home>` en vez de (o además de)
   la tabla `lectura` de producción — un pequeño adaptador de ingesta, sin
   tocar features/modelo/simulación/API.
3. Marcar el `Home` sintético equivalente como `activo=False` (se conserva su
   historial para no perder continuidad de entrenamiento) o borrarlo si ya no
   se necesita.
4. Repetir por hogar a medida que se despliegue hardware — nunca hace falta
   tocar `ml_engine/features`, `ml_engine/forecasting`, `ml_engine/shortage`
   ni `ml_engine/anomaly`.

## Limitaciones conocidas de la Fase 1 (documentadas, no ocultas)

- **Horizonte del modelo de consumo**: el LightGBM predice un solo paso
  (t+1h). La predicción de desabasto a 72h extiende ese pronóstico usando el
  perfil histórico por hora-del-día de cada hogar, escalado por la razón
  p10/p50/p90 calibrada del último pronóstico — es una aproximación razonable
  para validar el pipeline end-to-end, no un modelo multi-horizonte nativo.
  Upgrade natural: recursive forecasting real o un modelo directo
  multi-horizonte (LSTM/TFT, ver Sección 5 "upgrade path" del spec original).
- **Resolución temporal**: agregados horarios (no sub-hora). El simulador de
  eventos sí modela ráfagas realistas dentro de cada hora (suma de eventos
  discretos), pero no persiste la traza a nivel de minuto.
- **SHAP** es opcional (`shap` puede fallar de instalar en algunos entornos);
  si no está disponible, `explanation` queda vacío en vez de romper la API.
- Sin notificaciones push (fuera de alcance explícito de esta fase).
