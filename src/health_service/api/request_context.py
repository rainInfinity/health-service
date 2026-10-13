from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from health_service.domain.channels import ORDINARY_CHANNELS


@dataclass(slots=True)
class RequestContext:
    request_id: str  # 本次请求的服务端关联标识
    method: str  # HTTP 方法
    started_at: float  # 单调时钟起点，用于计算耗时
    route: str = "<unmatched>"  # Django 匹配到的路由模板；未匹配时为固定标记
    channel: str | None = None  # 已确认的普通渠道标签
    user_id: str | None = None  # 已鉴权用户的稳定标识
    error_code: str | None = None  # 已知错误的稳定业务码
    exception_recorded: bool = False  # 防止同一请求的异常被重复记录


_current_request: ContextVar[RequestContext | None] = ContextVar(
    "health_service_api_request", default=None
)


def activate_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _current_request.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _current_request.reset(token)


def current_request_context(request: Any | None = None) -> RequestContext | None:
    if request is not None:
        context = getattr(request, "_api_request_context", None)
        if context is not None:
            return context
    return _current_request.get()


def require_request_context(request: Any | None = None) -> RequestContext:
    context = current_request_context(request)
    if context is None:
        raise RuntimeError("API request context is not active")
    return context


def update_request_metadata(request: Any, context: RequestContext | None = None) -> None:
    context = context or require_request_context(request)
    match = getattr(request, "resolver_match", None)
    if match is not None:
        route = getattr(match, "route", None)
        if isinstance(route, str) and route.startswith("api/v1/") and len(route) <= 256:
            context.route = route
        kwargs = getattr(match, "kwargs", {})
        channel = kwargs.get("channel") if isinstance(kwargs, dict) else None
        if channel not in ORDINARY_CHANNELS:
            channel = getattr(request, "api_channel", None)
        if channel in ORDINARY_CHANNELS:
            context.channel = channel

    user_id = authenticated_user_id(request)
    if user_id is not None:
        context.user_id = user_id


def authenticated_user_id(request: Any) -> str | None:
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        value = _stable_identifier(getattr(user, "pk", None))
        if value is not None:
            return value

    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        return _stable_identifier(auth.get("user_id"))
    return _stable_identifier(getattr(auth, "user_id", None))


def _stable_identifier(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, UUID)):
        return str(value)
    return None
