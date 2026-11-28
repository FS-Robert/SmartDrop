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
    supabase_id = models.BigIntegerField(null=True, blank=True, unique=True, db_column='id_usuario_supabase')
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


class PreferenciasUsuario(models.Model):
    """Preferencias de notificaciones y de la interfaz, compartidas por la web y la app móvil.

    Se guardan en el backend (no en Supabase) con el id_usuario de Supabase, que es el mismo
    identificador que usan la sesión web y el JWT de la app.
    """

    IDIOMAS = (('es', 'Español'), ('en', 'English'))

    id_usuario = models.BigIntegerField(unique=True)
    alertas_nivel = models.BooleanField(default=True)
    suministro = models.BooleanField(default=True)
    calidad = models.BooleanField(default=True)
    consumo_elevado = models.BooleanField(default=True)
    modo_oscuro = models.BooleanField(default=False)
    idioma = models.CharField(max_length=2, choices=IDIOMAS, default='es')
    reportes_semanales = models.BooleanField(default=False)
    ultimo_reporte_semanal = models.DateField(null=True, blank=True)
    ultimo_reporte_datos = models.JSONField(null=True, blank=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'preferencias_usuario'
        verbose_name = 'preferencias de usuario'
        verbose_name_plural = 'preferencias de usuarios'

    def __str__(self):
        return f'Preferencias de {self.id_usuario}'


class ConversacionGotita(models.Model):
    """Una conversación con Gotita. Se borra sola cuando lleva 20 días sin usarse (ver asistente/historial.py)."""

    id_usuario = models.BigIntegerField(db_index=True)
    plataforma = models.CharField(max_length=4, default='web')
    titulo = models.CharField(max_length=80)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = 'gotita_conversacion'
        ordering = ['-actualizada']
        verbose_name = 'conversación con Gotita'
        verbose_name_plural = 'conversaciones con Gotita'

    def __str__(self):
        return f'{self.titulo} ({self.id_usuario})'


class MensajeGotita(models.Model):
    """Un mensaje de una conversación con Gotita: lo que preguntó la persona o lo que respondió Gotita."""

    ROLES = (('user', 'Persona'), ('gotita', 'Gotita'))

    conversacion = models.ForeignKey(ConversacionGotita, on_delete=models.CASCADE, related_name='mensajes')
    rol = models.CharField(max_length=6, choices=ROLES)
    texto = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gotita_mensaje'
        ordering = ['id']


class AjusteComunidad(models.Model):
    """Ajustes que decide el administrador para toda la comunidad (por ejemplo, el límite de consumo al mes)."""

    clave = models.CharField(max_length=60, unique=True)
    valor = models.FloatField()
    id_usuario = models.BigIntegerField(null=True, blank=True, help_text='Admin que lo cambió por última vez.')
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ajuste_comunidad'
        verbose_name = 'ajuste de la comunidad'
        verbose_name_plural = 'ajustes de la comunidad'

    def __str__(self):
        return f'{self.clave} = {self.valor}'


class RegistroManual(models.Model):
    """Lectura escrita a mano por un operador cuando un sensor no está disponible (PB054).

    Se guarda aparte de las lecturas de los sensores: no se mezcla con las gráficas, las alertas ni la IA,
    así nunca cambia lo que miden los sensores.
    """

    ubicacion = models.CharField(max_length=120, blank=True, help_text='Vivienda, tanque o zona donde se midió.')
    consumo_litros = models.DecimalField(max_digits=10, decimal_places=2)
    nivel_pct = models.DecimalField(max_digits=5, decimal_places=2)
    fecha_medicion = models.DateTimeField()
    observacion = models.CharField(max_length=300, blank=True)
    id_usuario = models.BigIntegerField(null=True, blank=True, help_text='Operador (id de Supabase) que lo registró.')
    operador = models.CharField(max_length=255, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'registro_manual'
        ordering = ('-fecha_medicion', '-id')
        verbose_name = 'registro manual'
        verbose_name_plural = 'registros manuales'

    def __str__(self):
        return f'{self.fecha_medicion:%d/%m/%Y %H:%M} · {self.consumo_litros} L · {self.nivel_pct} %'


class InterrupcionServicio(models.Model):
    """Hora en que volvió el agua para un reporte de "No llega agua" (PB042).

    Se guarda en la base local porque la tabla `reporte` de Supabase no tiene ese dato.
    Con esta hora y la del reporte se sabe cuánto duró el corte.
    """

    id_reporte = models.BigIntegerField(unique=True)
    restablecido = models.DateTimeField(help_text='Hora en que el servicio volvió.')
    id_usuario = models.BigIntegerField(null=True, blank=True, help_text='Administrador (id de Supabase) que lo anotó.')
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'interrupcion_servicio'
        verbose_name = 'interrupción del servicio'
        verbose_name_plural = 'interrupciones del servicio'

    def __str__(self):
        return f'Reporte #{self.id_reporte} · restablecido {self.restablecido:%d/%m/%Y %H:%M}'


class BloqueoValvula(models.Model):
    """Motivo por el que las válvulas no se pueden abrir (PB052 y calidad del agua).

    Hay una fila por motivo: "nivel" (el tanque bajó al 8 %) y "calidad" (el agua salió Pobre o
    Inaceptable). Mientras alguna esté activa, el botón "Abrir válvula" queda bloqueado en la web y
    en la app. Se guarda en la base local para que un reinicio del servidor no lo olvide.
    """

    MOTIVOS = (('nivel', 'Nivel insuficiente'), ('calidad', 'Calidad del agua'), ('aviso_nivel', 'Aviso de nivel bajo'))

    motivo = models.CharField(max_length=20, choices=MOTIVOS, unique=True)
    activo = models.BooleanField(default=False)
    valor = models.CharField(max_length=60, blank=True, help_text='Última lectura que lo activó (por ejemplo "6 %" o "Pobre, 1192 ppm").')
    desde = models.DateTimeField(null=True, blank=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bloqueo_valvula'
        verbose_name = 'bloqueo de válvula'
        verbose_name_plural = 'bloqueos de válvula'

    def __str__(self):
        return f"{self.get_motivo_display()}: {'activo' if self.activo else 'sin bloqueo'}"
