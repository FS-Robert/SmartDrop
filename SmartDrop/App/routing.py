from django.urls import path

from .consumers import ReporteChatConsumer, SensorRealtimeConsumer

websocket_urlpatterns = [
    path('ws/sensors/', SensorRealtimeConsumer.as_asgi()),
    path('ws/chat/reporte/<int:reporte_id>/', ReporteChatConsumer.as_asgi()),
]

