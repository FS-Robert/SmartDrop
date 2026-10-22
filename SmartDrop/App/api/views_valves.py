import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client
from ..mqtt_service import MqttError, publish_command
from ..queries import is_admin, owner_id, users_by_id
from .permissions import IsAdministrator, IsAuthenticatedUser


logger = logging.getLogger(__name__)


# Lo que un usuario sin rol de admin puede ver de una válvula: el topic MQTT y el pin del ESP32 son
# datos internos del sistema de control y solo los ve el admin.
CAMPOS_VALVULA_PUBLICOS = 'id_valvula,nombre,estado_actual,ultima_apertura'


class MobileValvulaEstadoView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, id_valvula):
        columnas = '*' if is_admin(request.user) else CAMPOS_VALVULA_PUBLICOS
        try:
            rows = supabase_client.select(
                'valvula', columnas, {'id_valvula': f'eq.{id_valvula}', 'limit': '1'}
            )
        except supabase_client.SupabaseError:
            return Response({'error': 'No se pudo consultar la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        if not rows:
            return Response({'error': 'Válvula no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(rows[0])


class MobileValvulaLogsView(APIView):
    permission_classes = [IsAdministrator]

    def get(self, request, id_valvula):
        try:
            valves = supabase_client.select(
                'valvula', 'id_valvula',
                {'id_valvula': f'eq.{id_valvula}', 'limit': '1'},
            )
            if not valves:
                return Response(
                    {'error': 'Válvula no encontrada.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            movements = supabase_client.select(
                'log_valvula',
                'id_valvula,accion,estado_anterior,estado_nuevo,tipo_activacion,'
                'id_usuario,fecha_hora,origen_accion,duracion_programada',
                {
                    'id_valvula': f'eq.{id_valvula}',
                    'order': 'fecha_hora.desc',
                    'limit': '50',
                },
            )
            users = users_by_id(
                (row.get('id_usuario') for row in movements), 'id_usuario,nombre,apellido,correo',
            ).values()
        except Exception:
            logger.exception('No se pudo cargar el historial móvil de la válvula %s', id_valvula)
            return Response(
                {'error': 'No se pudo cargar el historial de la válvula.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        user_names = {
            str(user['id_usuario']): (
                f"{user.get('nombre', '')} {user.get('apellido', '')}".strip()
                or user.get('correo', '')
            )
            for user in users
            if user.get('id_usuario') is not None
        }
        logs = []
        for movement in movements:
            activation_type = str(movement.get('tipo_activacion') or '').strip().lower()
            is_automatic = activation_type in {'automatico', 'automática', 'automático', 'auto'}
            is_timed = activation_type == 'temporizado'
            user_id = movement.get('id_usuario')
            duration = movement.get('duracion_programada')

            logs.append({
                'accion': str(movement.get('accion') or '').upper(),
                'detalle': str(duration) if is_timed and duration is not None else '0',
                'fecha_hora': movement.get('fecha_hora'),
                'usuario': (
                    user_names.get(str(user_id))
                    or ('Sistema automático' if is_automatic else 'Usuario no identificado')
                ),
                'origen': movement.get('origen_accion') or activation_type or 'manual',
            })

        return Response({'advertencia': None, 'logs': logs})


class MobileValvulaCommandView(APIView):
    permission_classes = [IsAdministrator]
    limite_alcance = 'valvula'
    limite_por = 'usuario'

    def post(self, request, id_valvula, action):
        action = action.replace('-remoto', '')
        if action not in ('abrir', 'cerrar'):
            return Response({'error': 'Comando inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows = supabase_client.select(
                'valvula',
                'id_valvula,estado_actual,topic_mqtt_comando',
                {'id_valvula': f'eq.{id_valvula}', 'limit': '1'},
            )
        except supabase_client.SupabaseError:
            return Response({'error': 'No se pudo consultar la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        if not rows or not rows[0].get('topic_mqtt_comando'):
            return Response({'error': 'Válvula no encontrada o sin topic MQTT.'}, status=status.HTTP_404_NOT_FOUND)
        valvula = rows[0]
        state = 'abierta' if action == 'abrir' else 'cerrada'
        try:
            publish_command(valvula['topic_mqtt_comando'], action)
            supabase_client.update(
                'valvula',
                {'estado_actual': state},
                {'id_valvula': f'eq.{id_valvula}'},
            )
        except MqttError as exc:
            logger.error('No se pudo enviar el comando %s a la válvula %s: %s', action, id_valvula, exc)
            return Response({'error': 'No se pudo enviar el comando a la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        except supabase_client.SupabaseError:
            return Response({'error': 'El comando se envió, pero no se pudo guardar el estado.'}, status=status.HTTP_502_BAD_GATEWAY)

        # Mismo registro que los comandos desde la web: quién, cuándo y desde dónde.
        try:
            supabase_client.insert('log_valvula', {
                'id_valvula': valvula['id_valvula'],
                'accion': action,
                'estado_anterior': valvula.get('estado_actual') or 'desconocida',
                'estado_nuevo': state,
                'tipo_activacion': 'manual',
                'id_usuario': owner_id(request.user),
                'fecha_hora': timezone.now().isoformat(),
                'ip_dispositivo': request.META.get('REMOTE_ADDR'),
                'origen_accion': 'app',
            })
        except supabase_client.SupabaseError:
            logger.error('No se pudo registrar en log_valvula el comando %s de la válvula %s', action, id_valvula)
        return Response({'ok': True, 'estado_actual': state})
