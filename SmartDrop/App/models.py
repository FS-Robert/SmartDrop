from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _


class Rol(models.Model):
    id_rol = models.BigAutoField(primary_key=True, db_column='id_rol')
    nombre_rol = models.CharField(max_length=60, db_column='nombre_rol')
    descripcion = models.CharField(max_length=200, blank=True, db_column='descripcion')

    class Meta:
        db_table = 'rol'
        verbose_name = 'rol'
        verbose_name_plural = 'roles'

    def __str__(self):
        return self.nombre_rol


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, nombre, apellido, password=None, rol=None, **extra_fields):
        if not email:
            raise ValueError('El correo es obligatorio')
        if not nombre:
            raise ValueError('El nombre es obligatorio')
        if not apellido:
            raise ValueError('El apellido es obligatorio')

        email = self.normalize_email(email)
        if rol is None:
            rol, _ = Rol.objects.get_or_create(nombre_rol='user', defaults={'descripcion': 'Usuario estándar'})

        user = self.model(email=email, nombre=nombre, apellido=apellido, rol=rol, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, nombre, apellido, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if password is None:
            raise ValueError('La contraseña del superuser no puede estar vacía')

        role, _ = Rol.objects.get_or_create(nombre_rol='admin', defaults={'descripcion': 'Administrador'})
        return self.create_user(email, nombre, apellido, password=password, rol=role, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    id_usuario = models.BigAutoField(primary_key=True, db_column='id_usuario')
    nombre = models.CharField(max_length=255)
    apellido = models.CharField(max_length=255)
    email = models.EmailField(_('correo'), unique=True, db_column='correo')
    password = models.CharField(_('contrasena'), max_length=128, db_column='contrasena')
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, db_column='id_rol')
    is_active = models.BooleanField(default=True, db_column='estado_usuario')
    fecha_registro = models.DateTimeField(auto_now_add=True, db_column='fecha_registro')
    is_staff = models.BooleanField(default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nombre', 'apellido']

    class Meta:
        db_table = 'usuario'
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f'{self.nombre} {self.apellido}'

    def get_short_name(self):
        return self.nombre
