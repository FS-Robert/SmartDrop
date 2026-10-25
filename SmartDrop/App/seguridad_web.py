"""Reglas de seguridad que la página le manda al navegador en cada respuesta.

* Qué fuentes externas se aceptan: solo las letras de Google y, de cdn.jsdelivr.net, las dos librerías que se
  usan (Chart.js y html2canvas-pro, cada una con su versión). jsDelivr sirve cualquier paquete de npm o GitHub,
  así que permitir el dominio entero dejaba cargar cualquier script de ahí.
* Nadie puede mostrar SmartDrop dentro de otra página (evita engaños con botones escondidos).
* El micrófono solo lo puede pedir la propia página (para Gotita); cámara, ubicación y pagos, nadie.
"""
from asgiref.sync import iscoroutinefunction, markcoroutinefunction

POLITICA_CONTENIDO = '; '.join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net/npm/chart.js@4.4.0/ "
    "https://cdn.jsdelivr.net/npm/html2canvas-pro@1.5.8/ https://cdn.jsdelivr.net/npm/mermaid@11.4.1/",
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
