from django.urls import path

from . import views_api, views_ops, views_web

app_name = 'ml_engine'

urlpatterns = [
    # Dashboard (Django templates, admin-only, sesión).
    path('admin/prediccion/', views_web.ml_dashboard, name='dashboard'),
    path('admin/prediccion/zona/<int:zone_id>/', views_web.ml_zone_detail, name='zone_detail'),
    path('admin/prediccion/fugas/', views_web.ml_leaks, name='leaks'),

    # API REST versionada, admin-only (JWT o sesión).
    path('v1/ml/zones/summary/', views_api.ZoneSummaryListView.as_view(), name='zone_summary'),
    path('v1/ml/zones/<int:zone_id>/current-status/', views_api.ZoneCurrentStatusView.as_view(), name='zone_current_status'),
    path('v1/ml/zones/<int:zone_id>/tank-trajectory/', views_api.ZoneTankTrajectoryView.as_view(), name='zone_tank_trajectory'),
    path('v1/ml/zones/<int:zone_id>/consumption-forecast/', views_api.ZoneConsumptionForecastView.as_view(), name='zone_consumption_forecast'),
    path('v1/ml/zones/<int:zone_id>/consumption-history/', views_api.ZoneConsumptionHistoryView.as_view(), name='zone_consumption_history'),
    path('v1/ml/zones/<int:zone_id>/shortage-prediction/', views_api.ZoneShortagePredictionView.as_view(), name='zone_shortage_prediction'),
    path('v1/ml/zones/<int:zone_id>/anomalies/', views_api.ZoneAnomaliesView.as_view(), name='zone_anomalies'),
    path('v1/ml/homes/<int:home_id>/consumption-forecast/', views_api.HomeConsumptionForecastView.as_view(), name='home_consumption_forecast'),
    path('v1/ml/homes/<int:home_id>/anomalies/', views_api.HomeAnomaliesView.as_view(), name='home_anomalies'),

    # Predicciones bajo demanda, fugas y avisos (web y app móvil, solo admin).
    path('v1/ml/predictions/run/', views_ops.PredictionRunView.as_view(), name='prediction_run'),
    path('v1/ml/predictions/status/', views_ops.PredictionStatusView.as_view(), name='prediction_status'),
    path('v1/ml/predictions/jobs/<str:job_id>/', views_ops.PredictionJobView.as_view(), name='prediction_job'),
    path('v1/ml/leaks/', views_ops.LeakOverviewView.as_view(), name='leak_overview'),
    path('v1/ml/leaks/alerts/', views_ops.LeakAlertsView.as_view(), name='leak_alerts'),
    path('v1/ml/leaks/alerts/read/', views_ops.LeakAlertsReadView.as_view(), name='leak_alerts_read'),
]
