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
_LEVEL_COLORS = {
    logging.DEBUG: "36",
    logging.INFO: "32",
    logging.WARNING: "33",
    logging.ERROR: "31",
    logging.CRITICAL: "1;31",
}
_STATUS_COLORS = {2: "32", 3: "36", 4: "33", 5: "31"}


class StandardOutputHandler(logging.StreamHandler):
    """将日志写入当前标准输出。"""

    def emit(self, record: logging.LogRecord) -> None:
        """发出日志前绑定当前 stdout，兼容运行时捕获。"""
        self.stream = sys.stdout
        super().emit(record)


def _structured_entry(record: logging.LogRecord) -> dict[str, Any]:
    """从日志记录中构造只含安全字段的结构化条目。"""
    is_api_record = record.name == _API_LOGGER.name or record.name.startswith(
        f"{_API_LOGGER.name}."
    )
    event = getattr(record, "event", None)
    if is_api_record:
        stable_event = (
            event if isinstance(event, str) and event in _API_EVENT_NAMES else "api.event"
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
    return entry


def _format_console_value(value: Any) -> str:
    """按单行键值格式编码字段值，避免空格破坏字段边界。"""
    if isinstance(value, str):
        if value and not any(char.isspace() or char in '="' or char == "\\" for char in value):
            return value
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dict, list)):
        encoded_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return json.dumps(encoded_json, ensure_ascii=False)
    return str(value)


def _colorize(value: str, color: str | None, enabled: bool) -> str:
    """仅在终端输出时为字段添加 ANSI 颜色。"""
    if not enabled or color is None:
        return value
    return f"\x1b[{color}m{value}\x1b[0m"


class StructuredApiFormatter(logging.Formatter):
    """将安全日志字段序列化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """生成 API 或通用应用事件的 JSON 记录。"""
        return json.dumps(
            _structured_entry(record),
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ConsoleApiFormatter(logging.Formatter):
    """生成人类可读的单行键值日志，并在交互终端高亮。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化安全字段；非终端输出不含 ANSI 控制码。"""
        entry = _structured_entry(record)
        is_tty = getattr(sys.stdout, "isatty", lambda: False)()
        parts = []
        for key, value in entry.items():
            field = f"{key}={_format_console_value(value)}"
            if key == "level":
                field = _colorize(field, _LEVEL_COLORS.get(record.levelno), is_tty)
            elif key == "event":
                field = _colorize(field, "36", is_tty)
            elif key == "status" and isinstance(value, int):
                field = _colorize(
                    field,
                    _STATUS_COLORS.get(value // 100),
                    is_tty,
                )
            parts.append(field)
        return " ".join(parts)


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
