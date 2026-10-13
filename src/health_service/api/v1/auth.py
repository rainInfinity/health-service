"""实现普通渠道公开注册和密码登录 HTTP 入口。"""

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router

from health_service.api.exceptions import ApiError, ApiErrorCode
from health_service.api.responses import ApiResponse, success_response
from health_service.application.accounts import (
    UsernameAlreadyExists,
    login_user,
    logout_user,
    refresh_user_tokens,
    register_user,
)
from health_service.application.exceptions import (
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshChannelMismatch,
)
from health_service.schemas.requests.accounts import (
    AccountCredentialsRequest,
    RefreshTokenRequest,
)
from health_service.schemas.responses.accounts import (
    TokenPairResponse,
    UserPublicResponse,
)

from .common import require_ordinary_channel
from .contracts import ERROR_RESPONSES

router = Router()


@router.post(
    "/{channel}/auth/register",
    response={201: ApiResponse[UserPublicResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def register(request: HttpRequest, channel: str, body: AccountCredentialsRequest):
    """在普通渠道注册共用账号并返回公开账号字段。"""
    require_ordinary_channel(channel)
    request.api_channel = channel
    try:
        user = await sync_to_async(register_user, thread_sensitive=True)(
            username=body.username,
            password=body.password,
        )
    except UsernameAlreadyExists as error:
        raise ApiError(ApiErrorCode.USERNAME_EXISTS) from error
    return success_response(
        request,
        UserPublicResponse(user_id=user.pk, username=user.username),
        status=201,
    )


@router.post(
    "/{channel}/auth/login",
    response={200: ApiResponse[TokenPairResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def login(request: HttpRequest, channel: str, body: AccountCredentialsRequest):
    """验证密码并签发只适用于当前渠道的双 Token。"""
    require_ordinary_channel(channel)
    request.api_channel = channel
    try:
        tokens = await sync_to_async(login_user, thread_sensitive=True)(
            username=body.username,
            password=body.password,
            channel=channel,
        )
    except InvalidCredentials as error:
        raise ApiError(ApiErrorCode.INVALID_CREDENTIALS) from error
    return success_response(
        request,
        TokenPairResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        ),
    )


@router.post(
    "/{channel}/auth/refresh",
    response={200: ApiResponse[TokenPairResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def refresh(request: HttpRequest, channel: str, body: RefreshTokenRequest):
    """原子轮换当前渠道的 Refresh 并返回新令牌对。"""
    require_ordinary_channel(channel)
    request.api_channel = channel
    try:
        tokens = await sync_to_async(refresh_user_tokens, thread_sensitive=True)(
            raw_token=body.refresh_token,
            channel=channel,
        )
    except InvalidRefreshToken as error:
        raise ApiError(ApiErrorCode.INVALID_TOKEN) from error
    except RefreshChannelMismatch as error:
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN) from error
    return success_response(
        request,
        TokenPairResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        ),
    )


@router.post(
    "/{channel}/auth/logout",
    response={200: ApiResponse[None], **ERROR_RESPONSES},
    by_alias=True,
)
async def logout(request: HttpRequest, channel: str, body: RefreshTokenRequest):
    """只撤销请求渠道当前提交的 Refresh。"""
    require_ordinary_channel(channel)
    request.api_channel = channel
    try:
        await sync_to_async(logout_user, thread_sensitive=True)(
            raw_token=body.refresh_token,
            channel=channel,
        )
    except InvalidRefreshToken as error:
        raise ApiError(ApiErrorCode.INVALID_TOKEN) from error
    except RefreshChannelMismatch as error:
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN) from error
    return success_response(request, None)
