from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from . import supabase_client
from .api.authentication import MobileUser


@sync_to_async
def _owned_sensor_ids(user):
    propietario_id = getattr(user, 'supabase_id', None) or user.id_usuario
    viviendas = supabase_client.select(
        'vivienda',
        'id_vivienda',
        {'id_usuario_propietario': f'eq.{propietario_id}', 'limit': '1000'},
    )
    vivienda_ids = [
        str(row['id_vivienda'])
        for row in viviendas
        if row.get('id_vivienda') is not None
    ]
    if not vivienda_ids:
        return []

    sensores = supabase_client.select(
        'sensor',
        'id_sensor',
        {'id_vivienda': f"in.({','.join(vivienda_ids)})", 'limit': '1000'},
    )
    return [
        str(row['id_sensor'])
        for row in sensores
        if row.get('id_sensor') is not None
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
                sensor_ids = await _owned_sensor_ids(user)
            except Exception:
                await self.close(code=4500)
                return
            self.groups.extend(f'sensor-reading-{sensor_id}' for sensor_id in sensor_ids)

        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, 'groups', []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def sensor_reading(self, event):
        await self.send_json(event['payload'])
