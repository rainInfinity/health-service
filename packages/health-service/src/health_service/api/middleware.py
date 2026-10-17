from __future__ import annotations

import time
from uuid import uuid4

from asgiref.sync import iscoroutinefunction
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

from .exceptions import ApiErrorCode, error_code_for_status, error_for_code
from .request_context import (
    RequestContext,
    activate_request_context,
    reset_request_context,
    update_request_metadata,
)
from .structured_logging import log_request_completed, log_unexpected_exception

_API_PREFIX = "/api/v1/"
_ERROR_STATUSES = frozenset({400, 401, 403, 404, 405, 409, 422, 429})
_NON_JSON_ROUTE_NAMES = frozenset({"openapi-json", "openapi-view"})


@sync_and_async_middleware
def api_request_middleware(get_response):
    if iscoroutinefunction(get_response):

        async def middleware(request: HttpRequest) -> HttpResponse:
            if not request.path_info.startswith(_API_PREFIX):
                return await get_response(request)
            return await _handle_async(request, get_response)

    else:

        def middleware(request: HttpRequest) -> HttpResponse:
            if not request.path_info.startswith(_API_PREFIX):
                return get_response(request)
            return _handle_sync(request, get_response)

    return middleware


def _start_request(request: HttpRequest) -> tuple[RequestContext, object]:
    context = RequestContext(
        request_id=uuid4().hex,
        method=request.method,
        started_at=time.monotonic(),
    )
    request._api_request_context = context
    return context, activate_request_context(context)


def _finish_request(
    request: HttpRequest,
    response: HttpResponse,
    context: RequestContext,
) -> HttpResponse:
    update_request_metadata(request, context)
    if (
        response.status_code in _ERROR_STATUSES or response.status_code >= 500
    ) and not getattr(request, "_api_response_handled", False):
        route_name = getattr(getattr(request, "resolver_match", None), "url_name", None)
        if route_name not in _NON_JSON_ROUTE_NAMES:
            allowed = response.get("Allow")
            response = error_for_code(
                request,
                ApiErrorCode.INTERNAL_ERROR
                if response.status_code >= 500
                else error_code_for_status(response.status_code),
            )
            if allowed:
                response["Allow"] = allowed

    response["X-Request-ID"] = context.request_id
    duration_ms = (time.monotonic() - context.started_at) * 1000
    log_request_completed(context, response.status_code, duration_ms)
    return response


def _handle_sync(request: HttpRequest, get_response) -> HttpResponse:
    context, token = _start_request(request)
    try:
        try:
            response = get_response(request)
        except Exception as exception:  # noqa: BLE001  # API 边界兜底，避免信息泄漏
            update_request_metadata(request, context)
            context.error_code = ApiErrorCode.INTERNAL_ERROR.value
            log_unexpected_exception(context, exception)
            response = error_for_code(request, ApiErrorCode.INTERNAL_ERROR)
        return _finish_request(request, response, context)
    finally:
        reset_request_context(token)


async def _handle_async(request: HttpRequest, get_response) -> HttpResponse:
    context, token = _start_request(request)
    try:
        try:
            response = await get_response(request)
        except Exception as exception:  # noqa: BLE001  # API 边界兜底，避免信息泄漏
            update_request_metadata(request, context)
            context.error_code = ApiErrorCode.INTERNAL_ERROR.value
            log_unexpected_exception(context, exception)
            response = error_for_code(request, ApiErrorCode.INTERNAL_ERROR)
        return _finish_request(request, response, context)
    finally:
        reset_request_context(token)
