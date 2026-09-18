from django.urls import path

from .consumers import SensorRealtimeConsumer

websocket_urlpatterns = [
    path('ws/sensors/', SensorRealtimeConsumer.as_asgi()),
]
