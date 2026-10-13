from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from ninja import Schema
from ninja.responses import Status
from pydantic import ConfigDict, Field

from .request_context import require_request_context


class ApiResponse[PayloadT](Schema):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    code: str  # 对外稳定业务码
    message: str  # 面向客户端的固定文案
    data: PayloadT  # 业务载荷；错误响应中为 null
    request_id: str = Field(alias="requestId")  # 服务端请求关联标识


def success_response[PayloadT](
    request: HttpRequest,
    data: PayloadT,
    *,
    status: int = 200,
    message: str = "成功",
) -> Status[ApiResponse[PayloadT]]:
    context = require_request_context(request)
    return Status(
        status,
        ApiResponse(
            code="OK",
            message=message,
            data=data,
            request_id=context.request_id,
        ),
    )


def error_response(
    request: HttpRequest,
    *,
    code: str,
    message: str,
    status: int,
) -> JsonResponse:
    context = require_request_context(request)
    context.error_code = code
    request._api_response_handled = True
    return JsonResponse(
        {
            "code": code,
            "message": message,
            "data": None,
            "requestId": context.request_id,
        },
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )
