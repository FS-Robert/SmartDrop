from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password

from . import supabase_client
from .models import Rol, Usuario


def sync_user_from_supabase(row: dict) -> Usuario:
    """Crea o actualiza el usuario local a partir de un registro de Supabase."""
    rol_id = row.get('id_rol')
    if rol_id:
        rol, _ = Rol.objects.get_or_create(
            id_rol=rol_id,
            defaults={'nombre_rol': 'user', 'descripcion': 'Usuario estándar'},
        )
    else:
        rol, _ = Rol.objects.get_or_create(
            nombre_rol='user',
            defaults={'descripcion': 'Usuario estándar'},
        )

    email = row['correo']
    user, _ = Usuario.objects.update_or_create(
        email=email,
        defaults={
            'nombre': row.get('nombre', ''),
            'apellido': row.get('apellido', ''),
            'rol': rol,
            'is_active': row.get('estado_usuario', True),
        },
    )
    # La contraseña ya viene hasheada desde Supabase; no usar set_password.
    user.password = row.get('contrasena', user.password)
    user.save(update_fields=['password'])
    return user


class SupabaseAuthBackend(BaseBackend):
    """Autentica usuarios consultando la tabla `usuario` en Supabase vía REST API."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = kwargs.get('email') or username
        if not email or not password:
            return None

        row = supabase_client.get_user_by_email(email)
        if not row:
            return None

        stored_hash = row.get('contrasena', '')
        if not stored_hash or not check_password(password, stored_hash):
            return None

        if not row.get('estado_usuario', True):
            return None

        return sync_user_from_supabase(row)

    def get_user(self, user_id):
        try:
            return Usuario.objects.get(pk=user_id)
        except Usuario.DoesNotExist:
            return None
