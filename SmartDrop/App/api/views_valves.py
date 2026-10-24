import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client, valve_service
from ..mqtt_service import MqttError, publish_command
from ..queries import is_admin, owner_id, users_by_id
from ..ratelimit import client_ip
from .permissions import IsAdministrator, IsAuthenticatedUser


logger = logging.getLogger(__name__)


# Lo que un usuario sin rol de admin puede ver de una válvula: el topic MQTT y el pin del ESP32 son
# datos internos del sistema de control y solo los ve el admin.
CAMPOS_VALVULA_PUBLICOS = 'id_valvula,nombre,estado_actual,ultima_apertura'


class MobileValvulaEstadoView(APIView):
    """Estado de la válvula para la app: la fila de `valvula` más el temporizador activo (si lo hay).

    `temporizador_activo` = {duracion_programada, segundos_restantes, fin} o null; la app lo usa para
    mostrar la cuenta regresiva.
    """
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
        valvula = rows[0]
        abierta = valve_service.esta_abierta(valvula.get('estado_actual'))
        return Response({
            **valvula,
            # La app muestra "Abierta desde las …" con este campo.
            'abierta_desde': valvula.get('ultima_apertura') if abierta else None,
            'temporizador_activo': valve_service.temporizador_para_app(id_valvula),
        })


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


def _duracion(valor):
    """Segundos de apertura temporizada (0 = sin temporizador); mismas reglas que la web (5 a 120 s)."""
    try:
        duracion = int(float(valor or 0))
    except (TypeError, ValueError):
        raise ValueError('La duración debe ser un número de segundos.')
    if duracion and not 5 <= duracion <= 120:
        raise ValueError('La duración debe estar entre 5 y 120 segundos.')
    return duracion


class MobileValvulaCommandView(APIView):
    """POST api/valvula/<id>/abrir/ o /cerrar/, o /control/ con {"accion": "ABRIR"|"CERRAR", "duracion": s}
    (lo que envía la app). Con duración, la válvula se cierra sola al terminar, igual que desde la web."""

    permission_classes = [IsAdministrator]
    limite_alcance = 'valvula'
    limite_por = 'usuario'

    def post(self, request, id_valvula, action):
        action = action.replace('-remoto', '')
        duracion = 0
        if action == 'control':
            action = str(request.data.get('accion', '')).strip().lower()
            try:
                duracion = _duracion(request.data.get('duracion')) if action == 'abrir' else 0
            except ValueError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if action not in ('abrir', 'cerrar'):
            return Response({'error': 'Comando inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        # El mismo servicio que la web: temporizador compartido, "abrir" sin tiempo cancela uno pendiente y
        # el movimiento queda en log_valvula (quién, cuándo y desde dónde).
        try:
            resultado = valve_service.ejecutar_comando(
                id_valvula, action,
                duracion_seg=duracion or None,
                id_usuario=owner_id(request.user),
                ip=client_ip(request),
                origen='app',
                publish=publish_command,
            )
        except valve_service.ValvulaNoEncontrada:
            return Response({'error': 'Válvula no encontrada o sin topic MQTT.'}, status=status.HTTP_404_NOT_FOUND)
        except MqttError as exc:
            logger.error('No se pudo enviar el comando %s a la válvula %s: %s', action, id_valvula, exc)
            return Response({'error': 'No se pudo enviar el comando a la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        except supabase_client.SupabaseError:
            return Response({'error': 'No se pudo completar el comando. Revisa el estado de la válvula.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            'ok': True,
            'estado_actual': resultado['estado_nuevo'],
            'temporizador_activo': valve_service.temporizador_para_app(id_valvula),
        })
