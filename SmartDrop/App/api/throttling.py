"""Rate limiting de la API de la app (Django REST Framework).

Se aplican por defecto (settings.REST_FRAMEWORK) un límite por IP para peticiones sin sesión y otro por
usuario para peticiones con token. Las vistas sensibles (login, registro, renovación de token, comandos
de la válvula y creación de reportes) agregan límites más estrictos.
Los valores se pueden cambiar con RATELIMIT_OVERRIDES (ver App/ratelimit.py).
"""
from django.core.cache import caches
from rest_framework.throttling import SimpleRateThrottle

from ..ratelimit import client_ip, enabled, rate_for


class _BaseThrottle(SimpleRateThrottle):
    cache = caches['limites']  # la misma caché que App/seguridad.py y App/ratelimit.py
    default_rate = None
    methods = None  # None = todos los métodos

    def get_rate(self):
        return rate_for(self.scope, self.default_rate)

    def allow_request(self, request, view):
        if not enabled():
            return True
        if self.methods is not None and request.method not in self.methods:
            return True
        return super().allow_request(request, view)

    def get_ident(self, request):
        return client_ip(request)

    @staticmethod
    def _user_id(request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            return getattr(user, 'id_usuario', None) or getattr(user, 'pk', None)
        return None

    def get_cache_key(self, request, view):
        ident = self.ident_for(request)
        if ident is None:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': ident}

    def ident_for(self, request):
        return self.get_ident(request)


class AnonApiThrottle(_BaseThrottle):
    """Peticiones sin token: por IP."""
    scope = 'api_anonimo'
    default_rate = '120/m'

    def ident_for(self, request):
        return None if self._user_id(request) is not None else self.get_ident(request)


class UserApiThrottle(_BaseThrottle):
    """Peticiones con token: por usuario (la app consulta cada 10 s en varias pantallas)."""
    scope = 'api_usuario'
    default_rate = '300/m'

    def ident_for(self, request):
        user_id = self._user_id(request)
        return None if user_id is None else f'u{user_id}'


class LoginIpThrottle(_BaseThrottle):
    """Intentos de inicio de sesión desde una misma IP."""
    scope = 'login_ip'
    default_rate = '20/m'
    methods = ('POST',)


class LoginCuentaThrottle(_BaseThrottle):
    """Intentos contra una misma cuenta desde cualquier IP (fuerza bruta distribuida)."""
    scope = 'login_cuenta'
    default_rate = '10/m'
    methods = ('POST',)

    def ident_for(self, request):
        try:
            correo = str(request.data.get('correo', '') or request.data.get('email', '')).strip().lower()
        except (AttributeError, ValueError):
            correo = ''
        return f'correo:{correo}' if correo else None


class RegistroThrottle(_BaseThrottle):
    scope = 'registro'
    default_rate = '20/h'
    methods = ('POST',)


class RefreshThrottle(_BaseThrottle):
    scope = 'refresh'
    default_rate = '30/m'
    methods = ('POST',)


class ValvulaComandoThrottle(_BaseThrottle):
    """Comandos físicos a la válvula: por usuario (además se piden confirmación en la app y la web)."""
    scope = 'valvula_comando'
    default_rate = '20/m'
    methods = ('POST',)

    def ident_for(self, request):
        user_id = self._user_id(request)
        return f'u{user_id}' if user_id is not None else self.get_ident(request)


class ReportesEscrituraThrottle(_BaseThrottle):
    """Crear reportes, enviar mensajes y subir adjuntos (evita llenar Supabase y el disco)."""
    scope = 'reportes_escritura'
    default_rate = '30/h'
    methods = ('POST',)

    def ident_for(self, request):
        user_id = self._user_id(request)
        return f'u{user_id}' if user_id is not None else self.get_ident(request)


class ReportesMensajesThrottle(ReportesEscrituraThrottle):
    """Mensajes del chat de un reporte."""
    scope = 'reportes_mensajes'
    default_rate = '120/h'


class VincularThrottle(ReportesEscrituraThrottle):
    """Vincular vivienda por NIC: evita probar NICs al azar hasta dar con uno ajeno."""
    scope = 'vincular'
    default_rate = '20/h'
