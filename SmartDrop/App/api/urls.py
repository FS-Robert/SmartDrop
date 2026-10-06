from django.urls import path

from .views_auth import MobileLoginView, MobileMeView, MobileRefreshView, MobileRegisterView
from .views_mobile_data import (
    MobileEstadoAguaView,
    MobileGraficasView,
    MobileResumenView,
    MobileVincularViviendaView,
    MobileViviendasView,
)
from .views_valves import MobileValvulaCommandView, MobileValvulaEstadoView, MobileValvulaLogsView
from .views_mobile_consumption import (
    MobileConsumoView,
    MobileRecomendacionesView,
    MobileRetroalimentacionView,
)
from .views_mobile_search import MobileBusquedaGlobalView
from .views_mobile_reports import (
    MobileReporteAdjuntarView,
    MobileReporteDetalleView,
    MobileReportesCatalogoView,
    MobileReportesComunidadView,
    MobileReportesView,
)

urlpatterns = [
    path('auth/registro/', MobileRegisterView.as_view(), name='mobile_register'),
    path('auth/login/', MobileLoginView.as_view(), name='mobile_login'),
    path('auth/me/', MobileMeView.as_view(), name='mobile_me'),
    path('auth/refresh/', MobileRefreshView.as_view(), name='mobile_refresh'),
    path('auth/mis-viviendas/', MobileViviendasView.as_view(), name='mobile_viviendas'),
    path('auth/vincular-vivienda/', MobileVincularViviendaView.as_view(), name='mobile_vincular_vivienda'),
    path('auth/estado-agua/', MobileEstadoAguaView.as_view(), name='mobile_estado_agua'),
    path('auth/consumo/', MobileConsumoView.as_view(), name='mobile_consumo'),
    path('auth/retroalimentacion/', MobileRetroalimentacionView.as_view(), name='mobile_retroalimentacion'),
    path('auth/recomendaciones/', MobileRecomendacionesView.as_view(), name='mobile_recomendaciones'),
    path('api/graficas/', MobileGraficasView.as_view(), name='mobile_graficas'),
    path('api/resumen/', MobileResumenView.as_view(), name='mobile_resumen'),
    path('api/valvula/<int:id_valvula>/estado/', MobileValvulaEstadoView.as_view(), name='mobile_valvula_estado'),
    path('api/valvula/<int:id_valvula>/logs/', MobileValvulaLogsView.as_view(), name='mobile_valvula_logs'),
    path('api/valvula/<int:id_valvula>/<str:action>/', MobileValvulaCommandView.as_view(), name='mobile_valvula_command'),
    path('api/buscar/', MobileBusquedaGlobalView.as_view(), name='mobile_buscar'),
    path('api/reportes/', MobileReportesView.as_view(), name='mobile_reportes'),
    path('api/reportes/catalogo/', MobileReportesCatalogoView.as_view(), name='mobile_reportes_catalogo'),
    path('api/reportes/comunidad/', MobileReportesComunidadView.as_view(), name='mobile_reportes_comunidad'),
    path('api/reportes/<int:reporte_id>/', MobileReporteDetalleView.as_view(), name='mobile_reporte_detalle'),
    path('api/reportes/<int:reporte_id>/adjuntar/', MobileReporteAdjuntarView.as_view(), name='mobile_reporte_adjuntar'),
]
