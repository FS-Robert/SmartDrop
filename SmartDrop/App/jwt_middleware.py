from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .api.authentication import MobileUser


@database_sync_to_async
def user_from_token(token_value):
    try:
        return MobileUser(AccessToken(token_value).payload)
    except TokenError:
        return None


class JWTWebSocketMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers', []))
        authorization = headers.get(b'authorization', b'').decode('utf-8')
        if authorization.startswith('Bearer '):
            scope['user'] = await user_from_token(authorization.split(' ', 1)[1])
        return await super().__call__(scope, receive, send)
