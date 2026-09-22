from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password as django_check_password
from passlib.hash import bcrypt
import logging

from . import supabase_client
from .models import Rol, Usuario

logger = logging.getLogger(__name__)


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
            'supabase_id': row.get('id_usuario'),
            'nombre': row.get('nombre', ''),
            'apellido': row.get('apellido', ''),
            'rol': rol,
            'is_active': row.get('estado_usuario', True),
            'is_staff': rol_id == 2,
        },
    )
    # La contraseña ya viene hasheada desde Supabase; no usar set_password.
    user.password = row.get('contrasena', user.password)
    user.save(update_fields=['password'])
    return user


class SupabaseAuthBackend(BaseBackend):
    """Autentica usuarios consultando la tabla `usuario` en Supabase vía REST API."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = (kwargs.get('email') or username or '').strip().lower()
        if not email or not password:
            return None

        row = supabase_client.get_user_by_email(email)
        if not row:
            logger.warning('Inicio de sesión rechazado: correo no encontrado (%s).', email)
            return None

        stored_hash = row.get('contrasena', '')
        if not stored_hash:
            logger.warning('Inicio de sesión rechazado: usuario sin contraseña (%s).', email)
            return None

        try:
            if stored_hash.startswith(('$2b$', '$2a$', '$2y$')):
                password_valid = bcrypt.verify(password, stored_hash)
            else:
                password_valid = django_check_password(password, stored_hash)
        except (TypeError, ValueError):
            password_valid = False

        if not password_valid:
            logger.warning('Inicio de sesión rechazado: contraseña incorrecta (%s).', email)
            return None

        if not row.get('estado_usuario', True):
            logger.warning('Inicio de sesión rechazado: usuario inactivo (%s).', email)
            return None

        return sync_user_from_supabase(row)

    def get_user(self, user_id):
        try:
            return Usuario.objects.get(pk=user_id)
        except Usuario.DoesNotExist:
            return None
