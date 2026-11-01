"""运行独立的 Redis Streams 推理任务消费者。"""

import signal
from collections.abc import Mapping
from os import getpid
from socket import gethostname
from threading import Event, Thread, current_thread, main_thread
from time import monotonic
from types import FrameType
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from health_inference import InferenceError
from redis import Redis
from redis.exceptions import RedisError, ResponseError

from health_service.application.dispatch import dispatch_pending_tasks
from health_service.application.execution import (
    claim_task_for_execution,
    publish_successful_task_outputs,
    record_task_execution_failure,
    renew_task_execution_lease,
    save_task_execution_success,
)
from health_service.inference_logging import log_inference_event
from health_service.infrastructure.inference_adapter import (
    clear_version_runtime_cache,
    run_task_inference,
)
from health_service.models import Task

_BATCH_SIZE = 100
_MAX_TASK_ID = (1 << 63) - 1
_TERMINAL_STATUSES = frozenset({Task.StatusChoices.SUCCEEDED, Task.StatusChoices.FAILED})
_EXECUTABLE_STATUSES = frozenset({Task.StatusChoices.SUBMITTED, Task.StatusChoices.RUNNING})


class Command(BaseCommand):
    """消费 Stream 消息并按数据库租约安全执行任务。"""

    help = "运行独立推理任务消费者"

    def add_arguments(self, parser):
        """注册消费者名称及单轮运行选项。"""
        parser.add_argument("--consumer", default=None, help="Redis 消费者名称")
        parser.add_argument("--once", action="store_true", help="扫描并消费一批后退出")

    def handle(self, *args, consumer: str | None, once: bool, **options) -> None:
        """创建消费组并持续执行补发扫描、消息领取和终态确认。"""
        consumer_name = consumer or _default_consumer_name()
        if not consumer_name.strip():
            raise CommandError("消费者名称不能为空")

        self._consumer = consumer_name
        self._mode = "once" if once else "continuous"
        self._worker_error_recorded = False
        self._log_event("inference.worker.starting")
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self._claim_cursor = "0-0"
        self._shutdown_event = Event()
        previous_handlers = self._install_shutdown_signal_handlers()
        try:
            _ensure_consumer_group(client)
            ready = False
            while not self._shutdown_event.is_set():
                dispatch_pending_tasks()
                if not ready:
                    self._log_event("inference.worker.ready")
                    ready = True
                self._consume_batch(client, consumer_name, once=once)
                if once or self._shutdown_event.is_set():
                    break
        except DatabaseError as error:
            self._record_worker_error(error)
            raise CommandError("推理消费者的数据库操作失败") from error
        except RedisError as error:
            self._record_worker_error(error)
            raise CommandError("推理消费者的 Redis 操作失败") from error
        finally:
            clear_version_runtime_cache()
            self._restore_shutdown_signal_handlers(previous_handlers)
            client.close()
        self._log_event("inference.worker.stopped")

    def _log_event(self, event: str, **fields) -> None:
        """为当前命令事件补充独立消费者关联信息。"""
        log_inference_event(event, consumer=self._consumer, mode=self._mode, **fields)

    def _record_worker_error(self, error, **fields) -> None:
        """只记录一次固定基础设施诊断，不暴露原始异常。"""
        if self._worker_error_recorded:
            return
        self._worker_error_recorded = True
        is_database = isinstance(error, DatabaseError)
        self._log_event(
            "inference.worker.error",
            reason="database_error" if is_database else "redis_error",
            exception_type="DatabaseError" if is_database else "RedisError",
            **fields,
        )

    def _install_shutdown_signal_handlers(self):
        """在主线程将终止信号转换为停止领取标记并保留旧处理器。"""
        if current_thread() is not main_thread():
            return {}
        previous_handlers = {}
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, self._request_shutdown)
        return previous_handlers

    def _restore_shutdown_signal_handlers(self, previous_handlers) -> None:
        """恢复命令启动前的终止信号处理器。"""
        if current_thread() is main_thread():
            for signal_number, handler in previous_handlers.items():
                signal.signal(signal_number, handler)

    def _request_shutdown(self, _signal_number: int, _frame: FrameType | None) -> None:
        """收到关闭信号后停止领取新消息，不中断当前安全执行。"""
        self._shutdown_event.set()

    def _consume_batch(self, client: Redis, consumer: str, *, once: bool) -> None:
        """先重领过期未确认消息，再有界读取新消息。"""
        if self._shutdown_event.is_set():
            return
        claimed = client.xautoclaim(
            settings.INFERENCE_STREAM,
            settings.INFERENCE_GROUP,
            consumer,
            min_idle_time=int(settings.INFERENCE_LEASE_SECONDS * 1000),
            start_id=self._claim_cursor,
            count=_BATCH_SIZE,
        )
        self._claim_cursor = claimed[0]
        claimed_messages = claimed[1]
        for message_id, fields in claimed_messages:
            if self._shutdown_event.is_set():
                return
            self._consume_message(client, message_id, fields)
        if self._shutdown_event.is_set():
            return

        remaining = _BATCH_SIZE - len(claimed_messages)
        if remaining <= 0:
            return
        responses = client.xreadgroup(
            groupname=settings.INFERENCE_GROUP,
            consumername=consumer,
            streams={settings.INFERENCE_STREAM: ">"},
            count=remaining,
            block=None if once else settings.INFERENCE_POLL_MILLISECONDS,
        )
        for _, messages in responses:
            for message_id, fields in messages:
                if self._shutdown_event.is_set():
                    return
                self._consume_message(client, message_id, fields)

    def _consume_message(
        self,
        client: Redis,
        message_id: str,
        fields: Mapping[str, str],
    ) -> None:
        """验证消息和任务状态，只有终态已持久化后才确认消息。"""
        task_id = _parse_task_id(fields)
        self._log_event(
            "inference.message.received",
            task_id=task_id,
            message_id=message_id,
        )
        try:
            self._process_message(client, message_id, task_id)
        except (DatabaseError, RedisError) as error:
            self._record_worker_error(error, task_id=task_id, message_id=message_id)
            raise

    def _process_message(
        self,
        client: Redis,
        message_id: str,
        task_id: int | None,
    ) -> None:
        """按执行所有权处理一条消息，终态提交后才报告并确认。"""
        if task_id is None:
            self._ack(client, message_id)
            return

        status = _task_status(task_id)
        if status is None or status in _TERMINAL_STATUSES:
            if status == Task.StatusChoices.SUCCEEDED:
                publish_successful_task_outputs(task_id=task_id)
            self._ack(client, message_id)
            return
        if status not in _EXECUTABLE_STATUSES:
            self._ack(client, message_id)
            return

        execution_token = claim_task_for_execution(task_id=task_id)
        if execution_token is None:
            status = _task_status(task_id)
            if status is None or status not in _EXECUTABLE_STATUSES:
                self._ack(client, message_id)
            return
        started_at = monotonic()
        self._log_event("inference.task.started", task_id=task_id, message_id=message_id)
        heartbeat_stop = Event()
        lease_lost = Event()
        heartbeat_thread = Thread(
            target=_renew_execution_lease,
            args=(task_id, execution_token, heartbeat_stop, lease_lost),
            name=f"inference-lease-{task_id}",
            daemon=True,
            kwargs={
                "consumer": self._consumer,
                "mode": self._mode,
                "message_id": message_id,
            },
        )
        heartbeat_thread.start()

        result = None
        execution_error = None
        failed = False
        failure_code = None
        try:
            result = run_task_inference(
                task_id=task_id,
                execution_token=execution_token,
            )
        except (DatabaseError, RedisError):
            raise
        except InferenceError as error:
            execution_error = error
            failed = True
        except Exception:  # noqa: BLE001  # 未知算法异常统一映射为固定安全失败。
            failed = True
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

        if lease_lost.is_set():
            return
        if failed:
            failure_code = record_task_execution_failure(
                task_id=task_id,
                execution_token=execution_token,
                error=execution_error,
            )
            terminal = failure_code is not None
        elif result is None:
            self._log_event(
                "inference.task.lease_lost",
                task_id=task_id,
                message_id=message_id,
                reason="ownership_rejected",
            )
            return
        else:
            try:
                terminal = save_task_execution_success(
                    task_id=task_id,
                    execution_token=execution_token,
                    result=result,
                )
            except (DatabaseError, RedisError):
                raise
            except InferenceError as error:
                failure_code = record_task_execution_failure(
                    task_id=task_id,
                    execution_token=execution_token,
                    error=error,
                )
                terminal = failure_code is not None
            except Exception:  # noqa: BLE001  # 未知结果异常统一映射为固定安全失败。
                failure_code = record_task_execution_failure(
                    task_id=task_id,
                    execution_token=execution_token,
                    error=None,
                )
                terminal = failure_code is not None

        if terminal:
            self._log_event(
                "inference.task.failed" if failure_code else "inference.task.succeeded",
                task_id=task_id,
                message_id=message_id,
                error_code=failure_code,
                duration_ms=(monotonic() - started_at) * 1000,
            )
            self._ack(client, message_id)
        else:
            self._log_event(
                "inference.task.lease_lost",
                task_id=task_id,
                message_id=message_id,
                reason="ownership_rejected",
            )

    @staticmethod
    def _ack(client: Redis, message_id: str) -> None:
        """确认已终结或不可执行的单条 Stream 消息。"""
        client.xack(settings.INFERENCE_STREAM, settings.INFERENCE_GROUP, message_id)


def _ensure_consumer_group(client: Redis) -> None:
    """从 Stream 起点创建消费组；已存在时保持其待确认状态。"""
    try:
        client.xgroup_create(
            settings.INFERENCE_STREAM,
            settings.INFERENCE_GROUP,
            id="0-0",
            mkstream=True,
        )
    except ResponseError as error:
        if not str(error).startswith("BUSYGROUP"):
            raise


def _parse_task_id(fields: object) -> int | None:
    """只接受派发器生成的规范正整数任务标识。"""
    if not isinstance(fields, Mapping):
        return None
    raw_task_id = fields.get("task_id")
    if not isinstance(raw_task_id, str) or not raw_task_id.isascii() or not raw_task_id.isdigit():
        return None
    try:
        task_id = int(raw_task_id)
    except ValueError:
        return None
    if task_id <= 0 or task_id > _MAX_TASK_ID or str(task_id) != raw_task_id:
        return None
    return task_id


def _task_status(task_id: int) -> str | None:
    """只读取消息授权所需的任务主键与状态。"""
    return Task.objects.filter(pk=task_id).values_list("status", flat=True).first()


def _default_consumer_name() -> str:
    """生成同一主机进程内唯一的 Redis 消费者名称。"""
    return f"{gethostname()}-{getpid()}-{uuid4().hex}"


def _renew_execution_lease(
    task_id: int,
    execution_token: UUID,
    stop_event: Event,
    lease_lost: Event,
    *,
    consumer: str,
    mode: str,
    message_id: str,
) -> None:
    """独立续约执行租约；续约或线程连接清理异常时标记所有权失效。"""
    try:
        while not stop_event.wait(settings.INFERENCE_HEARTBEAT_SECONDS):
            if not renew_task_execution_lease(
                task_id=task_id,
                execution_token=execution_token,
            ):
                lease_lost.set()
                log_inference_event(
                    "inference.task.lease_lost",
                    task_id=task_id,
                    consumer=consumer,
                    mode=mode,
                    message_id=message_id,
                    reason="lease_expired",
                )
                return
    except Exception as error:  # noqa: BLE001  # 任意心跳故障都必须放弃终态写入。
        lease_lost.set()
        log_inference_event(
            "inference.task.lease_lost",
            task_id=task_id,
            consumer=consumer,
            mode=mode,
            message_id=message_id,
            reason="heartbeat_error",
            exception_type="DatabaseError" if isinstance(error, DatabaseError) else None,
        )
    finally:
        try:
            connections.close_all()
        except Exception:  # noqa: BLE001  # 线程连接无法清理时保守放弃提交。
            lease_lost.set()
            log_inference_event(
                "inference.task.lease_lost",
                task_id=task_id,
                consumer=consumer,
                mode=mode,
                message_id=message_id,
                reason="connection_cleanup_failed",
            )
