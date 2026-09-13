from django.urls import path
from . import views

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
    
]