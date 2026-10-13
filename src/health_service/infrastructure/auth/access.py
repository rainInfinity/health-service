"""提供只接受 Access Token 的异步 Bearer 鉴权。"""

from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from ninja_jwt.authentication import AsyncJWTAuth


class ChannelAccessAuth(AsyncJWTAuth):
    """验证有效账号并将令牌渠道提供给路由做精确匹配。"""

    async def authenticate(self, request: HttpRequest, token: str) -> Any:
        request.user = AnonymousUser()
        get_validated_token = sync_to_async(self.get_validated_token, thread_sensitive=True)
        validated_token = await get_validated_token(token)
        get_user = sync_to_async(self.get_user, thread_sensitive=True)
        user = await get_user(validated_token)
        request.user = user
        request.authenticated_channel = validated_token.get("channel")
        return user
