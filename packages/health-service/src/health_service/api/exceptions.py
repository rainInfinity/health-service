from __future__ import annotations

from enum import StrEnum
from math import ceil

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from ninja.errors import HttpError
from ninja.errors import Throttled as NinjaThrottled
from ninja.errors import ValidationError as NinjaValidationError
from ninja_extra import NinjaExtraAPI
from ninja_extra import exceptions as ninja_extra_exceptions

from health_service.application.catalog_exceptions import (
    CatalogConfigurationInvalid,
    CatalogConflict,
    CatalogNotFound,
)
from health_service.application.task_exceptions import (
    InvalidTaskPagination,
    TaskConflict,
    TaskInputInvalid,
    TaskNotFound,
)

from .request_context import require_request_context, update_request_metadata
from .responses import error_response
from .structured_logging import log_unexpected_exception


class ApiErrorCode(StrEnum):
    """对外稳定的业务错误码，不包含任意异常信息。"""

    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_TOKEN = "INVALID_TOKEN"
    CHANNEL_FORBIDDEN = "CHANNEL_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    USERNAME_EXISTS = "USERNAME_EXISTS"
    CONFLICT = "CONFLICT"
    INVALID_INPUT = "INVALID_INPUT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_ERROR_DETAILS: dict[ApiErrorCode, tuple[int, str]] = {
    ApiErrorCode.INVALID_CREDENTIALS: (401, "用户名或密码错误"),
    ApiErrorCode.INVALID_TOKEN: (401, "认证凭证无效"),
    ApiErrorCode.CHANNEL_FORBIDDEN: (403, "无权访问此资源"),
    ApiErrorCode.NOT_FOUND: (404, "接口不存在"),
    ApiErrorCode.METHOD_NOT_ALLOWED: (405, "请求方法不支持"),
    ApiErrorCode.USERNAME_EXISTS: (409, "用户名已存在"),
    ApiErrorCode.CONFLICT: (409, "请求冲突"),
    ApiErrorCode.INVALID_INPUT: (422, "请求参数无效"),
    ApiErrorCode.RATE_LIMITED: (429, "请求过于频繁"),
    ApiErrorCode.INTERNAL_ERROR: (500, "服务器内部错误"),
}


class ApiError(Exception):
    """携带需要映射为固定 API 错误响应的业务错误码。"""

    def __init__(self, code: ApiErrorCode) -> None:
        self.code = code  # 用于映射固定状态和对外文案
        super().__init__(code.value)


def error_code_for_status(status: int) -> ApiErrorCode:
    return {
        400: ApiErrorCode.INVALID_INPUT,
        401: ApiErrorCode.INVALID_TOKEN,
        403: ApiErrorCode.CHANNEL_FORBIDDEN,
        404: ApiErrorCode.NOT_FOUND,
        405: ApiErrorCode.METHOD_NOT_ALLOWED,
        409: ApiErrorCode.CONFLICT,
        422: ApiErrorCode.INVALID_INPUT,
        429: ApiErrorCode.RATE_LIMITED,
    }.get(status, ApiErrorCode.INTERNAL_ERROR)


def error_for_code(request: HttpRequest, code: ApiErrorCode) -> HttpResponse:
    status, message = _ERROR_DETAILS[code]
    return error_response(
        request,
        code=code.value,
        message=message,
        status=status,
    )


def register_exception_handlers(api: NinjaExtraAPI) -> None:
    api.add_exception_handler(ApiError, _handle_business_error)
    api.add_exception_handler(NinjaValidationError, _handle_validation_error)
    api.add_exception_handler(DjangoValidationError, _handle_validation_error)
    api.add_exception_handler(PermissionDenied, _handle_permission_denied)
    api.add_exception_handler(Http404, _handle_not_found)
    api.add_exception_handler(CatalogNotFound, _handle_catalog_not_found)
    api.add_exception_handler(CatalogConflict, _handle_catalog_conflict)
    api.add_exception_handler(
        CatalogConfigurationInvalid,
        _handle_catalog_configuration_invalid,
    )
    api.add_exception_handler(TaskNotFound, _handle_task_not_found)
    api.add_exception_handler(TaskConflict, _handle_task_conflict)
    api.add_exception_handler(TaskInputInvalid, _handle_task_input_invalid)
    api.add_exception_handler(
        InvalidTaskPagination,
        _handle_invalid_task_pagination,
    )
    api.add_exception_handler(HttpError, _handle_http_error)
    api.add_exception_handler(
        ninja_extra_exceptions.APIException, _handle_extra_api_exception
    )
    api.add_exception_handler(Exception, _handle_unexpected_exception)


def _handle_business_error(request: HttpRequest, exception: ApiError) -> HttpResponse:
    return error_for_code(request, exception.code)


def _handle_validation_error(
    request: HttpRequest, exception: Exception
) -> HttpResponse:
    return error_for_code(request, ApiErrorCode.INVALID_INPUT)


def _handle_permission_denied(
    request: HttpRequest, exception: PermissionDenied
) -> HttpResponse:
    return error_for_code(request, ApiErrorCode.CHANNEL_FORBIDDEN)


def _handle_not_found(request: HttpRequest, exception: Http404) -> HttpResponse:
    return error_for_code(request, ApiErrorCode.NOT_FOUND)


def _handle_catalog_not_found(
    request: HttpRequest,
    exception: CatalogNotFound,
) -> HttpResponse:
    """将目录资源缺失映射为统一 404 响应。"""
    return error_for_code(request, ApiErrorCode.NOT_FOUND)


def _handle_catalog_conflict(
    request: HttpRequest,
    exception: CatalogConflict,
) -> HttpResponse:
    """将目录唯一性或不可变冲突映射为统一 409 响应。"""
    return error_for_code(request, ApiErrorCode.CONFLICT)


def _handle_catalog_configuration_invalid(
    request: HttpRequest,
    exception: CatalogConfigurationInvalid,
) -> HttpResponse:
    """将无效目录配置映射为统一 422 响应。"""
    return error_for_code(request, ApiErrorCode.INVALID_INPUT)


def _handle_task_not_found(
    request: HttpRequest,
    exception: TaskNotFound,
) -> HttpResponse:
    """将账号或渠道不匹配的任务查询映射为统一 404 响应。"""
    return error_for_code(request, ApiErrorCode.NOT_FOUND)


def _handle_task_conflict(
    request: HttpRequest,
    exception: TaskConflict,
) -> HttpResponse:
    """将任务状态冲突映射为统一 409 响应。"""
    return error_for_code(request, ApiErrorCode.CONFLICT)


def _handle_task_input_invalid(
    request: HttpRequest,
    exception: TaskInputInvalid,
) -> HttpResponse:
    """将不符合任务输入契约的请求映射为统一 422 响应。"""
    return error_for_code(request, ApiErrorCode.INVALID_INPUT)


def _handle_invalid_task_pagination(
    request: HttpRequest,
    exception: InvalidTaskPagination,
) -> HttpResponse:
    """将越界任务分页映射为统一 422 响应。"""
    return error_for_code(request, ApiErrorCode.INVALID_INPUT)


def _handle_http_error(request: HttpRequest, exception: HttpError) -> HttpResponse:
    response = error_for_code(request, error_code_for_status(exception.status_code))
    if isinstance(exception, NinjaThrottled) and exception.wait is not None:
        response["Retry-After"] = str(ceil(exception.wait))
    return response


def _handle_extra_api_exception(
    request: HttpRequest, exception: ninja_extra_exceptions.APIException
) -> HttpResponse:
    response = error_for_code(request, error_code_for_status(exception.status_code))
    if (
        isinstance(exception, ninja_extra_exceptions.Throttled)
        and exception.wait is not None
    ):
        response["Retry-After"] = str(ceil(exception.wait))
    return response


def _handle_unexpected_exception(
    request: HttpRequest, exception: Exception
) -> HttpResponse:
    context = require_request_context(request)
    update_request_metadata(request, context)
    context.error_code = ApiErrorCode.INTERNAL_ERROR.value
    log_unexpected_exception(context, exception)
    return error_for_code(request, ApiErrorCode.INTERNAL_ERROR)
