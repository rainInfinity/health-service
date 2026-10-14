"""提供 v1 路由共用的渠道边界校验。"""

from django.http import Http404, HttpRequest

from health_service.api.exceptions import ApiError, ApiErrorCode
from health_service.domain.channels import ORDINARY_CHANNELS, ChannelLabel


def require_ordinary_channel(channel: str) -> None:
    """将未知及管理渠道映射为不存在的普通渠道入口。"""
    if channel not in ORDINARY_CHANNELS:
        raise Http404

def require_auth_channel(channel: str) -> None:
    """允许公开认证入口使用普通或管理渠道，但不开放其他渠道。"""
    if channel not in ORDINARY_CHANNELS and channel != ChannelLabel.MANAGER.value:
        raise Http404


def require_manager_channel(request: HttpRequest, channel: str) -> None:
    """逐请求检查管理渠道、账号可用状态及当前管理权限。"""
    if channel != ChannelLabel.MANAGER.value:
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN)
    request.api_channel = channel
    if getattr(request, "authenticated_channel", None) != channel:
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN)
    user = getattr(request, "user", None)
    if (
        not getattr(user, "is_authenticated", False)
        or not getattr(user, "is_active", False)
        or not getattr(user, "is_staff", False)
    ):
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN)


def require_authenticated_channel(request: HttpRequest, channel: str) -> None:
    """限制本人路由只能使用与有效 Access 一致的普通渠道。"""
    require_ordinary_channel(channel)
    request.api_channel = channel
    if getattr(request, "authenticated_channel", None) != channel:
        raise ApiError(ApiErrorCode.CHANNEL_FORBIDDEN)
