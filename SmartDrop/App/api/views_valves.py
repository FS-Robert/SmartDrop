import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client, valve_service
from ..mqtt_service import MqttError, publish_command
from ..queries import users_by_id
from ..ratelimit import client_ip
from .permissions import IsAdministrator, IsAuthenticatedUser
from .throttling import ValvulaComandoThrottle


logger = logging.getLogger(__name__)


class MobileValvulaEstadoView(APIView):
    """Estado de la válvula para la app: la fila de `valvula` más el temporizador activo (si lo hay).

    `temporizador_activo` = {duracion_programada, segundos_restantes, fin} o null; la app lo usa para
    mostrar la cuenta regresiva.
    """
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, id_valvula):
        try:
            rows = supabase_client.select(
                'valvula',
                'id_valvula,nombre,estado_actual,estado_operativo,ultima_apertura,ultima_conexion_mqtt',
                {'id_valvula': f'eq.{int(id_valvula)}', 'limit': '1'},
            )
        except supabase_client.SupabaseError:
            logger.exception('No se pudo leer el estado de la válvula %s', id_valvula)
            return Response({'error': 'No se pudo consultar la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        if not rows:
            return Response({'error': 'Válvula no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        valvula = dict(rows[0])
        abierta = valve_service.esta_abierta(valvula.get('estado_actual'))
        valvula['temporizador_activo'] = valve_service.temporizador_para_app(id_valvula) if abierta else None
        valvula['abierta_desde'] = valvula.get('ultima_apertura') if abierta else None
        return Response(valvula)


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
    """Comandos de la válvula desde la app (solo administradores).

    * POST api/valvula/<id>/control/  con {"accion": "ABRIR" | "CERRAR", "duracion": 0 | 5..120}
      (lo que envía la app; duracion 0 = sin temporizador).
    * POST api/valvula/<id>/abrir/ o /cerrar/ (también -remoto), con "duracion" opcional en el cuerpo.

    Cada comando se publica por MQTT, actualiza `valvula` y queda en `log_valvula` con el usuario del
    token (nunca el nombre que mande el cliente) y origen "app".
    """
    permission_classes = [IsAdministrator]
    throttle_classes = [ValvulaComandoThrottle]

    def post(self, request, id_valvula, action):
        action = str(action or '').strip().lower()
        data = request.data if isinstance(request.data, dict) else {}
        try:
            comando = valve_service.normalizar_comando(data.get('accion') if action == 'control' else action)
            duracion = valve_service.parse_duracion(data.get('duracion', data.get('duracion_seg')))
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resultado = valve_service.ejecutar_comando(
                id_valvula, comando,
                duracion_seg=duracion if comando == 'abrir' else None,
                id_usuario=getattr(request.user, 'id_usuario', None),
                ip=client_ip(request),
                origen='app',
                publish=publish_command,
            )
        except valve_service.ValvulaNoEncontrada as exc:
            return Response({'error': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except (MqttError, supabase_client.SupabaseError) as exc:
            logger.warning('Comando %s a la válvula %s falló: %s', comando, id_valvula, exc)
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        temporizador = valve_service.temporizador_para_app(id_valvula) if resultado['estado_nuevo'] == 'abierta' else None
        return Response({
            'ok': True,
            'accion': resultado['accion'].upper(),
            'estado_anterior': resultado['estado_anterior'],
            'estado_actual': resultado['estado_nuevo'],
            'tipo_activacion': resultado['tipo_activacion'],
            'fecha_hora': resultado['fecha_hora'],
            'abierta_desde': resultado['ultima_apertura'],
            'temporizador_activo': temporizador,
            'log_registrado': resultado['log_registrado'],
        })
