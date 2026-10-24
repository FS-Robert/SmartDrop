"""Reglas de seguridad que la página le manda al navegador en cada respuesta.

* Qué fuentes externas se aceptan: solo las letras de Google, las gráficas y la foto de pantalla de Gotita
  (cdn.jsdelivr.net). Cualquier otro script de afuera se bloquea, aunque alguien logre meterlo en la página.
* Nadie puede mostrar SmartDrop dentro de otra página (evita engaños con botones escondidos).
* El micrófono solo lo puede pedir la propia página (para Gotita); cámara, ubicación y pagos, nadie.
"""
from asgiref.sync import iscoroutinefunction, markcoroutinefunction

POLITICA_CONTENIDO = '; '.join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob:",
    "connect-src 'self' ws: wss:",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
))

PERMISOS = 'microphone=(self), camera=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=()'


class CabecerasDeSeguridad:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self._despues_async(request)
        return self._poner(self.get_response(request))

    async def _despues_async(self, request):
        return self._poner(await self.get_response(request))

    @staticmethod
    def _poner(response):
        response.headers.setdefault('Content-Security-Policy', POLITICA_CONTENIDO)
        response.headers.setdefault('Permissions-Policy', PERMISOS)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        return response
