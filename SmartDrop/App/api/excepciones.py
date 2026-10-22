"""Respuestas de error de la API en el formato que ya usa la app ({'error': '...'}), en español."""
import math

from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler

from ..seguridad import mensaje_espera


def manejar_excepcion(exc, context):
    respuesta = exception_handler(exc, context)
    if respuesta is not None and isinstance(exc, Throttled):
        espera = max(1, math.ceil(exc.wait or 1))
        respuesta.data = {'ok': False, 'error': mensaje_espera(espera)}
        respuesta['Retry-After'] = str(espera)
    return respuesta
