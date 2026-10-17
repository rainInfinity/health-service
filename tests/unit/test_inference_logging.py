"""验证新日志前缀、北京时间换算与推理元数据的安全边界。"""

import json
import logging
from datetime import UTC, datetime

import pytest
from health_service.api.structured_logging import ConsoleApiFormatter, StructuredApiFormatter


def test_console_and_json_share_beijing_timestamp_and_millisecond_duration():
    """日志跨日转换且不进位，HTTP 状态优先但耗时精度不受影响。"""
    record = logging.LogRecord("health_service.api", logging.INFO, __file__, 1, "ignored", (), None)
    record.created = datetime(2026, 10, 10, 18, 19, 19, 999999, tzinfo=UTC).timestamp()
    record.event = "request.completed"
    record.status = 200
    record.durationMs = 0.125
    console = ConsoleApiFormatter().format(record)
    structured = json.loads(StructuredApiFormatter().format(record))
    assert console == "2026-10-11 02:19:19 INFO status=200 event=request.completed durationMs=0.125"
    assert structured == {
        "timestamp": "2026-10-11 02:19:19",
        "level": "INFO",
        "event": "request.completed",
        "status": 200,
        "durationMs": 0.125,
    }


@pytest.mark.parametrize("namespace", ["health_service.inference", "health_service"])
def test_inference_fields_are_only_exposed_in_its_own_namespace(namespace):
    """推理关联字段不能向普通日志开放，任意正文和令牌始终被丢弃。"""
    record = logging.LogRecord(namespace, logging.ERROR, __file__, 1, "SENSITIVE-MESSAGE", (), None)
    for field, value in {
        "event": "inference.task.failed",
        "consumer": "worker-1",
        "mode": "once",
        "taskId": 42,
        "messageId": "123-0",
        "durationMs": 12.375,
        "errorCode": "INFERENCE.INVALID_INPUT",
        "result": "SENSITIVE-RESULT",
        "executionToken": "SENSITIVE-TOKEN",
        "input": "SENSITIVE-HEALTH",
        "path": "/SENSITIVE-PATH",
        "exceptionType": "SENSITIVE-EXCEPTION",
        "reason": "SENSITIVE-REASON",
    }.items():
        setattr(record, field, value)
    text = StructuredApiFormatter().format(record)
    console = ConsoleApiFormatter().format(record)
    entry = json.loads(text)
    if namespace == "health_service.inference":
        assert entry["taskId"] == 42 and entry["messageId"] == "123-0"
        assert entry["consumer"] == "worker-1" and entry["mode"] == "once"
        assert entry["durationMs"] == 12.375
        assert entry["errorCode"] == "INFERENCE.INVALID_INPUT"
    else:
        assert "taskId" not in entry and "consumer" not in entry
    assert "SENSITIVE" not in text + console
    assert "status=" not in console


def test_malformed_inference_identifiers_are_not_echoed():
    """畸形关联信息及未知诊断不会让 formatter 泄露原值或中断输出。"""
    record = logging.LogRecord(
        "health_service.inference", logging.WARNING, __file__, 1, "ignored", (), None
    )
    record.event = "inference.message.received"
    record.taskId = "HEALTH-SENTINEL"
    record.messageId = "PATH-SENTINEL"
    record.mode = ["INVALID"]
    record.reason = "PRIVATE-SENTINEL"
    entry = json.loads(StructuredApiFormatter().format(record))
    assert set(entry) == {"timestamp", "level", "event"}
