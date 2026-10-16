"""编排 Redis 消息发布与数据库派发时间补记。"""

from django.conf import settings
from django.db import DatabaseError

from health_service.inference_logging import log_inference_event
from health_service.infrastructure.persistence import tasks as persistence
from health_service.infrastructure.streaming import publish_task_message


def dispatch_task(task_id: int) -> bool:
    """发布任务主键并条件记录成功时间，允许安全重复派发。"""
    if not publish_task_message(task_id):
        log_inference_event(
            "inference.dispatch.failed", task_id=task_id, reason="dispatch_publish_failed",
        )
        return False
    try:
        persistence.mark_task_dispatched(task_id=task_id)
    except DatabaseError:
        log_inference_event(
            "inference.dispatch.failed", task_id=task_id, reason="dispatch_marker_failed",
            exception_type="DatabaseError",
        )
        return True
    return True


_DISPATCH_BATCH_SIZE = 100


def dispatch_pending_tasks() -> int:
    """有界扫描并补发未发布或租约过期的任务。"""
    task_ids = persistence.list_dispatchable_task_ids(
        limit=_DISPATCH_BATCH_SIZE,
        dispatch_interval_seconds=settings.INFERENCE_DISPATCH_INTERVAL_SECONDS,
    )
    dispatched = 0
    for task_id in task_ids:
        if not dispatch_task(task_id):
            break
        dispatched += 1
    return dispatched
