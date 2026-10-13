"""验证日志处理器的本地轮转策略和归档清理。"""

import logging
import time
from logging.handlers import TimedRotatingFileHandler


def test_configured_log_handlers_rotate_at_local_midnight_and_keep_thirty_archives():
    """确认 API 与应用日志处理器按本地午夜保留三十个归档。"""
    for logger_name in ("health_service.api", "health_service"):
        handlers = [
            handler
            for handler in logging.getLogger(logger_name).handlers
            if isinstance(handler, TimedRotatingFileHandler)
        ]

        assert len(handlers) == 1
        assert handlers[0].when == "MIDNIGHT"
        assert handlers[0].interval == 24 * 60 * 60
        assert handlers[0].backupCount == 30
        assert handlers[0].utc is False


def test_timed_rotation_splits_records_and_removes_oldest_archive(tmp_path):
    """用手动轮转时间验证文件切分和最旧归档删除。"""
    log_path = tmp_path / "server.log"
    handler = TimedRotatingFileHandler(
        log_path,
        when="S",
        interval=1,
        backupCount=2,
        encoding="utf-8",
        utc=False,
    )
    rotation_origin = int(time.mktime((2024, 1, 1, 0, 0, 0, 0, 0, -1)))

    try:
        for index in range(4):
            handler.emit(
                logging.LogRecord(
                    "test.rotation",
                    logging.INFO,
                    __file__,
                    1,
                    f"record-{index}",
                    (),
                    None,
                )
            )
            if index < 3:
                handler.rolloverAt = rotation_origin + index + 1
                handler.doRollover()
                handler.rolloverAt = int(time.time()) + 24 * 60 * 60
    finally:
        handler.close()

    archives = list(tmp_path.glob("server.log.*"))
    contents = [log_path.read_text(encoding="utf-8")]
    contents.extend(path.read_text(encoding="utf-8") for path in archives)
    all_records = "\n".join(contents)

    assert len(archives) == 2
    assert "record-0" not in all_records
    for index in range(1, 4):
        assert f"record-{index}" in all_records
