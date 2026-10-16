"""通过真实 Redis Stream 验证独立推理消费者的确认与恢复语义。"""

import signal
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from time import sleep
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError
from django.test import override_settings
from django.utils import timezone
from redis import Redis

from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
)
from health_service.infrastructure.inference_adapter import (
    run_task_inference as run_actual_task_inference,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.management.commands import run_inference as inference_command
from health_service.models import Channel, Task, TaskInputFile, TaskMetric
from tests.log_records import parse_console_records

_PPG_CSV = (
    "timestampFrame,avgR,avgG,avgB\n"
    "0,1,2,3\n"
    "500,2,3,4\n"
    "1000,3,4,5\n"
    "1500,4,5,6\n"
)
_EXAMPLE_METRICS = (
    ("example.ppg.sample_count", "samples"),
    ("example.ppg.duration_seconds", "seconds"),
    ("example.ppg.sampling_rate_hz", "hertz"),
)


@pytest.fixture
def inference_stream():
    """为单个测试创建唯一 Stream 和消费组，并只清理该测试键。"""
    stream = f"test:health:consumer:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        with override_settings(INFERENCE_STREAM=stream, INFERENCE_GROUP=group):
            yield client, stream, group
    finally:
        client.delete(stream)
        client.close()


def _create_example_task(
    *, last_dispatched_at: datetime | None = None
) -> Task:
    """创建带示例 PPG 文件和完整投影配置的待执行任务。"""
    user = get_user_model().objects.create_user(
        username=f"consumer-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"consumer-{uuid4().hex}",
        name="Consumer example",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="hr_br.example",
        params=None,
    )
    for code, unit in _EXAMPLE_METRICS:
        create_metric_definition(
            code=code,
            description=code,
            value_type="number",
            unit=unit,
        )
    set_model_version_metrics(
        version_id=draft.version_id,
        metric_codes=tuple(code for code, _ in _EXAMPLE_METRICS),
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    now = timezone.now()
    task = Task.objects.create(
        user=user,
        channel=Channel.objects.get(label="ios-cn"),
        model_version_id=version.version_id,
        status=Task.StatusChoices.SUBMITTED,
        profile_snapshot={},
        submitted_at=now,
        last_dispatched_at=last_dispatched_at,
    )
    _store_ppg_input(task, uploaded_at=now)
    return task


def _store_ppg_input(task: Task, *, uploaded_at: datetime) -> None:
    """为给定任务写入独立选中的示例 PPG 输入文件。"""
    storage = LocalFileStorage(
        Path(settings.TASK_DATA_ROOT),
        Path(settings.MODEL_ARTIFACT_ROOT),
    )
    stored = storage.save_upload(
        BytesIO(_PPG_CSV.encode("utf-8")),
        area=StorageArea.TASK_DATA,
        max_bytes=4096,
    )
    TaskInputFile.objects.create(
        task=task,
        logical_name="ppg",
        storage_key=stored.storage_key,
        size_bytes=stored.size_bytes,
        uploaded_at=uploaded_at,
        is_selected=True,
    )


def _create_example_task_for_version(version_id: int) -> Task:
    """为已发布示例版本创建第二个独立待执行任务。"""
    user = get_user_model().objects.create_user(
        username=f"consumer-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    now = timezone.now()
    task = Task.objects.create(
        user=user,
        channel=Channel.objects.get(label="ios-cn"),
        model_version_id=version_id,
        status=Task.StatusChoices.SUBMITTED,
        profile_snapshot={},
        submitted_at=now,
    )
    _store_ppg_input(task, uploaded_at=now)
    return task


def _run_once(consumer: str = "test-consumer") -> None:
    """通过 Django 命令入口实际运行一轮消费。"""
    call_command("run_inference", consumer=consumer, once=True, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_once_dispatches_and_executes_example_task(tmp_path, inference_stream, capsys):
    """单轮命令补发并完成示例任务，原子保存完整结果和所选指标。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_example_task()
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == {
        "example": True,
        "sample_count": 4,
        "duration_seconds": 1.5,
        "sampling_rate_hz": 2.0,
    }
    assert all(key not in task.result_payload for key in ("hr", "br"))
    assert TaskMetric.objects.filter(task=task).count() == 3
    assert client.xpending(stream, group)["pending"] == 0
    records = parse_console_records(capsys.readouterr().out)
    assert [record["event"] for record in records] == [
        "inference.worker.starting", "inference.worker.ready",
        "inference.message.received", "inference.task.started",
        "inference.task.succeeded", "inference.worker.stopped",
    ]
    task_records = [record for record in records if "taskId" in record]
    assert all(record["taskId"] == str(task.pk) for record in task_records)
    assert len({record["messageId"] for record in task_records}) == 1
    assert float(records[-2]["durationMs"]) >= 0


@pytest.mark.django_db(transaction=True)
def test_once_exits_immediately_when_stream_has_no_messages(inference_stream, capsys):
    """空闲 Stream 的单轮命令立即返回且不制造任务消息。"""
    client, stream, _ = inference_stream

    _run_once()

    assert client.xrange(stream) == []
    assert [record["event"] for record in parse_console_records(capsys.readouterr().out)] == [
        "inference.worker.starting", "inference.worker.ready", "inference.worker.stopped",
    ]


@pytest.mark.django_db(transaction=True)
def test_once_reuses_existing_consumer_group(inference_stream):
    """消费组已存在时单轮命令继续消费且不重置组状态。"""
    client, stream, group = inference_stream
    client.xgroup_create(stream, group, id="0-0", mkstream=True)

    _run_once()

    assert [item["name"] for item in client.xinfo_groups(stream)] == [group]


@pytest.mark.django_db(transaction=True)
def test_once_acknowledges_terminal_malformed_and_missing_task_messages(
    tmp_path, inference_stream, capsys
):
    """终态、畸形和不存在任务的消息安全确认且不改写结果。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_example_task()
        saved_result = {"stable": "existing-result"}
        Task.objects.filter(pk=task.pk).update(
            status=Task.StatusChoices.SUCCEEDED,
            result_payload=saved_result,
            finished_at=timezone.now(),
        )
        client.xadd(stream, {"task_id": str(task.pk)})
        client.xadd(stream, {"task_id": "not-an-id"})
        client.xadd(stream, {"task_id": "9223372036854775808"})
        client.xadd(stream, {"task_id": "9223372036854775000"})
        client.xadd(stream, {"other": "field"})

        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == saved_result
    assert client.xpending(stream, group)["pending"] == 0
    records = parse_console_records(capsys.readouterr().out)
    assert all(record["event"] != "inference.task.started" for record in records)
    assert "not-an-id" not in str(records)


@pytest.mark.django_db(transaction=True)
def test_once_leaves_message_pending_while_database_lease_is_active(
    tmp_path, inference_stream, capsys
):
    """已有有效执行租约时不重领任务，也不确认其消息。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_example_task(last_dispatched_at=timezone.now())
        execution_token = uuid4()
        Task.objects.filter(pk=task.pk).update(
            status=Task.StatusChoices.RUNNING,
            started_at=timezone.now(),
            execution_token=execution_token,
            lease_expires_at=(
                timezone.now() + timedelta(seconds=settings.INFERENCE_LEASE_SECONDS)
            ),
            attempt_count=1,
        )
        client.xadd(stream, {"task_id": str(task.pk)})

        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.execution_token == execution_token
    assert client.xpending(stream, group)["pending"] == 1
    records = parse_console_records(capsys.readouterr().out)
    assert any(record["event"] == "inference.message.received" for record in records)
    assert all(record["event"] != "inference.task.started" for record in records)


@pytest.mark.django_db(transaction=True)
def test_once_reclaims_idle_pending_message_with_xautoclaim(tmp_path, inference_stream):
    """单轮命令使用 XAUTOCLAIM 恢复旧消费者遗留的待确认任务。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_example_task(last_dispatched_at=timezone.now())
        client.xgroup_create(stream, group, id="0-0", mkstream=True)
        message_id = client.xadd(stream, {"task_id": str(task.pk)})
        client.xreadgroup(
            groupname=group,
            consumername="previous-consumer",
            streams={stream: ">"},
            count=1,
        )
        client.xclaim(
            stream,
            group,
            "previous-consumer",
            min_idle_time=0,
            message_ids=[message_id],
            idle=settings.INFERENCE_LEASE_SECONDS * 1000 + 1,
        )

        _run_once("recovery-consumer")

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["sample_count"] == 4
    assert client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_heartbeat_keeps_long_inference_lease_valid(tmp_path, inference_stream, monkeypatch):
    """计算超过初始租约时独立心跳持续续约并允许成功提交。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        INFERENCE_LEASE_SECONDS=0.15,
        INFERENCE_HEARTBEAT_SECONDS=0.03,
    ):
        task = _create_example_task()

        def slow_inference(*, task_id, execution_token):
            """延迟后执行真实示例算法以跨越初始租约。"""
            sleep(0.45)
            return run_actual_task_inference(
                task_id=task_id,
                execution_token=execution_token,
            )

        monkeypatch.setattr(inference_command, "run_task_inference", slow_inference)
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.attempt_count == 1
    assert task.finished_at is not None
    assert client.xpending(stream, group)["pending"] == 0


@pytest.mark.parametrize("heartbeat_failure", ["lease-lost", "database-error"])
@pytest.mark.django_db(transaction=True)
def test_heartbeat_failure_never_writes_terminal_state_or_ack(
    tmp_path, inference_stream, monkeypatch, heartbeat_failure, capsys
):
    """心跳失权或数据库异常均保留非终态任务和未确认消息。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        INFERENCE_LEASE_SECONDS=0.3,
        INFERENCE_HEARTBEAT_SECONDS=0.03,
    ):
        task = _create_example_task()

        def slow_inference(*, task_id, execution_token):
            """等待心跳线程完成失败判定后返回真实算法结果。"""
            sleep(0.12)
            return run_actual_task_inference(
                task_id=task_id,
                execution_token=execution_token,
            )

        def fail_heartbeat(*, task_id, execution_token):
            """模拟租约被抢占或数据库暂时故障。"""
            if heartbeat_failure == "database-error":
                raise OperationalError("不可持久化的数据库诊断")
            return False

        monkeypatch.setattr(inference_command, "run_task_inference", slow_inference)
        monkeypatch.setattr(
            inference_command,
            "renew_task_execution_lease",
            fail_heartbeat,
        )
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.failure_code is None
    assert task.execution_token is not None
    assert client.xpending(stream, group)["pending"] == 1
    output = capsys.readouterr().out
    records = parse_console_records(output)
    lost = [record for record in records if record["event"] == "inference.task.lease_lost"]
    assert len(lost) == 1
    assert lost[0]["taskId"] == str(task.pk)
    assert lost[0]["reason"] == ("heartbeat_error" if heartbeat_failure == "database-error" else "lease_expired")
    assert all(record["event"] not in {"inference.task.succeeded", "inference.task.failed"} for record in records)
    assert "不可持久化的数据库诊断" not in output


@pytest.mark.django_db(transaction=True)
def test_shutdown_signal_finishes_current_task_and_stops_batch(
    tmp_path, inference_stream, monkeypatch
):
    """收到终止信号后完成当前任务，不领取同批后续任务。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        first_task = _create_example_task()
        second_task = _create_example_task_for_version(first_task.model_version_id)

        def stop_after_start(*, task_id, execution_token):
            """触发 SIGTERM 后继续完成已经开始的真实示例算法。"""
            signal.raise_signal(signal.SIGTERM)
            return run_actual_task_inference(
                task_id=task_id,
                execution_token=execution_token,
            )

        monkeypatch.setattr(inference_command, "run_task_inference", stop_after_start)
        _run_once()

    first_task.refresh_from_db()
    second_task.refresh_from_db()
    assert first_task.status == Task.StatusChoices.SUCCEEDED
    assert second_task.status == Task.StatusChoices.SUBMITTED
    assert client.xpending(stream, group)["pending"] == 1


@pytest.mark.django_db(transaction=True)
def test_invalid_input_logs_only_committed_safe_failure(tmp_path, inference_stream, capsys):
    """真实输入解析失败只输出已保存错误码，不泄露原始输入。"""
    client, stream, group = inference_stream
    with override_settings(TASK_DATA_ROOT=tmp_path / "tasks", MODEL_ARTIFACT_ROOT=tmp_path / "models"):
        task = _create_example_task()
        input_file = TaskInputFile.objects.get(task=task)
        storage = LocalFileStorage(Path(settings.TASK_DATA_ROOT), Path(settings.MODEL_ARTIFACT_ROOT))
        storage.resolve_file(StorageArea.TASK_DATA, input_file.storage_key).write_text("HEALTH-INPUT-SENTINEL")
        _run_once()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.INVALID_INPUT"
    assert client.xpending(stream, group)["pending"] == 0
    output = capsys.readouterr().out
    records = parse_console_records(output)
    failed = [record for record in records if record["event"] == "inference.task.failed"]
    assert len(failed) == 1 and failed[0]["errorCode"] == task.failure_code
    assert failed[0]["taskId"] == str(task.pk)
    assert "HEALTH-INPUT-SENTINEL" not in output


@pytest.mark.parametrize("boundary", ["save-error", "save-rejected", "ack-error"])
@pytest.mark.django_db(transaction=True)
def test_result_logs_do_not_cross_persistence_and_ack_boundaries(
    tmp_path, inference_stream, monkeypatch, capsys, boundary,
):
    """保存失败、失权和确认失败分别保持真实数据库状态与日志含义。"""
    from django.core.management.base import CommandError
    from redis.exceptions import ConnectionError as RedisConnectionError

    client, stream, group = inference_stream
    actual_save = inference_command.save_task_execution_success

    def interrupted_save(*, task_id, execution_token, result):
        """在真实终态事务前模拟数据库故障或使数据库租约过期。"""
        if boundary == "save-error":
            raise OperationalError("PRIVATE-DATABASE-SENTINEL")
        Task.objects.filter(pk=task_id).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        return actual_save(task_id=task_id, execution_token=execution_token, result=result)

    def interrupted_ack(*args, **kwargs):
        """模拟终态提交后的 Redis 确认故障。"""
        raise RedisConnectionError("PRIVATE-REDIS-SENTINEL")

    with override_settings(TASK_DATA_ROOT=tmp_path / "tasks", MODEL_ARTIFACT_ROOT=tmp_path / "models"):
        task = _create_example_task()
        if boundary == "ack-error":
            monkeypatch.setattr(Redis, "xack", interrupted_ack)
        else:
            monkeypatch.setattr(inference_command, "save_task_execution_success", interrupted_save)
        if boundary == "save-rejected":
            _run_once()
        else:
            with pytest.raises(CommandError):
                _run_once()
    task.refresh_from_db()
    records = parse_console_records(capsys.readouterr().out)
    assert client.xpending(stream, group)["pending"] >= 1
    events = [record["event"] for record in records]
    assert "inference.task.failed" not in events
    if boundary == "ack-error":
        assert task.status == Task.StatusChoices.SUCCEEDED
        assert events.index("inference.task.succeeded") < events.index("inference.worker.error")
    else:
        assert task.status == Task.StatusChoices.RUNNING
        assert task.result_payload is None and task.finished_at is None
        assert "inference.task.succeeded" not in events
    if boundary == "save-rejected":
        assert "inference.task.lease_lost" in events
    else:
        error = next(record for record in records if record["event"] == "inference.worker.error")
        assert error["taskId"] == str(task.pk)
        assert "inference.worker.stopped" not in events
    assert "PRIVATE-" not in str(records)
