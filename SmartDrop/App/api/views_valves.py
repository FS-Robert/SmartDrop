from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client
from ..mqtt_service import MqttError, publish_command
from .permissions import IsAdministrator, IsAuthenticatedUser


class MobileValvulaEstadoView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, id_valvula):
        rows = supabase_client.select(
            'valvula', '*', {'id_valvula': f'eq.{id_valvula}', 'limit': '1'}
        )
        if not rows:
            return Response({'error': 'Válvula no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(rows[0])


class MobileValvulaCommandView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, id_valvula, action):
        action = action.replace('-remoto', '')
        if action not in ('abrir', 'cerrar'):
            return Response({'error': 'Comando inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        rows = supabase_client.select(
            'valvula',
            'id_valvula,estado_actual,topic_mqtt_comando',
            {'id_valvula': f'eq.{id_valvula}', 'limit': '1'},
        )
        if not rows or not rows[0].get('topic_mqtt_comando'):
            return Response({'error': 'Válvula no encontrada o sin topic MQTT.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            publish_command(rows[0]['topic_mqtt_comando'], action)
            state = 'abierta' if action == 'abrir' else 'cerrada'
            supabase_client.update(
                'valvula',
                {'estado_actual': state},
                {'id_valvula': f'eq.{id_valvula}'},
            )
        except MqttError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'ok': True, 'estado_actual': state})
