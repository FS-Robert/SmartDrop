import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client, valve_service
from ..mqtt_service import MqttError, publish_command
from ..queries import users_by_id
from ..ratelimit import client_ip
from ..queries import is_admin, owner_id, users_by_id
from ..views import _cancel_active_timer, _start_timer_for_valvula, _timer_info_for_valvula
from .permissions import IsAdministrator, IsAuthenticatedUser
from .throttling import ValvulaComandoThrottle


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
        return Response({**rows[0], 'temporizador_activo': _temporizador(id_valvula)})


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


def _temporizador(id_valvula):
    """Temporizador de apertura en curso (mismo registro que la web), en el formato que lee la app."""
    info = _timer_info_for_valvula(id_valvula)
    if not info['timer_activo']:
        return None
    return {'duracion_programada': info['duracion_seg'], 'segundos_restantes': info['timer_remaining']}


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
        cierre_durante_temporizador = action == 'cerrar' and _temporizador(id_valvula) is not None
        try:
            if action == 'cerrar':
                _cancel_active_timer(id_valvula)
            publish_command(valvula['topic_mqtt_comando'], action)
            supabase_client.update(
                'valvula',
                {
                    'estado_actual': state,
                    'ultima_apertura': timezone.now().isoformat() if action == 'abrir' else None,
                },
                {'id_valvula': f'eq.{id_valvula}'},
            )
        except MqttError as exc:
            logger.error('No se pudo enviar el comando %s a la válvula %s: %s', action, id_valvula, exc)
            return Response({'error': 'No se pudo enviar el comando a la válvula.'}, status=status.HTTP_502_BAD_GATEWAY)
        except supabase_client.SupabaseError:
            return Response({'error': 'El comando se envió, pero no se pudo guardar el estado.'}, status=status.HTTP_502_BAD_GATEWAY)

        if duracion:
            _start_timer_for_valvula(id_valvula, duracion)

        # Mismo registro que los comandos desde la web: quién, cuándo y desde dónde.
        razon = None
        if cierre_durante_temporizador:
            razon = 'Cierre manual durante temporizador'
        elif duracion:
            razon = f'Apertura temporizada de {duracion} segundos'
        try:
            supabase_client.insert('log_valvula', {
                'id_valvula': valvula['id_valvula'],
                'accion': action,
                'estado_anterior': valvula.get('estado_actual') or 'desconocida',
                'estado_nuevo': state,
                'tipo_activacion': 'temporizado' if duracion else 'manual',
                'id_usuario': owner_id(request.user),
                'fecha_hora': timezone.now().isoformat(),
                'ip_dispositivo': request.META.get('REMOTE_ADDR'),
                'origen_accion': 'app',
                'duracion_programada': duracion or None,
                'razon': razon,
            })
        except supabase_client.SupabaseError:
            logger.error('No se pudo registrar en log_valvula el comando %s de la válvula %s', action, id_valvula)
        return Response({'ok': True, 'estado_actual': state, 'temporizador_activo': _temporizador(id_valvula)})
