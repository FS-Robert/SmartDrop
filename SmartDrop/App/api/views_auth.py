import logging

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .. import seguridad, supabase_client
from ..forms import errores_contrasena
from .permissions import IsAuthenticatedUser
from .throttling import (
    AnonApiThrottle,
    LoginCuentaThrottle,
    LoginIpThrottle,
    RefreshThrottle,
    RegistroThrottle,
    UserApiThrottle,
)

logger = logging.getLogger(__name__)

ADMIN_ROLE_ID = 2


def _nombre_rol(id_rol):
    return 'admin' if id_rol == ADMIN_ROLE_ID else 'user'


def _tokens(id_usuario, correo, id_rol):
    refresh = RefreshToken()
    refresh['id_usuario'] = id_usuario
    refresh['correo'] = correo
    refresh['id_rol'] = id_rol
    refresh['nombre_rol'] = _nombre_rol(id_rol)
    return refresh


class MobileRegisterView(APIView):
    permission_classes = [AllowAny]
    limite_alcance = 'registro'

    def post(self, request):
        required = ('nombre', 'apellido', 'correo', 'contrasena')
        values = {field: str(request.data.get(field, '')).strip() for field in required}
        if not all(values.values()):
            return Response({'error': 'Completa todos los campos.'}, status=status.HTTP_400_BAD_REQUEST)

        values['correo'] = values['correo'].lower()
        try:
            validate_email(values['correo'])
        except ValidationError:
            return Response({'error': 'Escribe un correo electrónico válido.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(values['correo']) > 254 or len(values['nombre']) > 255 or len(values['apellido']) > 255:
            return Response({'error': 'Algún dato es demasiado largo.'}, status=status.HTTP_400_BAD_REQUEST)
        # Recortada igual que en el inicio de sesión (MobileLoginView), para que siempre coincidan.
        contrasena = values['contrasena']
        errores = errores_contrasena(contrasena, values['correo'], values['nombre'], values['apellido'])
        if errores:
            return Response({'error': ' '.join(errores)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            row = supabase_client.create_usuario(**{
                'nombre': values['nombre'],
                'apellido': values['apellido'],
                'email': values['correo'],
                'password': contrasena,
            })
        except supabase_client.SupabaseError as exc:
            code = status.HTTP_409_CONFLICT if exc.status_code == 409 else status.HTTP_502_BAD_GATEWAY
            return Response({'error': str(exc)}, status=code)
        except Exception:
            logger.exception('Error inesperado al registrar un usuario desde la app')
            return Response({'error': 'No se pudo registrar el usuario.'}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({
            'mensaje': 'Usuario registrado exitosamente.',
            'id_usuario': row.get('id_usuario'),
        }, status=status.HTTP_201_CREATED)


class MobileLoginView(APIView):
    permission_classes = [AllowAny]
    limite_alcance = 'acceso'

    def post(self, request):
        email = str(request.data.get('correo', '')).strip().lower()
        password = str(request.data.get('contrasena', '')).strip()
        if not email or not password:
            return Response({'error': 'Completa todos los campos.'}, status=status.HTTP_400_BAD_REQUEST)

        espera = seguridad.acceso_bloqueado(email)
        if espera:
            return Response({'error': seguridad.mensaje_bloqueo(espera)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            user = authenticate(request, email=email, password=password)
        except supabase_client.SupabaseError:
            return Response(
                {'error': 'No se pudo validar el inicio de sesión. Inténtalo de nuevo en unos minutos.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not user:
            seguridad.registrar_fallo_acceso(email)
            return Response({'error': 'Correo o contraseña incorrectos.'}, status=status.HTTP_400_BAD_REQUEST)

        id_usuario = user.supabase_id or user.id_usuario
        refresh = _tokens(id_usuario, user.email, user.rol_id)
        return Response({
            'mensaje': 'Inicio de sesión exitoso.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'id_rol': user.rol_id,
            'nombre_rol': _nombre_rol(user.rol_id),
            'id_usuario': id_usuario,
            'nombre': user.nombre,
        })


class MobileMeView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response({
            'id_usuario': request.user.id_usuario,
            'correo': request.user.correo,
            'id_rol': request.user.rol_id,
        })

class MobileRefreshView(APIView):
    """Renueva el token de acceso con el token de renovación (la app lo usa al recibir 401).

    Antes de renovar se vuelve a leer la cuenta en Supabase: si se desactivó o se borró, el token de
    renovación se invalida; el rol del nuevo token es el actual, no el que tenía al iniciar sesión.
    """

    permission_classes = [AllowAny]
    limite_alcance = 'renovar'

    def post(self, request):
        token = str(request.data.get('refresh', '')).strip()
        if not token:
            return Response({'error': 'Falta el token de renovación.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            refresh = RefreshToken(token)
        except TokenError:
            return Response({'error': 'Token de renovación inválido o vencido.'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            row = supabase_client.get_user_by_email(str(refresh.get('correo', '')))
        except supabase_client.SupabaseError:
            return Response(
                {'error': 'No se pudo renovar la sesión en este momento.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not row or not row.get('estado_usuario', True) or row.get('id_usuario') != refresh.get('id_usuario'):
            refresh.blacklist()
            logger.warning('Renovación rechazada: la cuenta %s ya no está activa.', refresh.get('id_usuario'))
            return Response({'error': 'Tu sesión terminó. Inicia sesión de nuevo.'}, status=status.HTTP_401_UNAUTHORIZED)

        access = refresh.access_token
        access['id_rol'] = row.get('id_rol')
        access['nombre_rol'] = _nombre_rol(row.get('id_rol'))
        return Response({'access': str(access)})


class MobileLogoutView(APIView):
    """Cierra la sesión de la app: el token de renovación deja de servir en el servidor."""

    permission_classes = [AllowAny]
    limite_alcance = 'renovar'

    def post(self, request):
        try:
            RefreshToken(str(request.data.get('refresh', '')).strip()).blacklist()
        except TokenError:
            pass  # vencido, inválido o ya cerrado: el resultado es el mismo
        return Response({'mensaje': 'Sesión cerrada.'})
