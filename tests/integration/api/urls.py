"""提供仅供集成测试使用的隔离 API 路由。"""

from __future__ import annotations

import asyncio
import logging

from django.http import HttpResponse, StreamingHttpResponse
from django.urls import path
from ninja import Schema
from ninja.errors import (
    AuthenticationError,
    AuthorizationError,
    Throttled,
)
from ninja_extra import NinjaExtraAPI
from pydantic import Field

from health_service.api.exceptions import (
    ApiError,
    ApiErrorCode,
    register_exception_handlers,
)
from health_service.api.responses import ApiResponse, success_response

_APPLICATION_LOGGER = logging.getLogger("health_service")

api = NinjaExtraAPI(title="隔离 API 测试", version="1.0.0")
register_exception_handlers(api)


class ResultData(Schema):
    value: str


class ProfileData(Schema):
    channel: str
    user_id: int = Field(alias="userId")

    model_config = {"populate_by_name": True}  # noqa: RUF012  # Pydantic Schema 配置


class InputData(Schema):
    value: str


_ERROR_RESPONSES = {
    401: ApiResponse[None],
    403: ApiResponse[None],
    404: ApiResponse[None],
    405: ApiResponse[None],
    409: ApiResponse[None],
    422: ApiResponse[None],
    429: ApiResponse[None],
    500: ApiResponse[None],
}


def _test_auth(request):
    """验证隔离测试请求中的模拟用户凭据。"""
    user_ids = {
        "Bearer test-user-101": 101,
        "Bearer test-user-202": 202,
    }
    user_id = user_ids.get(request.headers.get("Authorization"))
    if user_id is None:
        raise AuthenticationError(message="AUTHENTICATION-SENTINEL")
    return {"user_id": user_id}


@api.get(
    "/success",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def success(request):
    """返回成功路径的隔离 API 回复。"""
    return success_response(request, ResultData(value="ready"))


@api.get(
    "/application-event",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def application_event(request):
    """触发通用应用日志事件并返回成功回复。"""
    _APPLICATION_LOGGER.info(
        "TEST-APPLICATION-MESSAGE-SENTINEL",
        extra={"event": "application.test"},
    )
    return success_response(request, ResultData(value="application-event"))


@api.post(
    "/created",
    response={201: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def created(request):
    """返回创建成功状态的隔离 API 回复。"""
    return success_response(request, ResultData(value="created"), status=201)


@api.delete(
    "/empty",
    response={200: ApiResponse[None], **_ERROR_RESPONSES},
    by_alias=True,
)
def empty(request):
    """验证空载荷回复。"""
    return success_response(request, None)


@api.post(
    "/validate",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def validate(request, body: InputData):
    """返回请求 Schema 验证通过的测试值。"""
    return success_response(request, ResultData(value=body.value))


@api.get(
    "/conflict",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def conflict(request):
    """触发已知用户名冲突错误。"""
    raise ApiError(ApiErrorCode.USERNAME_EXISTS)


@api.get(
    "/permission",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def permission(request):
    """触发授权失败错误。"""
    raise AuthorizationError(message="PERMISSION-SENTINEL")


@api.get(
    "/invalid-credentials",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def invalid_credentials(request):
    """触发无效凭据错误。"""
    raise ApiError(ApiErrorCode.INVALID_CREDENTIALS)


@api.get(
    "/generic-conflict",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def generic_conflict(request):
    """触发通用冲突错误。"""
    raise ApiError(ApiErrorCode.CONFLICT)


@api.get(
    "/rate-limited",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def rate_limited(request):
    """触发限流错误。"""
    raise Throttled(wait=2)


@api.get(
    "/failure",
    response={200: ApiResponse[ResultData], **_ERROR_RESPONSES},
    by_alias=True,
)
def failure(request):
    """触发未预期异常以验证 500 日志边界。"""
    raise RuntimeError("EXCEPTION-SENTINEL-病史-手机号 SQL-SENTINEL /private/config/path")


@api.get(
    "/protected/{channel}",
    auth=_test_auth,
    response={200: ApiResponse[ProfileData], **_ERROR_RESPONSES},
    by_alias=True,
)
async def protected(request, channel: str):
    """异步返回受保护用户的渠道资料。"""
    await asyncio.sleep(0.01 if channel == "android-cn" else 0)
    return success_response(
        request,
        ProfileData(channel=channel, user_id=request.auth["user_id"]),
    )


def _test_page(request):
    """提供不包装的 HTML 测试页面。"""
    return HttpResponse("<html>admin</html>", content_type="text/html")


def _test_stream(request):
    """提供不包装的文件流测试响应。"""
    return StreamingHttpResponse(iter([b"download"]), content_type="application/octet-stream")


urlpatterns = [
    path("api/v1/", api.urls),
    path("admin/test-page", _test_page),
    path("download", _test_stream),
]
