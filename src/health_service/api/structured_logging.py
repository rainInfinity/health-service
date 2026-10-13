"""结构化处理 API 与通用应用日志。"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .request_context import RequestContext

_API_LOGGER = logging.getLogger("health_service.api")
_API_EVENT_NAMES = frozenset({"request.completed", "request.exception"})
_APPLICATION_EVENT_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
_API_LOG_FIELDS = (
    "requestId",
    "method",
    "route",
    "status",
    "durationMs",
    "channel",
    "userId",
    "errorCode",
    "exceptionType",
    "locations",
)


class StandardOutputHandler(logging.StreamHandler):
    """将日志写入当前标准输出。"""

    def emit(self, record: logging.LogRecord) -> None:
        """发出日志前绑定当前 stdout，兼容运行时捕获。"""
        self.stream = sys.stdout
        super().emit(record)


class StructuredApiFormatter(logging.Formatter):
    """使用白名单字段序列化 API 与通用应用日志。"""

    def format(self, record: logging.LogRecord) -> str:
        """API 记录输出请求白名单，应用事件只输出稳定事件名。"""
        is_api_record = record.name == _API_LOGGER.name or record.name.startswith(
            f"{_API_LOGGER.name}."
        )
        event = getattr(record, "event", None)
        if is_api_record:
            stable_event = (
                event
                if isinstance(event, str) and event in _API_EVENT_NAMES
                else "api.event"
            )
            fields = _API_LOG_FIELDS
        else:
            stable_event = (
                event
                if isinstance(event, str)
                and len(event) <= 64
                and _APPLICATION_EVENT_PATTERN.fullmatch(event)
                else "application.event"
            )
            fields = ()

        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": stable_event,
        }
        for field in fields:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def log_request_completed(context: RequestContext, status: int, duration_ms: float) -> None:
    """记录请求完成状态和关联字段。"""
    _API_LOGGER.info(
        "request.completed",
        extra={
            "event": "request.completed",
            "requestId": context.request_id,
            "method": context.method,
            "route": context.route,
            "status": status,
            "durationMs": round(duration_ms, 3),
            "channel": context.channel,
            "userId": context.user_id,
            "errorCode": context.error_code,
        },
    )


def log_unexpected_exception(context: RequestContext, exception: Exception) -> None:
    """记录异常类型与有限调用位置，不记录异常正文。"""
    if context.exception_recorded:
        return
    context.exception_recorded = True

    locations = [
        {
            "module": Path(frame.filename).stem,
            "function": frame.name,
            "line": frame.lineno,
        }
        for frame in traceback.extract_tb(exception.__traceback__)[-8:]
    ]
    _API_LOGGER.error(
        "request.exception",
        extra={
            "event": "request.exception",
            "requestId": context.request_id,
            "method": context.method,
            "route": context.route,
            "errorCode": context.error_code or "INTERNAL_ERROR",
            "exceptionType": type(exception).__name__,
            "locations": locations,
        },
    )
