from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken


class MobileUser:
    is_authenticated = True

    def __init__(self, payload):
        self.id_usuario = payload.get('id_usuario')
        self.supabase_id = payload.get('id_usuario')
        self.email = payload.get('correo', '')
        self.correo = self.email
        self.rol_id = payload.get('id_rol')
        self.nombre_rol = payload.get('nombre_rol')


class SupabaseJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Bearer '):
            return None

        try:
            token = AccessToken(header.split(' ', 1)[1])
        except TokenError as exc:
            raise AuthenticationFailed('Token invalido o expirado.') from exc

        return MobileUser(token.payload), token
