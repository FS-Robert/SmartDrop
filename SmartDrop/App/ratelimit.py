"""Límite de peticiones (rate limiting) para las vistas web de Django y para la API REST de la app.

* Vistas Django (formularios web, /api/login/, /api/lecturas/, comandos de válvula…): decorador `rate_limit`.
* API DRF (/auth/*, /api/*): clases de `App.api.throttling`, que usan `client_ip` y `rate_for` de aquí.

Los contadores viven en la caché de Django (LocMemCache). El servicio corre en un solo proceso de Daphne
(ver deploy/smartdrop.service), así que el conteo es exacto; si algún día se usan varios procesos hay que
poner Redis como caché para que compartan el contador. Nginx aplica además un límite general por IP
(deploy/nginx-smartdrop.conf).

Los límites se pueden ajustar sin tocar código con la variable de entorno RATELIMIT_OVERRIDES, por ejemplo
`RATELIMIT_OVERRIDES=login_ip=40/m,registro=50/h`, o apagar con RATELIMIT_ENABLED=0 (solo para pruebas).
"""
import functools
import hashlib
import logging
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

_PERIODOS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
_RATE_RE = re.compile(r'^\s*(\d+)\s*/\s*([smhd])[a-z]*\s*$', re.IGNORECASE)


def client_ip(request):
    """IP del cliente.

    Daphne corre con --proxy-headers y Nginx le manda `X-Forwarded-For $remote_addr` (sobrescrito, no
    agregado), así que REMOTE_ADDR ya es la IP real y el cliente no la puede falsificar con su propia
    cabecera X-Forwarded-For. Por eso aquí no se lee X-Forwarded-For directamente.
    """
    return (request.META.get('REMOTE_ADDR') or '').strip() or 'desconocida'


def parse_rate(rate):
    """'10/m' → (10, 60). Acepta s, m, h, d (y también 'min', 'hour', 'day')."""
    match = _RATE_RE.match(str(rate or ''))
    if not match:
        raise ValueError(f'Límite inválido: {rate!r}. Usa el formato N/s, N/m, N/h o N/d.')
    return int(match.group(1)), _PERIODOS[match.group(2).lower()]


def rate_for(scope, default):
    """Límite configurado para `scope` (RATELIMIT_OVERRIDES en el entorno) o el valor por defecto."""
    return (getattr(settings, 'RATELIMIT_OVERRIDES', {}) or {}).get(scope, default)


def enabled():
    return getattr(settings, 'RATELIMIT_ENABLED', True)


def hit(scope, ident, rate):
    """Cuenta una petición. Devuelve (permitida, segundos_para_reintentar)."""
    limit, period = parse_rate(rate)
    now = time.time()
    window = int(now // period)
    digest = hashlib.sha256(f'{scope}:{ident}'.encode('utf-8')).hexdigest()[:32]
    key = f'rl:{scope}:{digest}:{window}'
    cache.add(key, 0, timeout=period + 5)
    try:
        count = cache.incr(key)
    except ValueError:
        # La clave expiró justo entre add() e incr().
        cache.set(key, 1, timeout=period + 5)
        count = 1
    retry_after = max(1, int(period - (now % period)))
    return count <= limit, retry_after


def _quiere_json(request):
    accept = request.headers.get('Accept', '')
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in accept
        or (request.content_type or '').startswith('application/json')
        or request.path.startswith(('/api/', '/auth/'))
    )


MENSAJE_LIMITE = 'Demasiadas solicitudes. Espera {segundos} segundos e inténtalo de nuevo.'


def respuesta_limite(request, retry_after):
    mensaje = MENSAJE_LIMITE.format(segundos=retry_after)
    if _quiere_json(request):
        response = JsonResponse({'ok': False, 'error': mensaje}, status=429)
    else:
        response = HttpResponse(
            '<!doctype html><html lang="es"><meta charset="utf-8"><title>Demasiadas solicitudes</title>'
            f'<body style="font-family:sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem">'
            f'<h1>Demasiadas solicitudes</h1><p>{mensaje}</p><p><a href="javascript:history.back()">Volver</a></p>',
            status=429, content_type='text/html; charset=utf-8',
        )
    response['Retry-After'] = str(retry_after)
    return response


def _key_ip(request):
    return f'ip:{client_ip(request)}'


def _key_user(request):
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return f'u:{user.pk}'
    return _key_ip(request)


_KEYS = {'ip': _key_ip, 'user': _key_user}


def rate_limit(scope, rate, key='ip', methods=('POST',), on_limit=None):
    """Decorador para vistas Django.

    `key` es 'ip', 'user' o una función request → texto (None = no limitar esta petición).
    `methods` son los métodos HTTP que cuentan (por defecto solo POST; None = todos).
    `on_limit(request, retry_after)` permite responder con la misma página y un mensaje de error.
    """
    key_func = _KEYS[key] if isinstance(key, str) else key
    parse_rate(rate)  # falla al importar si el límite está mal escrito

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if enabled() and (methods is None or request.method in methods):
                ident = key_func(request)
                if ident is not None:
                    permitida, retry_after = hit(scope, ident, rate_for(scope, rate))
                    if not permitida:
                        logger.warning(
                            'Límite de peticiones "%s" alcanzado por %s en %s', scope, client_ip(request), request.path,
                        )
                        if on_limit is not None:
                            return on_limit(request, retry_after)
                        return respuesta_limite(request, retry_after)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def correo_de_la_peticion(request):
    """Correo del intento de login (formulario o JSON), en minúsculas; None si no viene."""
    correo = request.POST.get('email') or request.POST.get('correo')
    if not correo and (request.content_type or '').startswith('application/json'):
        try:
            import json
            data = json.loads(request.body.decode('utf-8') or '{}')
            if isinstance(data, dict):
                correo = data.get('email') or data.get('correo')
        except (ValueError, UnicodeDecodeError):
            correo = None
    correo = str(correo or '').strip().lower()
    return f'correo:{correo}' if correo else None
