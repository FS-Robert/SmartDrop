from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler as drf_exception_handler

from ..ratelimit import MENSAJE_LIMITE


def exception_handler(exc, context):
    """Igual que el de DRF, pero el error 429 sale en español y con la clave `error` que lee la app."""
    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        segundos = int(exc.wait) if exc.wait else 60
        mensaje = MENSAJE_LIMITE.format(segundos=max(1, segundos))
        response.data = {'error': mensaje, 'detail': mensaje}
    return response
