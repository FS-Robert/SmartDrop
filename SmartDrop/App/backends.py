from functools import lru_cache

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password as django_check_password
from passlib.hash import bcrypt
import logging

from . import supabase_client
from .models import Rol, Usuario

logger = logging.getLogger(__name__)

ADMIN_ROLE_ID = 2


class SinRolError(ValueError):
    """La cuenta de Supabase no tiene rol: no se adivina uno (podría terminar siendo el de admin)."""


@lru_cache(maxsize=1)
def _hash_falso():
    """Hash bcrypt de relleno: comparar contra él tarda lo mismo que con una cuenta real, así el tiempo de
    respuesta no delata si un correo está registrado."""
    return bcrypt.using(rounds=12, ident='2b').hash('contraseña-de-relleno')


def _rol_local(rol_id):
    """Rol local con el mismo id que en Supabase. Si falta, se toma el rol 'user' de Supabase."""
    if not rol_id:
        rol_id = supabase_client.get_rol_id('user')
        if not rol_id:
            raise SinRolError('La cuenta no tiene rol asignado en Supabase.')
    nombre = 'admin' if rol_id == ADMIN_ROLE_ID else 'user'
    rol, _ = Rol.objects.get_or_create(
        id_rol=rol_id,
        defaults={'nombre_rol': nombre, 'descripcion': 'Administrador' if nombre == 'admin' else 'Usuario estándar'},
    )
    return rol


def sync_user_from_supabase(row: dict) -> Usuario:
    """Crea o actualiza el usuario local a partir de un registro de Supabase."""
    rol = _rol_local(row.get('id_rol'))
    rol_id = rol.id_rol

    email = row['correo']
    user, _ = Usuario.objects.update_or_create(
        email=email,
        defaults={
            'supabase_id': row.get('id_usuario'),
            'nombre': row.get('nombre', ''),
            'apellido': row.get('apellido', ''),
            'rol': rol,
            'is_active': row.get('estado_usuario', True),
            'is_staff': rol_id == ADMIN_ROLE_ID,
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
        if not email or not password or len(email) > 254:
            return None

        row = supabase_client.get_user_by_email(email)
        if not row:
            bcrypt.verify(password, _hash_falso())
            logger.warning('Inicio de sesión rechazado: correo no encontrado (%s).', email)
            return None

        stored_hash = row.get('contrasena', '')
        if not stored_hash:
            bcrypt.verify(password, _hash_falso())
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

        try:
            return sync_user_from_supabase(row)
        except SinRolError:
            logger.warning('Inicio de sesión rechazado: la cuenta no tiene rol (%s).', email)
            return None

    def get_user(self, user_id):
        try:
            user = Usuario.objects.get(pk=user_id)
        except Usuario.DoesNotExist:
            return None
        return user if user.is_active else None
