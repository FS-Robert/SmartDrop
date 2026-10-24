"""
ASGI config for SmartDrop project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SmartDrop.settings')

django_asgi_application = get_asgi_application()

from App.routing import websocket_urlpatterns
from App.jwt_middleware import JWTWebSocketMiddleware

websocket_router = URLRouter(websocket_urlpatterns)
# La app se autentica con su token; el navegador, con la cookie de sesión. Esas conexiones solo se aceptan si
# la página que las abre es de un dominio de ALLOWED_HOSTS: otra página no puede usar tu sesión para leer el chat.
app_websocket = JWTWebSocketMiddleware(websocket_router)
navegador_websocket = AllowedHostsOriginValidator(AuthMiddlewareStack(websocket_router))


def websocket_application(scope, receive, send):
	headers = dict(scope.get('headers', []))
	has_bearer = headers.get(b'authorization', b'').lower().startswith(b'bearer ')
	return (app_websocket if has_bearer else navegador_websocket)(scope, receive, send)

application = ProtocolTypeRouter({
	'http': django_asgi_application,
	'websocket': websocket_application,
})
