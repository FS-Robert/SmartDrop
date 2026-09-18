from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .. import supabase_client
from ..models import Usuario
from .permissions import IsAuthenticatedUser


class MobileRegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        required = ('nombre', 'apellido', 'correo', 'contrasena')
        values = {field: str(request.data.get(field, '')).strip() for field in required}
        if not all(values.values()):
            return Response({'error': 'Completa todos los campos.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            row = supabase_client.create_usuario(**{
                'nombre': values['nombre'],
                'apellido': values['apellido'],
                'email': values['correo'],
                'password': values['contrasena'],
            })
        except supabase_client.SupabaseError as exc:
            code = status.HTTP_409_CONFLICT if exc.status_code == 409 else status.HTTP_502_BAD_GATEWAY
            return Response({'error': str(exc)}, status=code)
        except Exception:
            return Response({'error': 'No se pudo registrar el usuario.'}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({
            'mensaje': 'Usuario registrado exitosamente.',
            'id_usuario': row.get('id_usuario'),
        }, status=status.HTTP_201_CREATED)


class MobileLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get('correo', '')).strip().lower()
        password = str(request.data.get('contrasena', '')).strip()
        if not email or not password:
            return Response({'error': 'Completa todos los campos.'}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, email=email, password=password)
        if not user:
            return Response({'error': 'Correo o contraseña incorrectos.'}, status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken()
        refresh['id_usuario'] = user.supabase_id or user.id_usuario
        refresh['correo'] = user.email
        refresh['id_rol'] = user.rol_id
        refresh['nombre_rol'] = user.rol.nombre_rol
        return Response({
            'mensaje': 'Inicio de sesión exitoso.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'id_rol': user.rol_id,
            'nombre_rol': user.rol.nombre_rol,
            'id_usuario': user.supabase_id or user.id_usuario,
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
