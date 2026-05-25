from django.urls import path
from . import views

urlpatterns = [
    path('',                    views.dashboard,          name='dashboard'),
    path('login/',              views.login_view,         name='login'),
    path('register/',           views.register,           name='register'),
    path('logout/',             views.logout_view,        name='logout'),
    path('tanque/',             views.tanque,             name='tanque'),
    path('calidad/',            views.calidad,            name='calidad'),
    path('presion/',            views.presion,            name='presion'),
    path('consumo/',            views.consumo,            name='consumo'),
    path('retroalimentacion/',  views.retroalimentacion,  name='retroalimentacion'),
    path('recomendaciones/',    views.recomendaciones,    name='recomendaciones'),
    path('usuario/',            views.usuario,            name='usuario'),
]