import logging
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from . import supabase_client
from .api.authentication import MobileUser

logger = logging.getLogger(__name__)


@sync_to_async
def _owned_vivienda_ids(user):
    """Viviendas del usuario. Los sensores se comparten entre viviendas, así que se escucha por vivienda."""
    propietario_id = getattr(user, 'supabase_id', None) or user.id_usuario
    viviendas = supabase_client.select(
        'vivienda',
        'id_vivienda',
        {'id_usuario_propietario': f'eq.{propietario_id}', 'limit': '1000'},
    )
    return [
        str(row['id_vivienda'])
        for row in viviendas
        if row.get('id_vivienda') is not None
    ]


class SensorRealtimeConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not getattr(user, 'is_authenticated', False):
            headers = dict(self.scope.get('headers', []))
            authorization = headers.get(b'authorization', b'').decode('utf-8')
            if authorization.startswith('Bearer '):
                try:
                    user = MobileUser(AccessToken(authorization.split(' ', 1)[1]).payload)
                except TokenError:
                    user = None
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.groups = []
        if getattr(user, 'rol_id', None) == 2:
            self.groups.append('sensor-readings-admin')
        else:
            try:
                vivienda_ids = await _owned_vivienda_ids(user)
            except Exception:
                await self.close(code=4500)
                return
            self.groups.extend(f'sensor-vivienda-{vivienda_id}' for vivienda_id in vivienda_ids)

        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, 'groups', []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def sensor_reading(self, event):
        await self.send_json(event['payload'])


class ReporteChatConsumer(AsyncJsonWebsocketConsumer):
    """Consumer de WebSockets para live chat en tiempo real en reportes de problemas."""

    async def connect(self):
        user = self.scope.get('user')
        if not getattr(user, 'is_authenticated', False):
            headers = dict(self.scope.get('headers', []))
            authorization = headers.get(b'authorization', b'').decode('utf-8')
            if authorization.startswith('Bearer '):
                try:
                    user = MobileUser(AccessToken(authorization.split(' ', 1)[1]).payload)
                except TokenError:
                    user = None
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.reporte_id = self.scope['url_route']['kwargs'].get('reporte_id')
        if not self.reporte_id:
            await self.close(code=4400)
            return

        self.room_group_name = f'reporte_chat_{self.reporte_id}'
        es_admin = getattr(user, 'rol_id', None) == 2
        user_id = getattr(user, 'supabase_id', None) or getattr(user, 'id_usuario', None)

        if not es_admin:
            try:
                rows = await sync_to_async(supabase_client.select)(
                    'reporte', 'id_reporte',
                    {'id_reporte': f'eq.{self.reporte_id}', 'id_usuario': f'eq.{user_id}', 'limit': '1'},
                )
                if not rows:
                    await self.close(code=4403)
                    return
            except Exception:
                await self.close(code=4500)
                return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive_json(self, content):
        mensaje_texto = (content.get('mensaje') or '').strip()
        if not mensaje_texto:
            return

        user = self.scope.get('user')
        user_id = getattr(user, 'supabase_id', None) or getattr(user, 'id_usuario', None)
        es_admin = getattr(user, 'rol_id', None) == 2

        autor_nombre = 'Usuario'
        if hasattr(user, 'get_full_name') and user.get_full_name().strip():
            autor_nombre = user.get_full_name().strip()
        elif getattr(user, 'nombre', None):
            autor_nombre = f"{user.nombre} {getattr(user, 'apellido', '')}".strip()

        try:
            row = await sync_to_async(supabase_client.insert)('reporte_mensaje', {
                'id_reporte': self.reporte_id,
                'id_usuario': user_id,
                'mensaje': mensaje_texto,
            })
            id_mensaje = (row or {}).get('id_mensaje')
            fecha_envio = (row or {}).get('fecha_envio') or timezone.now().isoformat()
        except Exception:
            logger.exception('Error al guardar mensaje de chat en Supabase vía WebSocket')
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'id_mensaje': id_mensaje,
                'id_usuario': user_id,
                'mensaje': mensaje_texto,
                'fecha_envio': fecha_envio,
                'es_admin_emisor': es_admin,
                'autor_nombre': autor_nombre,
            },
        )

    async def chat_message(self, event):
        await self.send_json(event)

