from django.urls import path
from . import views
from . import views_reportes

urlpatterns = [
    path('admin-panel/',        views.admin_panel,        name='admin_panel'),
    path('sensor/<str:sensor_id>/', views.sensor_detail,    name='sensor_detail'),
    path('sensor/<str:sensor_id>/data/', views.sensor_data, name='sensor_data'),
    path('',                    views.dashboard,          name='dashboard'),
    path('estado-sistema/',     views.estado_sistema,      name='estado_sistema'),
    path('login/',              views.login_view,         name='login'),
    path('register/',           views.register,           name='register'),
    path('logout/',             views.logout_view,        name='logout'),
    path('buscar/',             views.busqueda_global_view, name='busqueda_global'),
    path('api/register/',       views.api_register,       name='api_register'),
    path('api/login/',          views.api_login,          name='api_login'),
    path('api/lecturas/',       views.api_lectura,        name='api_lectura'),
    path('api/valvula/estado/', views.api_estado_valvula, name='api_estado_valvula'),
    path('tanque/',             views.tanque,             name='tanque'),
    path('calidad/',            views.calidad,            name='calidad'),
    path('presion/',            views.presion,            name='presion'),
    path('consumo/',            views.consumo,            name='consumo'),
    path('retroalimentacion/',  views.retroalimentacion,  name='retroalimentacion'),
    path('recomendaciones/',    views.recomendaciones,    name='recomendaciones'),
    path('valvulas/',           views.valvulas,           name='valvulas'),
    path('valvula/<str:valvula_id>/comando/', views.valvula_comando, name='valvula_comando'),
    path('usuario/',            views.usuario,            name='usuario'),
    path('vincular-vivienda/',  views.vincular_vivienda,  name='vincular_vivienda'),

    # ── Sistema de reportes de usuarios ──
    path('reportar/',           views_reportes.reportes_panel,      name='reportes_panel'),
    path('reportes/<int:reporte_id>/', views_reportes.reporte_detalle, name='reporte_detalle'),
    path('reportes/<int:reporte_id>/adjuntar/', views_reportes.reporte_adjuntar, name='reporte_adjuntar'),
    path('reportes/<int:reporte_id>/mensajes/', views_reportes.reporte_mensajes_json, name='reporte_mensajes_json'),
    path('comunidad/',          views_reportes.reportes_comunidad,  name='reportes_comunidad'),
    path('alertas/',            views_reportes.alertas_historial,   name='alertas_historial'),
    path('alertas/export/',     views_reportes.alertas_export,      name='alertas_export'),
    path('admin-reportes/',     views_reportes.admin_reportes,      name='admin_reportes'),
    path('admin-reportes/export/', views_reportes.admin_reportes_export, name='admin_reportes_export'),
    path('admin-reportes/<int:reporte_id>/actualizar/', views_reportes.admin_reporte_update, name='admin_reporte_update'),
    
]