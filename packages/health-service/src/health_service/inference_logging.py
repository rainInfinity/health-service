"""定义推理事件、固定诊断及专属安全日志字段。"""

import logging
import math
import re
from typing import Any

_LOGGER = logging.getLogger("health_service.inference")
_EVENT_LEVELS = {
    "inference.worker.starting": logging.INFO,
    "inference.worker.ready": logging.INFO,
    "inference.worker.stopped": logging.INFO,
    "inference.worker.error": logging.ERROR,
    "inference.message.received": logging.INFO,
    "inference.task.started": logging.INFO,
    "inference.task.succeeded": logging.INFO,
    "inference.task.failed": logging.ERROR,
    "inference.task.lease_lost": logging.WARNING,
    "inference.dispatch.failed": logging.WARNING,
    "inference.output.publish_failed": logging.WARNING,
}
_REASONS = frozenset(
    {
        "database_error",
        "redis_error",
        "lease_expired",
        "heartbeat_error",
        "connection_cleanup_failed",
        "ownership_rejected",
        "dispatch_publish_failed",
        "dispatch_marker_failed",
        "output_publish_failed",
    }
)
_EXCEPTION_TYPES = frozenset({"DatabaseError", "RedisError"})
_MESSAGE_ID = re.compile(r"[0-9]{1,20}-[0-9]{1,20}\Z")
_ERROR_CODE = re.compile(r"(?:TASK|INFERENCE|RESULT)\.[A-Z_]{1,48}\Z")


def inference_log_fields(record: logging.LogRecord) -> dict[str, Any]:
    """只为合法推理事件提取有界标识与固定诊断，忽略其他 extra。"""
    event = getattr(record, "event", None)
    if (
        not (record.name == _LOGGER.name or record.name.startswith(f"{_LOGGER.name}."))
        or not isinstance(event, str)
        or event not in _EVENT_LEVELS
    ):
        return {}
    fields: dict[str, Any] = {}
    consumer = getattr(record, "consumer", None)
    if (
        isinstance(consumer, str)
        and 0 < len(consumer) <= 256
        and all(char.isprintable() for char in consumer)
    ):
        fields["consumer"] = consumer
    mode = getattr(record, "mode", None)
    if isinstance(mode, str) and mode in {"once", "continuous"}:
        fields["mode"] = mode
    task_id = getattr(record, "taskId", None)
    if type(task_id) is int and 0 < task_id < (1 << 63):
        fields["taskId"] = task_id
    message_id = getattr(record, "messageId", None)
    if isinstance(message_id, str) and _MESSAGE_ID.fullmatch(message_id):
        fields["messageId"] = message_id
    duration = getattr(record, "durationMs", None)
    if type(duration) in {int, float} and math.isfinite(duration) and duration >= 0:
        fields["durationMs"] = round(duration, 3)
    error_code = getattr(record, "errorCode", None)
    if (
        record.event == "inference.task.failed"
        and isinstance(error_code, str)
        and _ERROR_CODE.fullmatch(error_code)
    ):
        fields["errorCode"] = error_code
    reason = getattr(record, "reason", None)
    if isinstance(reason, str) and reason in _REASONS:
        fields["reason"] = reason
    exception_type = getattr(record, "exceptionType", None)
    if isinstance(exception_type, str) and exception_type in _EXCEPTION_TYPES:
        fields["exceptionType"] = exception_type
    return fields


def log_inference_event(
    event: str,
    *,
    consumer: str | None = None,
    mode: str | None = None,
    task_id: int | None = None,
    message_id: str | None = None,
    duration_ms: float | None = None,
    error_code: str | None = None,
    reason: str | None = None,
    exception_type: str | None = None,
) -> None:
    """以固定级别记录推理事件，不接收原始异常、令牌或业务载荷。"""
    _LOGGER.log(
        _EVENT_LEVELS[event],
        event,
        extra={
            "event": event,
            "consumer": consumer,
            "mode": mode,
            "taskId": task_id,
            "messageId": message_id,
            "durationMs": duration_ms,
            "errorCode": error_code,
            "reason": reason,
            "exceptionType": exception_type,
        },
    )
