"""Límites de uso (rate limiting) compartidos por la web, la API móvil y los WebSockets.

Los contadores viven en la caché 'limites' (en memoria del proceso): el servidor corre un solo proceso de
Daphne (ver deploy/smartdrop.service), así que todas las peticiones ven los mismos contadores. Cada límite
tiene un nombre ("alcance") y su tasa está en settings.LIMITES_USO, por ejemplo '10/m' o '10/15m'.

Detrás de Nginx, REMOTE_ADDR es la IP real del visitante: Nginx reemplaza X-Forwarded-For con
$remote_addr y Daphne (--proxy-headers) la copia a REMOTE_ADDR.
"""
import functools
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import caches
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import BaseThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)

cache = caches['limites']

_SEGUNDOS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
_RUTAS_JSON = ('/api/', '/auth/', '/v1/')


def _tasa(alcance):
    """('10/15m') -> (10, 900): cantidad permitida y duración de la ventana en segundos."""
    cantidad, periodo = settings.LIMITES_USO[alcance].split('/')
    multiplo = int(periodo[:-1] or 1)
    return int(cantidad), multiplo * _SEGUNDOS[periodo[-1]]


def _llave(alcance, clave):
    limite, ventana = _tasa(alcance)
    ahora = int(time.time())
    inicio = ahora - ahora % ventana
    return f'limite:{alcance}:{clave}:{inicio}', limite, ventana, inicio + ventana - ahora


def consumir(alcance, clave):
    """Cuenta un uso. Devuelve los segundos que faltan para poder seguir si se pasó del límite, o 0."""
    llave, limite, ventana, restante = _llave(alcance, clave)
    cache.add(llave, 0, ventana + 1)
    try:
        usados = cache.incr(llave)
    except ValueError:  # la ventana venció entre add() e incr()
        cache.set(llave, 1, ventana + 1)
        usados = 1
    if usados > limite:
        if usados == limite + 1:  # una línea por ventana, no una por cada petición rechazada
            logger.warning('Límite de uso "%s" superado por %s', alcance, clave)
        return max(restante, 1)
    return 0


def agotado(alcance, clave):
    """Como consumir(), pero sin contar: dice si ese límite ya está lleno."""
    llave, limite, _, restante = _llave(alcance, clave)
    return max(restante, 1) if (cache.get(llave) or 0) >= limite else 0


def huella(texto):
    """Clave corta y sin datos personales (para correos)."""
    return hashlib.sha256(str(texto).strip().lower().encode()).hexdigest()[:20]


def ip_cliente(request):
    return request.META.get('REMOTE_ADDR') or 'desconocida'


def _usuario_del_token(request):
    cabecera = request.META.get('HTTP_AUTHORIZATION', '')
    if not cabecera.startswith('Bearer '):
        return None
    try:
        return AccessToken(cabecera.split(' ', 1)[1]).payload.get('id_usuario')
    except TokenError:
        return None


def identidad(request, por='ip'):
    """'u:<id>' para usuarios con sesión o token válido; 'ip:<ip>' para el resto."""
    if por == 'usuario':
        user = getattr(request, 'user', None)
        if getattr(user, 'is_authenticated', False):
            usuario_id = getattr(user, 'supabase_id', None) or getattr(user, 'id_usuario', None)
            if usuario_id is not None:
                return f'u:{usuario_id}'
        usuario_id = _usuario_del_token(request)
        if usuario_id is not None:
            return f'u:{usuario_id}'
    return f'ip:{ip_cliente(request)}'


def mensaje_espera(espera):
    if espera < 90:
        cantidad, unidad = espera, 'segundo'
    else:
        cantidad, unidad = round(espera / 60), 'minuto'
    return f'Demasiadas solicitudes. Espera {cantidad} {unidad}{"s" if cantidad != 1 else ""} e inténtalo de nuevo.'


def _quiere_json(request):
    return (
        request.path.startswith(_RUTAS_JSON)
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )


def respuesta_limite(request, espera):
    mensaje = mensaje_espera(espera)
    if _quiere_json(request):
        respuesta = JsonResponse({'ok': False, 'error': mensaje}, status=429)
    else:
        respuesta = render(request, 'App/limite.html', {'mensaje': mensaje}, status=429)
    respuesta['Retry-After'] = str(espera)
    return respuesta


def limitar(alcance, por='ip', metodos=('POST',)):
    """Decorador para vistas de Django: aplica el límite `alcance` a los métodos indicados."""
    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(request, *args, **kwargs):
            if request.method in metodos:
                espera = consumir(alcance, identidad(request, por))
                if espera:
                    return respuesta_limite(request, espera)
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


class LimiteUsoMiddleware:
    """Límite general para todas las peticiones HTTP: por usuario si hay sesión o token, si no por IP.

    Los límites más estrictos de cada acción (iniciar sesión, registrarse, subir archivos…) se aplican
    además en cada vista.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(settings.STATIC_URL):
            clave = identidad(request, por='usuario')
            alcance = 'general_usuario' if clave.startswith('u:') else 'general_anonimo'
            espera = consumir(alcance, clave)
            if espera:
                return respuesta_limite(request, espera)
        return self.get_response(request)


class RevalidarSesionMiddleware:
    """Cada REVALIDAR_SESION_SEGUNDOS confirma en Supabase que la cuenta de la sesión web sigue activa.

    La sesión dura dos semanas y la copia local del usuario solo se actualizaba al iniciar sesión: una cuenta
    desactivada o borrada en Supabase seguía entrando, y un admin al que se le quitó el rol lo conservaba.
    Si Supabase no responde, la sesión sigue (no se saca a todos por una caída).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        segundos = settings.REVALIDAR_SESION_SEGUNDOS
        if segundos and getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None):
            llave = f'sesion-revisada:{user.pk}'
            if cache.add(llave, True, segundos):
                self._revalidar(request, user, llave)
        return self.get_response(request)

    @staticmethod
    def _revalidar(request, user, llave):
        from django.contrib.auth import logout

        from . import supabase_client
        from .backends import sync_user_from_supabase

        try:
            row = supabase_client.get_user_by_email(user.email)
        except Exception:
            logger.warning('No se pudo revalidar la sesión del usuario %s en Supabase.', user.pk, exc_info=True)
            cache.delete(llave)
            return
        if not row or not row.get('estado_usuario', True):
            logger.warning('Sesión cerrada: la cuenta %s ya no existe o está desactivada en Supabase.', user.pk)
            logout(request)
            cache.delete(llave)
        elif row.get('id_rol') != user.rol_id:
            logger.info('El rol del usuario %s cambió en Supabase; se actualiza la sesión.', user.pk)
            try:
                request.user = sync_user_from_supabase(row)
            except Exception:
                logger.warning('Sesión cerrada: no se pudo actualizar el rol del usuario %s.', user.pk, exc_info=True)
                logout(request)
                cache.delete(llave)


class LimiteAPI(BaseThrottle):
    """Throttle de DRF: la vista declara `limite_alcance` (y opcionalmente `limite_por`).

    Solo cuenta escrituras (POST, PATCH, DELETE…): leer el historial o el feed no gasta el límite
    de crear reportes, por ejemplo.
    """

    def allow_request(self, request, view):
        self.espera = 0
        alcance = getattr(view, 'limite_alcance', None)
        if not alcance or request.method in SAFE_METHODS:
            return True
        self.espera = consumir(alcance, identidad(request, getattr(view, 'limite_por', 'ip')))
        return not self.espera

    def wait(self):
        return self.espera


# ── Inicio de sesión: intentos fallidos por correo ──────────────────────────────

def acceso_bloqueado(correo):
    """Segundos de espera si ese correo acumuló demasiados intentos fallidos (0 si puede intentar)."""
    return agotado('acceso_fallido', huella(correo)) if correo else 0


def registrar_fallo_acceso(correo):
    if correo:
        consumir('acceso_fallido', huella(correo))


def mensaje_bloqueo(espera):
    minutos = max(1, round(espera / 60))
    return (
        'Hubo demasiados intentos fallidos con este correo. '
        f'Espera {minutos} minuto{"s" if minutos != 1 else ""} e inténtalo de nuevo.'
    )


# ── Exportaciones CSV ───────────────────────────────────────────────────────────

_INICIO_FORMULA = ('=', '+', '-', '@', '\t', '\r')


def celda_csv(valor):
    """Texto que Excel no ejecuta como fórmula (un reporte con '=HYPERLINK(...)' quedaría como texto)."""
    if isinstance(valor, str) and valor.startswith(_INICIO_FORMULA):
        return "'" + valor
    return valor


def fila_csv(valores):
    return [celda_csv(valor) for valor in valores]
