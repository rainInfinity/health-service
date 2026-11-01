"""通过真实 Redis Stream 验证独立推理消费者的确认与恢复语义。"""

import asyncio
import csv
import json
import math
import os
import signal
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from time import sleep
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import AsyncClient, override_settings
from django.utils import timezone
from health_inference import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
)
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.registry import _ALGORITHMS
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
)
from health_service.application.execution import record_task_execution_failure
from health_service.application.tasks import (
    create_task_draft,
    submit_user_task,
    upload_task_input,
)
from health_service.infrastructure.inference_adapter import (
    _version_runtime,
    clear_version_runtime_cache,
)
from health_service.infrastructure.inference_adapter import (
    run_task_inference as run_actual_task_inference,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.management.commands import run_inference as inference_command
from health_service.models import Channel, Task, TaskInputFile, TaskMetric
from redis import Redis

from tests.integration.algorithm_fixtures import (
    TEST_METRIC_ALGORITHM_KEY,
    TEST_METRIC_CODE,
)
from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.log_records import parse_console_records

_PPG_CSV = "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n500,2,3,4\n1000,3,4,5\n1500,4,5,6\n"
_TEST_METRICS = ((TEST_METRIC_CODE, "count"),)


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


def _create_test_metric_task(*, last_dispatched_at: datetime | None = None) -> Task:
    """创建带 PPG 输入和测试指标投影的待执行任务。"""
    user = get_user_model().objects.create_user(
        username=f"consumer-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"consumer-{uuid4().hex}",
        name="Consumer test task",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_METRIC_ALGORITHM_KEY,
        params=None,
    )
    for code, unit in _TEST_METRICS:
        create_metric_definition(
            code=code,
            description=code,
            value_type="number",
            unit=unit,
        )
    set_model_version_metrics(
        version_id=draft.version_id,
        metric_codes=tuple(code for code, _ in _TEST_METRICS),
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


def _store_ppg_input(
    task: Task,
    *,
    uploaded_at: datetime,
    content: str = _PPG_CSV,
) -> None:
    """为给定任务写入独立选中的 PPG CSV 输入文件。"""
    storage = LocalFileStorage(
        Path(settings.TASK_DATA_ROOT),
        Path(settings.MODEL_ARTIFACT_ROOT),
    )
    stored = storage.save_task_input(
        BytesIO(content.encode("utf-8")),
        source_filename=f"{task.pk}.csv",
        allowed_extensions=frozenset({"csv"}),
        created_at=task.created_at,
        task_id=task.pk,
        user_id=task.user_id,
        channel=task.channel.label,
        algorithm_key=task.model_version.algorithm_key,
        logical_name="ppg",
        max_bytes=1024 * 1024,
    )
    TaskInputFile.objects.create(
        task=task,
        logical_name="ppg",
        storage_key=stored.storage_key,
        size_bytes=stored.size_bytes,
        uploaded_at=uploaded_at,
        is_selected=True,
    )


def _create_metric_task_for_version(version_id: int) -> Task:
    """为已发布测试版本创建第二个独立待执行任务。"""
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


@pytest.fixture
def isolated_quality_algorithm(monkeypatch):
    """临时装入拒绝算法并在测试后清理版本运行时缓存。"""
    algorithm_key = f"test.quality.{uuid4().hex}"
    scheduled_rejections: list[QualityRejection] = []
    invocations: list[QualityRejection] = []

    def reject_quality(_: InferenceInput) -> InferenceOutput:
        """触发本测试指定的质量拒绝并记录执行次数。"""
        if not scheduled_rejections:
            raise AssertionError("测试未指定质量拒绝")
        rejection = scheduled_rejections[0]
        invocations.append(rejection)
        error = QualityRejectedError(rejection)
        error.args = ("PRIVATE-QUALITY-SENTINEL",)
        error.safe_message = "PRIVATE-QUALITY-SENTINEL"
        raise error

    definition = AlgorithmDefinition(
        key=algorithm_key,
        is_example=False,
        required_inputs=(),
        required_artifacts=(),
        input_extensions={},
        artifact_extensions={},
        required_profile_fields=(),
        parameter_validator=lambda _: None,
        result_validator=lambda _: None,
        resource_factory=lambda _: reject_quality,
        metric_contracts=(),
    )
    clear_version_runtime_cache()
    monkeypatch.setitem(_ALGORITHMS, algorithm_key, definition)
    try:
        yield algorithm_key, scheduled_rejections, invocations
    finally:
        clear_version_runtime_cache()


def _create_quality_rejection_task(*, algorithm_key: str) -> Task:
    """为临时质量算法创建无输入、无指标的待执行任务。"""
    user = get_user_model().objects.create_user(
        username=f"consumer-quality-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"consumer-quality-{uuid4().hex}",
        name="Quality rejection test",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=algorithm_key,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    now = timezone.now()
    return Task.objects.create(
        user=user,
        channel=Channel.objects.get(label="ios-cn"),
        model_version_id=version.version_id,
        status=Task.StatusChoices.SUBMITTED,
        profile_snapshot={},
        submitted_at=now,
    )


def _run_once(consumer: str = "test-consumer") -> None:
    """通过 Django 命令入口实际运行一轮消费。"""
    call_command("run_inference", consumer=consumer, once=True, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_once_dispatches_and_executes_test_algorithm_task(
    tmp_path,
    inference_stream,
    capsys,
):
    """单轮命令补发并完成测试算法任务，保存结果和投影指标。"""
    clear_version_runtime_cache()
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_test_metric_task()
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == {"completed": True}
    assert _version_runtime.cache_info().currsize == 0
    assert client.xpending(stream, group)["pending"] == 0
    records = parse_console_records(capsys.readouterr().out)
    assert [record["event"] for record in records] == [
        "inference.worker.starting",
        "inference.worker.ready",
        "inference.message.received",
        "inference.task.started",
        "inference.task.succeeded",
        "inference.worker.stopped",
    ]
    task_records = [record for record in records if "taskId" in record]
    assert all(record["taskId"] == str(task.pk) for record in task_records)
    assert len({record["messageId"] for record in task_records}) == 1
    assert float(records[-2]["durationMs"]) >= 0


@pytest.mark.django_db(transaction=True)
def test_registered_client_metadata_tasks_execute_end_to_end(
    tmp_path,
    inference_stream,
    capsys,
):
    """从普通渠道注册到真实消费者完成旧式及元数据任务。"""
    redis_client, stream, group = inference_stream
    username = f"consumer-e2e-{uuid4().hex}"
    password = "Dawn-valley-417!strong"
    metadata = {
        "deviceModel": "E2E-CLIENT-METADATA-SECRET",
        "brand": "Example",
        "custom": {"appBuild": 42},
    }
    expected_result = {"completed": True}

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        model = create_model(
            code=f"consumer-e2e-{uuid4().hex}",
            name="Consumer end-to-end",
            description=None,
        )
        draft = create_model_version(
            model_id=model.model_id,
            version="1.0",
            algorithm_key=TEST_METRIC_ALGORITHM_KEY,
            params=None,
        )
        for code, unit in _TEST_METRICS:
            create_metric_definition(
                code=code,
                description=code,
                value_type="number",
                unit=unit,
            )
        set_model_version_metrics(
            version_id=draft.version_id,
            metric_codes=tuple(code for code, _ in _TEST_METRICS),
        )
        version = publish_model_version(version_id=draft.version_id)
        select_channel_model_version(
            channel="ios-cn",
            model_id=model.model_id,
            version_id=version.version_id,
        )

        async def register_and_submit():
            """注册真实用户并通过三个请求形态提交推理任务。"""
            client = AsyncClient()
            registration = await client.post(
                "/api/v1/ios-cn/auth/register",
                data=json.dumps({"username": username, "password": password}),
                content_type="application/json",
            )
            login = await client.post(
                "/api/v1/ios-cn/auth/login",
                data=json.dumps({"username": username, "password": password}),
                content_type="application/json",
            )
            headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
            tasks = []
            request_bodies = (None, {"clientMetadata": metadata}, {})
            for request_body in request_bodies:
                created = await client.post(
                    "/api/v1/ios-cn/tasks",
                    data=json.dumps({"modelId": model.model_id}),
                    content_type="application/json",
                    headers=headers,
                )
                task_id = created.json()["data"]["taskId"]
                uploaded = await client.post(
                    f"/api/v1/ios-cn/tasks/{task_id}/files",
                    data={
                        "logicalName": "ppg",
                        "file": SimpleUploadedFile(
                            "ppg.csv",
                            _PPG_CSV.encode("utf-8"),
                            content_type="text/csv",
                        ),
                    },
                    headers=headers,
                )
                submit_url = f"/api/v1/ios-cn/tasks/{task_id}/submit"
                if request_body is None:
                    submitted = await client.post(
                        submit_url,
                        data=b"",
                        content_type="application/octet-stream",
                        headers=headers,
                    )
                else:
                    submitted = await client.post(
                        submit_url,
                        data=json.dumps(request_body),
                        content_type="application/json",
                        headers=headers,
                    )
                tasks.append((task_id, created, uploaded, submitted))
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return registration, login, tasks, headers

        registration, login, task_responses, headers = asyncio.run(register_and_submit())
        assert registration.status_code == 201
        assert login.status_code == 200
        user = get_user_model().objects.get(pk=registration.json()["data"]["userId"])
        assert user.registration_channel.label == "ios-cn"

        task_ids = [task_id for task_id, *_ in task_responses]
        for task_id, created, uploaded, submitted in task_responses:
            assert created.status_code == 201
            assert uploaded.status_code == 201
            assert submitted.status_code == 200
            assert "clientMetadata" not in submitted.json()["data"]
            assert metadata["deviceModel"] not in submitted.content.decode()
            assert Task.objects.get(pk=task_id).status == Task.StatusChoices.SUBMITTED

        _run_once()

        tasks = [Task.objects.get(pk=task_id) for task_id in task_ids]
        assert all(task.status == Task.StatusChoices.SUCCEEDED for task in tasks)
        assert all(task.result_payload == expected_result for task in tasks)
        assert tasks[0].client_metadata is None
        assert tasks[1].client_metadata == metadata
        assert tasks[2].client_metadata is None
        assert all(task.attempt_count == 1 for task in tasks)
        assert redis_client.xpending(stream, group)["pending"] == 0

        async def read_final_details():
            """使用注册用户的 Access 核对任务完成响应未暴露元数据。"""
            client = AsyncClient()
            responses = [
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task_id}",
                    headers=headers,
                )
                for task_id in task_ids
            ]
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return responses

        details = asyncio.run(read_final_details())
        for detail in details:
            assert detail.status_code == 200
            assert detail.json()["data"]["status"] == Task.StatusChoices.SUCCEEDED
            assert "clientMetadata" not in detail.json()["data"]
            assert metadata["deviceModel"] not in detail.content.decode()

    assert metadata["deviceModel"] not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rejection", "expected_detail"),
    [
        (
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(40, 70),
            ),
            {
                "quality": {
                    "reason": "LOW_SCORE",
                    "details": {"score": 40, "threshold": 70},
                }
            },
        ),
        (
            QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None),
            {
                "quality": {
                    "reason": "NO_VALID_SIGNAL",
                    "details": None,
                }
            },
        ),
    ],
    ids=("low-score", "no-valid-signal"),
)
@pytest.mark.django_db(transaction=True)
def test_once_commits_quality_rejection_before_ack_and_acks_duplicates(
    tmp_path,
    inference_stream,
    isolated_quality_algorithm,
    monkeypatch,
    capsys,
    rejection: QualityRejection,
    expected_detail: dict[str, object],
):
    """真实单轮消费者先提交拒绝，再确认重复任务消息且只执行一次。"""
    client, stream, group = inference_stream
    algorithm_key, scheduled_rejections, invocations = isolated_quality_algorithm
    scheduled_rejections.append(rejection)
    actual_xack = Redis.xack
    task: Task

    def acknowledge_after_terminal(redis_client, *args, **kwargs):
        """确认消息前核对质量拒绝已经提交为失败终态。"""
        task.refresh_from_db()
        assert task.status == Task.StatusChoices.FAILED
        return actual_xack(redis_client, *args, **kwargs)

    monkeypatch.setattr(Redis, "xack", acknowledge_after_terminal)
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_quality_rejection_task(algorithm_key=algorithm_key)
        client.xadd(stream, {"task_id": str(task.pk)})
        client.xadd(stream, {"task_id": str(task.pk)})
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
    assert task.failure_detail == expected_detail
    assert task.result_payload is None
    assert task.finished_at is not None
    assert task.execution_token is None
    assert task.lease_expires_at is None
    assert task.attempt_count == 1
    assert invocations == [rejection]
    assert not TaskMetric.objects.filter(task=task).exists()
    assert client.xpending(stream, group)["pending"] == 0

    output = capsys.readouterr().out
    records = parse_console_records(output)
    failed = [record for record in records if record["event"] == "inference.task.failed"]
    assert len(failed) == 1
    assert failed[0]["errorCode"] == task.failure_code
    assert failed[0]["taskId"] == str(task.pk)
    assert "PRIVATE-QUALITY-SENTINEL" not in output
    assert rejection.reason.value not in output
    assert all(not {"quality", "score", "threshold"}.intersection(record) for record in records)


@pytest.mark.parametrize(
    ("rejection", "expected_quality"),
    [
        (
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(40, 70),
            ),
            {
                "reason": "LOW_SCORE",
                "details": {"score": 40, "threshold": 70},
            },
        ),
        (
            QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None),
            {"reason": "NO_VALID_SIGNAL", "details": None},
        ),
    ],
    ids=("low-score", "no-valid-signal"),
)
@pytest.mark.django_db(transaction=True)
def test_quality_rejection_runs_through_http_and_terminal_replay(
    tmp_path,
    inference_stream,
    isolated_quality_algorithm,
    rejection: QualityRejection,
    expected_quality: dict[str, object],
):
    """串联单轮消费者、真实 HTTP 详情读取与已终态消息重放。"""
    redis_client, stream, group = inference_stream
    algorithm_key, scheduled_rejections, invocations = isolated_quality_algorithm
    scheduled_rejections.append(rejection)

    async def query_detail(username: str, task_id: int):
        """通过登录接口认证并读取同一质量拒绝任务。"""
        client = AsyncClient()
        login = await client.post(
            "/api/v1/ios-cn/auth/login",
            data=json.dumps(
                {
                    "username": username,
                    "password": "Dawn-valley-483!strong",
                }
            ),
            content_type="application/json",
        )
        try:
            detail = await client.get(
                f"/api/v1/ios-cn/tasks/{task_id}",
                headers={"Authorization": (f"Bearer {login.json()['data']['accessToken']}")},
            )
            return login, detail
        finally:
            await sync_to_async(connections.close_all, thread_sensitive=True)()

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_quality_rejection_task(algorithm_key=algorithm_key)
        username = task.user.username
        redis_client.xadd(stream, {"task_id": str(task.pk)})
        _run_once()

        first_login, first_response = asyncio.run(query_detail(username, task.pk))
        redis_client.xadd(stream, {"task_id": str(task.pk)})
        _run_once("quality-terminal-replay")
        second_login, second_response = asyncio.run(query_detail(username, task.pk))

    task.refresh_from_db()
    expected_private_detail = {"quality": expected_quality}
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
    assert task.failure_detail == expected_private_detail
    assert task.result_payload is None
    assert task.finished_at is not None
    assert task.attempt_count == 1
    assert invocations == [rejection]
    assert not TaskMetric.objects.filter(task=task).exists()
    assert redis_client.xpending(stream, group)["pending"] == 0

    assert first_login.status_code == second_login.status_code == 200
    assert first_response.status_code == second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    assert first_body["code"] == second_body["code"] == "OK"
    first_data = first_body["data"]
    assert second_body["data"] == first_data
    assert first_data["status"] == Task.StatusChoices.FAILED
    assert first_data["failureCode"] == "INFERENCE.QUALITY_REJECTED"
    assert first_data["result"] is None
    assert first_data["qualityFailure"] == expected_quality
    assert first_data["failureMessage"] == (
        "信号质量评分未达到要求，请重新采集"
        if rejection.reason is QualityRejectionReason.LOW_SCORE
        else "未检测到可用信号，请重新采集"
    )
    assert not {"failureDetail", "profileSnapshot"}.intersection(first_data)


@pytest.mark.django_db(transaction=True)
def test_quality_rejection_after_lease_loss_is_not_saved_or_acked(
    tmp_path,
    inference_stream,
    isolated_quality_algorithm,
    monkeypatch,
    capsys,
):
    """测试算法报告拒绝时若已失权，消费者不写终态也不确认消息。"""
    client, stream, group = inference_stream
    algorithm_key, scheduled_rejections, invocations = isolated_quality_algorithm
    rejection = QualityRejection(
        QualityRejectionReason.LOW_SCORE,
        LowScoreDetails(40, 70),
    )
    scheduled_rejections.append(rejection)

    def delayed_inference(*, task_id: int, execution_token):
        """等待心跳判定失权后仍经真实适配器执行测试算法。"""
        sleep(0.12)
        return run_actual_task_inference(
            task_id=task_id,
            execution_token=execution_token,
        )

    def lose_lease(*, task_id: int, execution_token) -> bool:
        """模拟数据库租约续约拒绝。"""
        return False

    monkeypatch.setattr(inference_command, "run_task_inference", delayed_inference)
    monkeypatch.setattr(inference_command, "renew_task_execution_lease", lose_lease)
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        INFERENCE_LEASE_SECONDS=0.3,
        INFERENCE_HEARTBEAT_SECONDS=0.03,
    ):
        task = _create_quality_rejection_task(algorithm_key=algorithm_key)
        _run_once()

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.failure_code is None
    assert task.failure_message is None
    assert task.failure_detail is None
    assert task.finished_at is None
    assert task.execution_token is not None
    assert task.attempt_count == 1
    assert invocations == [rejection]
    assert client.xpending(stream, group)["pending"] == 1

    output = capsys.readouterr().out
    records = parse_console_records(output)
    events = [record["event"] for record in records]
    assert "inference.task.lease_lost" in events
    assert "inference.task.failed" not in events
    assert "PRIVATE-QUALITY-SENTINEL" not in output
    assert rejection.reason.value not in output
    assert all(not {"quality", "score", "threshold"}.intersection(record) for record in records)


@pytest.mark.django_db(transaction=True)
def test_quality_rejection_ack_failure_replays_only_terminal_ack(
    tmp_path,
    inference_stream,
    isolated_quality_algorithm,
    monkeypatch,
    capsys,
):
    """质量拒绝提交后 ACK 故障重放只确认终态，不重新执行算法。"""
    from django.core.management.base import CommandError
    from redis.exceptions import ConnectionError as RedisConnectionError

    client, stream, group = inference_stream
    algorithm_key, scheduled_rejections, invocations = isolated_quality_algorithm
    rejection = QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None)
    expected_detail = {"quality": {"reason": "NO_VALID_SIGNAL", "details": None}}
    scheduled_rejections.append(rejection)
    task: Task

    def fail_ack(redis_client, *args, **kwargs):
        """确认前验证质量终态已落库，再模拟 Redis 确认故障。"""
        task.refresh_from_db()
        assert task.status == Task.StatusChoices.FAILED
        raise RedisConnectionError("PRIVATE-REDIS-SENTINEL")

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_quality_rejection_task(algorithm_key=algorithm_key)
        with monkeypatch.context() as patch:
            patch.setattr(Redis, "xack", fail_ack)
            with pytest.raises(CommandError):
                _run_once()

        task.refresh_from_db()
        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
        assert task.failure_detail == expected_detail
        assert task.attempt_count == 1
        assert client.xpending(stream, group)["pending"] == 1
        first_output = capsys.readouterr().out

        message_id = client.xrange(stream)[0][0]
        client.xclaim(
            stream,
            group,
            "previous-quality-consumer",
            min_idle_time=0,
            message_ids=[message_id],
            idle=settings.INFERENCE_LEASE_SECONDS * 1000 + 1,
        )
        _run_once("quality-replay")

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_detail == expected_detail
    assert task.attempt_count == 1
    assert invocations == [rejection]
    assert client.xpending(stream, group)["pending"] == 0
    first_records = parse_console_records(first_output)
    replay_output = capsys.readouterr().out
    replay_records = parse_console_records(replay_output)
    first_events = [record["event"] for record in first_records]
    replay_events = [record["event"] for record in replay_records]
    assert "inference.task.failed" in first_events
    assert "inference.worker.error" in first_events
    assert "inference.message.received" in replay_events
    assert "inference.task.started" not in replay_events
    assert "inference.task.failed" not in replay_events
    output = first_output + replay_output
    assert "PRIVATE-QUALITY-SENTINEL" not in output
    assert "PRIVATE-REDIS-SENTINEL" not in output
    assert rejection.reason.value not in output
    assert all(
        not {"quality", "score", "threshold"}.intersection(record)
        for record in first_records + replay_records
    )


@pytest.mark.django_db(transaction=True)
def test_once_exits_immediately_when_stream_has_no_messages(inference_stream, capsys):
    """空闲 Stream 的单轮命令立即返回且不制造任务消息。"""
    client, stream, _ = inference_stream

    _run_once()

    assert client.xrange(stream) == []
    assert [record["event"] for record in parse_console_records(capsys.readouterr().out)] == [
        "inference.worker.starting",
        "inference.worker.ready",
        "inference.worker.stopped",
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
        task = _create_test_metric_task()
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
        task = _create_test_metric_task(last_dispatched_at=timezone.now())
        execution_token = uuid4()
        Task.objects.filter(pk=task.pk).update(
            status=Task.StatusChoices.RUNNING,
            started_at=timezone.now(),
            execution_token=execution_token,
            lease_expires_at=(timezone.now() + timedelta(seconds=settings.INFERENCE_LEASE_SECONDS)),
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
        task = _create_test_metric_task(last_dispatched_at=timezone.now())
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
    assert task.result_payload == {"completed": True}
    assert client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_successful_terminal_message_recovers_outputs_without_rerunning(
    tmp_path: Path,
    inference_stream,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """成功终态消息重复到达时只恢复正式目录，不再计算或改写指标。"""
    client, stream, group = inference_stream
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task = _create_test_metric_task()
        storage = LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts")
        execution_token = uuid4()
        execution_dir = storage.create_execution_output_dir(task.pk, execution_token)
        output_file = execution_dir / "processed.csv"
        output_file.write_bytes(b"authoritative result")
        output_key = storage.validate_output_file(
            task.pk,
            execution_token,
            output_file,
        )
        completed_at = timezone.now()
        Task.objects.filter(pk=task.pk).update(
            status=Task.StatusChoices.SUCCEEDED,
            result_payload={"completed": True, "output_files": {"report": output_key}},
            started_at=completed_at,
            finished_at=completed_at,
            attempt_count=1,
        )
        published_dir = (
            storage.task_data_root
            / task.channel.label
            / task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            / "outputs"
            / str(task.pk)
        )
        assert not published_dir.is_symlink()
        original_metrics = list(
            TaskMetric.objects.filter(task=task).values_list(
                "metric_id",
                "numeric_value",
                "boolean_value",
            )
        )
        run_calls: list[int] = []

        def unexpected_inference(*, task_id: int, execution_token) -> None:
            """检测终态消息是否错误地启动了算法。"""
            run_calls.append(task_id)
            raise AssertionError("终态任务不得重新执行")

        monkeypatch.setattr(
            inference_command,
            "run_task_inference",
            unexpected_inference,
        )
        client.xadd(stream, {"task_id": str(task.pk)})
        _run_once("output-recovery")
        assert published_dir.is_symlink()
        assert published_dir.resolve() == execution_dir.resolve()

        client.xadd(stream, {"task_id": str(task.pk)})
        _run_once("output-recovery")

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == {
        "completed": True,
        "output_files": {"report": output_key},
    }
    assert task.attempt_count == 1
    assert run_calls == []
    assert (
        list(
            TaskMetric.objects.filter(task=task).values_list(
                "metric_id",
                "numeric_value",
                "boolean_value",
            )
        )
        == original_metrics
    )
    assert client.xpending(stream, group)["pending"] == 0
    assert [path.name for path in published_dir.iterdir()] == ["processed.csv"]
    assert (published_dir / "processed.csv").read_bytes() == b"authoritative result"
    records = parse_console_records(capsys.readouterr().out)
    assert not {
        "inference.task.started",
        "inference.task.succeeded",
    }.intersection(record["event"] for record in records)


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
        task = _create_test_metric_task()

        def slow_inference(*, task_id, execution_token):
            """延迟后执行测试算法以跨越初始租约。"""
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
        task = _create_test_metric_task()

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
    assert lost[0]["reason"] == (
        "heartbeat_error" if heartbeat_failure == "database-error" else "lease_expired"
    )
    assert all(
        record["event"] not in {"inference.task.succeeded", "inference.task.failed"}
        for record in records
    )
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
        first_task = _create_test_metric_task()
        second_task = _create_metric_task_for_version(first_task.model_version_id)

        def stop_after_start(*, task_id, execution_token):
            """触发 SIGTERM 后继续完成已经开始的测试算法。"""
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
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks", MODEL_ARTIFACT_ROOT=tmp_path / "models"
    ):
        task, _username = _create_ppg_v1_input_failure_task("HEALTH-INPUT-SENTINEL")
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
    tmp_path,
    inference_stream,
    monkeypatch,
    capsys,
    boundary,
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
        Task.objects.filter(pk=task_id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        return actual_save(task_id=task_id, execution_token=execution_token, result=result)

    def interrupted_ack(*args, **kwargs):
        """模拟终态提交后的 Redis 确认故障。"""
        raise RedisConnectionError("PRIVATE-REDIS-SENTINEL")

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks", MODEL_ARTIFACT_ROOT=tmp_path / "models"
    ):
        task = _create_test_metric_task()
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


def _create_ppg_v1_input_failure_task(content: str) -> tuple[Task, str]:
    """创建已发布正式 v1 和带指定 PPG 文件的待执行任务。"""
    username = f"consumer-ppg-v1-{uuid4().hex}"
    user = get_user_model().objects.create_user(
        username=username,
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"consumer-ppg-v1-{uuid4().hex}",
        name="PPG HR/BR v1",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="ppg.active.offline.hr_br.v1",
        params=None,
    )
    definitions = (
        ("hr", "beats_per_minute"),
        ("br", "breaths_per_minute"),
    )
    for code, unit in definitions:
        create_metric_definition(
            code=code,
            description=code,
            value_type="number",
            unit=unit,
        )
    set_model_version_metrics(
        version_id=draft.version_id,
        metric_codes=tuple(code for code, _ in definitions),
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
    )
    _store_ppg_input(task, uploaded_at=now, content=content)
    return task, username


@pytest.mark.django_db(transaction=True)
def test_official_ppg_v1_http_task_executes_and_downloads_outputs(
    tmp_path,
    inference_stream,
    monkeypatch,
    capsys,
):
    """创建日与执行日跨日后，经 HTTP 上传正式 HR/BR 输入并下载结果。"""
    redis_client, stream, group = inference_stream
    historical_ppg_bytes = synthetic_ppg_csv(heart_rate_hz=1.0).encode("utf-8")
    selected_ppg_bytes = synthetic_ppg_csv(heart_rate_hz=1.2).encode("utf-8")
    password = "Dawn-valley-483!strong"
    user_model = get_user_model()
    manager = user_model.objects.create_user(
        username=f"consumer-manager-{uuid4().hex}",
        password=password,
        is_staff=True,
        is_superuser=False,
    )
    owner = user_model.objects.create_user(
        username=f"consumer-owner-{uuid4().hex}",
        password=password,
    )
    other_user = user_model.objects.create_user(
        username=f"consumer-other-{uuid4().hex}",
        password=password,
    )

    async def prepare_task():
        """通过实际 HTTP 接口创建正式版本、上传两份 PPG 并提交。"""
        client = AsyncClient()
        manager_login = await client.post(
            "/api/v1/manager/auth/login",
            data=json.dumps({"username": manager.username, "password": password}),
            content_type="application/json",
        )
        assert manager_login.status_code == 200
        manager_headers = {
            "Authorization": (f"Bearer {manager_login.json()['data']['accessToken']}")
        }
        model_response = await client.post(
            "/api/v1/manager/models",
            data=json.dumps({"code": f"ppg-v1-{uuid4().hex}", "name": "PPG HR/BR v1"}),
            content_type="application/json",
            headers=manager_headers,
        )
        assert model_response.status_code == 201
        model_id = model_response.json()["data"]["modelId"]
        version_response = await client.post(
            f"/api/v1/manager/models/{model_id}/versions",
            data=json.dumps(
                {
                    "version": "1.0",
                    "algorithmKey": "ppg.active.offline.hr_br.v1",
                    "params": None,
                }
            ),
            content_type="application/json",
            headers=manager_headers,
        )
        assert version_response.status_code == 201
        version_id = version_response.json()["data"]["versionId"]
        metric_responses = []
        for code, unit in (
            ("hr", "beats_per_minute"),
            ("br", "breaths_per_minute"),
        ):
            metric_responses.append(
                await client.post(
                    "/api/v1/manager/metrics",
                    data=json.dumps(
                        {
                            "code": code,
                            "description": code,
                            "valueType": "number",
                            "unit": unit,
                        }
                    ),
                    content_type="application/json",
                    headers=manager_headers,
                )
            )
        metrics_response = await client.put(
            f"/api/v1/manager/model-versions/{version_id}/metrics",
            data=json.dumps({"metricCodes": ["hr", "br"]}),
            content_type="application/json",
            headers=manager_headers,
        )
        publish_response = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/publish",
            headers=manager_headers,
        )
        selection_response = await client.put(
            f"/api/v1/manager/channels/ios-cn/models/{model_id}/selection",
            data=json.dumps({"versionId": version_id}),
            content_type="application/json",
            headers=manager_headers,
        )
        assert all(response.status_code == 201 for response in metric_responses)
        assert metrics_response.status_code == 200
        assert publish_response.status_code == 200
        assert publish_response.json()["data"]["publishedAt"] is not None
        assert selection_response.status_code == 200

        owner_login = await client.post(
            "/api/v1/ios-cn/auth/login",
            data=json.dumps({"username": owner.username, "password": password}),
            content_type="application/json",
        )
        assert owner_login.status_code == 200
        owner_headers = {"Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"}
        created = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps({"modelId": model_id}),
            content_type="application/json",
            headers=owner_headers,
        )
        assert created.status_code == 201
        created_data = created.json()["data"]
        task_id = created_data["taskId"]
        created_at = timezone.now() - timedelta(days=1)
        await sync_to_async(Task.objects.filter(pk=task_id).update, thread_sensitive=True)(
            created_at=created_at
        )
        historical_upload = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile(
                    "synthetic-ppg-history.csv",
                    historical_ppg_bytes,
                    content_type="text/csv",
                ),
            },
            headers=owner_headers,
        )
        uploaded = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile(
                    "synthetic-ppg-current.csv",
                    selected_ppg_bytes,
                    content_type="text/csv",
                ),
            },
            headers=owner_headers,
        )
        submitted = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=json.dumps({}),
            content_type="application/json",
            headers=owner_headers,
        )
        await sync_to_async(connections.close_all, thread_sensitive=True)()
        return (
            model_id,
            version_id,
            task_id,
            owner_headers,
            created_data,
            historical_upload,
            uploaded,
            submitted,
        )

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024 * 1024,
    ):
        (
            model_id,
            version_id,
            task_id,
            owner_headers,
            created_data,
            historical_upload,
            uploaded,
            submitted,
        ) = asyncio.run(prepare_task())
        assert created_data["versionId"] == version_id
        assert created_data["isExample"] is False
        assert historical_upload.status_code == uploaded.status_code == 201
        historical_file_id = historical_upload.json()["data"]["fileId"]
        historical_data = historical_upload.json()["data"]
        upload_data = uploaded.json()["data"]
        assert historical_data["logicalName"] == upload_data["logicalName"] == "ppg"
        assert upload_data["isSelected"] is True
        assert historical_file_id != upload_data["fileId"]
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "SUBMITTED"
        actual_replace = os.replace
        replacement_attempts = 0

        def fail_first_publication(source, destination):
            """在成功事务后仅模拟一次正式链接替换故障。"""
            nonlocal replacement_attempts
            replacement_attempts += 1
            if replacement_attempts == 1:
                raise OSError("PRIVATE-OUTPUT-PUBLISH-DETAIL")
            return actual_replace(source, destination)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "replace", fail_first_publication)
            _run_once("ppg-v1-http")

        assert replacement_attempts == 1
        consumer_records = parse_console_records(capsys.readouterr().out)
        publication_failures = [
            record
            for record in consumer_records
            if record["event"] == "inference.output.publish_failed"
        ]
        assert len(publication_failures) == 1
        assert publication_failures[0]["taskId"] == str(task_id)
        assert publication_failures[0]["reason"] == "output_publish_failed"
        assert "PRIVATE-OUTPUT-PUBLISH-DETAIL" not in str(consumer_records)
        completed_task = Task.objects.get(pk=task_id)
        assert completed_task.status == Task.StatusChoices.SUCCEEDED
        assert completed_task.attempt_count == 1
        published_output_dir = (
            Path(settings.TASK_DATA_ROOT)
            / "ios-cn"
            / completed_task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            / "outputs"
            / str(task_id)
        )
        assert not published_output_dir.is_symlink()

        async def query_and_download():
            """用任务所有者令牌读取详情及两个真实流式输出。"""
            client = AsyncClient()
            detail = await client.get(
                f"/api/v1/ios-cn/tasks/{task_id}",
                headers=owner_headers,
            )
            historical_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task_id}/files/{historical_file_id}/download",
                headers=owner_headers,
            )
            ppg_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task_id}/outputs/processed_ppg/download",
                headers=owner_headers,
            )
            rsp_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task_id}/outputs/processed_rsp/download",
                headers=owner_headers,
            )
            other_login = await client.post(
                "/api/v1/ios-cn/auth/login",
                data=json.dumps({"username": other_user.username, "password": password}),
                content_type="application/json",
            )
            wrong_channel_login = await client.post(
                "/api/v1/wxapp/auth/login",
                data=json.dumps({"username": owner.username, "password": password}),
                content_type="application/json",
            )
            other_headers = {
                "Authorization": (f"Bearer {other_login.json()['data']['accessToken']}")
            }
            wrong_channel_headers = {
                "Authorization": (f"Bearer {wrong_channel_login.json()['data']['accessToken']}")
            }
            unauthorized_responses = (
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task_id}/outputs/processed_ppg/download",
                    headers=other_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task_id}/outputs/processed_rsp/download",
                    headers=other_headers,
                ),
                await client.get(
                    f"/api/v1/wxapp/tasks/{task_id}/outputs/processed_ppg/download",
                    headers=wrong_channel_headers,
                ),
                await client.get(
                    f"/api/v1/wxapp/tasks/{task_id}/outputs/processed_rsp/download",
                    headers=wrong_channel_headers,
                ),
            )

            assert other_login.status_code == wrong_channel_login.status_code == 200

            async def read_body(response):
                """读取普通或流式 HTTP 响应字节。"""
                if not response.streaming:
                    return response.content
                content = response.streaming_content
                if hasattr(content, "__aiter__"):
                    return b"".join([chunk async for chunk in content])
                return b"".join(content)

            historical_body = await read_body(historical_response)
            bodies = (
                await read_body(ppg_response),
                await read_body(rsp_response),
            )
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return (
                detail,
                historical_response.status_code,
                historical_body,
                ppg_response.status_code,
                rsp_response.status_code,
                bodies,
                tuple(response.status_code for response in unauthorized_responses),
            )

        (
            detail,
            historical_input_status,
            historical_input_body,
            ppg_status,
            rsp_status,
            output_bodies,
            unauthorized_statuses,
        ) = asyncio.run(query_and_download())

    task = Task.objects.get(pk=task_id)
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.model_version_id == version_id
    assert task.attempt_count == 1
    storage = LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts")
    historical_input = TaskInputFile.objects.get(pk=historical_file_id)
    selected_input = TaskInputFile.objects.get(pk=upload_data["fileId"])
    assert not historical_input.is_selected and selected_input.is_selected
    assert historical_input.storage_key != selected_input.storage_key
    created_local = task.created_at.astimezone(ZoneInfo("Asia/Shanghai"))
    date_part = created_local.strftime("%Y%m%d")
    execution_local = task.finished_at.astimezone(ZoneInfo("Asia/Shanghai"))
    assert execution_local.date() > created_local.date()
    timestamp_part = created_local.strftime("%Y%m%d_%H%M%S")
    input_prefix = (
        f"ios-cn/{date_part}/{timestamp_part}-{task.pk}-{task.user_id}-"
        "ppg.active.offline.hr_br.v1-ppg-"
    )
    for input_record, expected_bytes in (
        (historical_input, historical_ppg_bytes),
        (selected_input, selected_ppg_bytes),
    ):
        assert input_record.storage_key.startswith(input_prefix)
        assert input_record.storage_key.endswith(".csv")
        uuid_part = Path(input_record.storage_key).stem.rsplit("-", 1)[-1]
        assert len(uuid_part) == 32
        assert all(character in "0123456789abcdef" for character in uuid_part)
        assert (
            storage.resolve_file(
                StorageArea.TASK_DATA,
                input_record.storage_key,
            ).read_bytes()
            == expected_bytes
        )
    output_keys = task.result_payload["output_files"]
    assert set(output_keys) == {"processed_ppg", "processed_rsp"}
    output_paths = {
        logical_name: storage.resolve_file(StorageArea.TASK_DATA, storage_key)
        for logical_name, storage_key in output_keys.items()
    }
    assert {path.name for path in output_paths.values()} == {
        "ppg-processed.csv",
        "rsp-processed.csv",
    }
    execution_dirs = {path.parent for path in output_paths.values()}
    assert len(execution_dirs) == 1
    execution_dir = next(iter(execution_dirs))
    execution_tokens = {key.split("/")[2] for key in output_keys.values()}
    assert len(execution_tokens) == 1
    execution_token = next(iter(execution_tokens))
    assert all(
        key.startswith(f".executions/{task.pk}/{execution_token}/") for key in output_keys.values()
    )
    published_dir = storage.task_data_root / "ios-cn" / date_part / "outputs" / str(task.pk)
    assert published_dir.is_symlink()
    assert published_dir.resolve() == execution_dir
    assert {path.name for path in published_dir.iterdir()} == {
        "ppg-processed.csv",
        "rsp-processed.csv",
    }
    assert output_paths["processed_ppg"].read_bytes() == output_bodies[0]
    assert output_paths["processed_rsp"].read_bytes() == output_bodies[1]
    assert historical_input_status == 200
    assert historical_input_body == historical_ppg_bytes
    assert task.result_payload["hr"] == pytest.approx(72.0, abs=0.2)
    assert math.isfinite(task.result_payload["br"])
    assert task.result_payload["quality"]["score"] >= 70.0
    assert task.result_payload["quality"]["threshold"] == 70
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["modelId"] == model_id
    assert detail_data["versionId"] == version_id
    assert detail_data["isExample"] is False
    assert detail_data["status"] == "SUCCEEDED"
    result = detail_data["result"]
    assert result["hr"] == task.result_payload["hr"]
    assert result["br"] == task.result_payload["br"]
    assert result["quality"] == task.result_payload["quality"]
    assert set(result["output_files"]) == {"processed_ppg", "processed_rsp"}
    assert str(tmp_path) not in json.dumps(result)

    projected_metrics = {
        item.metric.code: item.numeric_value
        for item in TaskMetric.objects.filter(task=task).select_related("metric")
    }
    assert projected_metrics == {
        "hr": result["hr"],
        "br": result["br"],
    }
    assert (ppg_status, rsp_status) == (200, 200)
    assert unauthorized_statuses == (404, 404, 404, 404)
    ppg_rows = list(csv.DictReader(StringIO(output_bodies[0].decode("utf-8"))))
    rsp_rows = list(csv.DictReader(StringIO(output_bodies[1].decode("utf-8"))))
    assert len(ppg_rows) == len(rsp_rows) == 1800
    assert list(ppg_rows[0]) == ["timestamp", "ppg"]
    assert list(rsp_rows[0]) == ["timestamp", "rsp"]
    expected_timestamps = [index * 1000.0 / 30.0 for index in range(1800)]
    for ppg_row, rsp_row, timestamp in zip(
        ppg_rows,
        rsp_rows,
        expected_timestamps,
        strict=True,
    ):
        assert float(ppg_row["timestamp"]) == pytest.approx(timestamp)
        assert float(rsp_row["timestamp"]) == pytest.approx(timestamp)
        assert math.isfinite(float(ppg_row["ppg"]))
        assert math.isfinite(float(rsp_row["rsp"]))
    assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_official_ppg_v1_keeps_fixed_versions_and_metric_projections_isolated(
    tmp_path,
    inference_stream,
):
    """正式版本发布、选择及执行后各自保持输入、身份和指标投影。"""
    redis_client, stream, group = inference_stream
    user = get_user_model().objects.create_user(
        username=f"consumer-ppg-version-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"consumer-ppg-version-{uuid4().hex}",
        name="PPG HR/BR version isolation",
        description=None,
    )
    metric_definitions = (
        ("hr", "beats_per_minute"),
        ("br", "breaths_per_minute"),
    )
    for code, unit in metric_definitions:
        create_metric_definition(
            code=code,
            description=code,
            value_type="number",
            unit=unit,
        )

    versions = {}
    for version_name, metric_code in (("1.0", "hr"), ("2.0", "br")):
        draft = create_model_version(
            model_id=model.model_id,
            version=version_name,
            algorithm_key="ppg.active.offline.hr_br.v1",
            params=None,
        )
        set_model_version_metrics(
            version_id=draft.version_id,
            metric_codes=(metric_code,),
        )
        versions[version_name] = publish_model_version(version_id=draft.version_id)

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024 * 1024,
    ):
        select_channel_model_version(
            channel="ios-cn",
            model_id=model.model_id,
            version_id=versions["1.0"].version_id,
        )
        first_draft = create_task_draft(
            user_id=user.pk,
            channel="ios-cn",
            model_id=model.model_id,
        )
        upload_task_input(
            task_id=first_draft.task_id,
            user_id=user.pk,
            channel="ios-cn",
            logical_name="ppg",
            source_filename="first.csv",
            source=SimpleUploadedFile(
                "first.csv",
                synthetic_ppg_csv(heart_rate_hz=1.2).encode("utf-8"),
                content_type="text/csv",
            ),
        )
        first_submission = submit_user_task(
            task_id=first_draft.task_id,
            user_id=user.pk,
            channel="ios-cn",
        )

        select_channel_model_version(
            channel="ios-cn",
            model_id=model.model_id,
            version_id=versions["2.0"].version_id,
        )
        second_draft = create_task_draft(
            user_id=user.pk,
            channel="ios-cn",
            model_id=model.model_id,
        )
        upload_task_input(
            task_id=second_draft.task_id,
            user_id=user.pk,
            channel="ios-cn",
            logical_name="ppg",
            source_filename="second.csv",
            source=SimpleUploadedFile(
                "second.csv",
                synthetic_ppg_csv(heart_rate_hz=1.5).encode("utf-8"),
                content_type="text/csv",
            ),
        )
        second_submission = submit_user_task(
            task_id=second_draft.task_id,
            user_id=user.pk,
            channel="ios-cn",
        )
        assert first_submission.status == second_submission.status == "SUBMITTED"
        _run_once("ppg-version-isolation")

    first_task = Task.objects.get(pk=first_draft.task_id)
    second_task = Task.objects.get(pk=second_draft.task_id)
    assert first_task.status == second_task.status == Task.StatusChoices.SUCCEEDED
    assert first_task.model_version_id == versions["1.0"].version_id
    assert second_task.model_version_id == versions["2.0"].version_id
    assert first_task.result_payload["hr"] == pytest.approx(72.0, abs=0.2)
    assert second_task.result_payload["hr"] == pytest.approx(90.0, abs=0.2)
    for task in (first_task, second_task):
        assert math.isfinite(task.result_payload["br"])
        assert task.result_payload["quality"]["score"] >= 70.0
        assert task.result_payload["quality"]["threshold"] == 70

    first_metrics = {
        item.metric.code: item.numeric_value
        for item in TaskMetric.objects.filter(task=first_task).select_related("metric")
    }
    second_metrics = {
        item.metric.code: item.numeric_value
        for item in TaskMetric.objects.filter(task=second_task).select_related("metric")
    }
    assert first_metrics == {"hr": pytest.approx(72.0, abs=0.2)}
    assert second_metrics == {"br": second_task.result_payload["br"]}
    assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_formal_ppg_quality_rejection_has_no_output_or_metrics(
    tmp_path,
    inference_stream,
):
    """真实 HR/BR v1 质量拒绝不写成功结果、指标或处理文件。"""
    redis_client, stream, group = inference_stream
    content = synthetic_ppg_csv(heart_rate_hz=1.2, noise_amplitude=30.0)
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task, username = _create_ppg_v1_input_failure_task(content)
        _run_once("ppg-quality-rejection")
        task.refresh_from_db()

        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
        assert task.failure_message == "信号质量评分未达到要求，请重新采集"
        assert task.failure_detail["quality"]["reason"] == "LOW_SCORE"
        score = task.failure_detail["quality"]["details"]["score"]
        assert score < 70.0
        assert task.failure_detail["quality"]["details"]["threshold"] == 70
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        output_root = (
            tmp_path
            / "tasks"
            / task.channel.label
            / task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            / "outputs"
            / str(task.pk)
        )
        assert not output_root.exists()
        assert not output_root.is_symlink()

        async def query_failure():
            """通过本人详情接口核对受控质量拒绝文案和明细。"""
            client = AsyncClient()
            login = await client.post(
                "/api/v1/ios-cn/auth/login",
                data=json.dumps(
                    {
                        "username": username,
                        "password": "Dawn-valley-483!strong",
                    }
                ),
                content_type="application/json",
            )
            headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
            detail = await client.get(
                f"/api/v1/ios-cn/tasks/{task.pk}",
                headers=headers,
            )
            output_download = await client.get(
                f"/api/v1/ios-cn/tasks/{task.pk}/outputs/processed_ppg/download",
                headers=headers,
            )
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return login, detail, output_download

        login, detail, output_download = asyncio.run(query_failure())

    assert login.status_code == 200
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["status"] == Task.StatusChoices.FAILED
    assert detail_data["failureCode"] == "INFERENCE.QUALITY_REJECTED"
    assert detail_data["failureMessage"] == "信号质量评分未达到要求，请重新采集"
    assert detail_data["result"] is None
    assert detail_data["qualityFailure"] == {
        "reason": "LOW_SCORE",
        "details": {"score": score, "threshold": 70},
    }
    assert not {"failureDetail", "profileSnapshot"}.intersection(detail_data)
    assert output_download.status_code == 404
    assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_controlled_input_failure_is_public_and_stale_token_cannot_overwrite(
    tmp_path,
    inference_stream,
    monkeypatch,
):
    """单轮消费者安全呈现多块错误并拒绝旧令牌改写终态。"""
    redis_client, stream, group = inference_stream
    claimed_tokens = []
    actual_claim = inference_command.claim_task_for_execution

    def capture_claimed_token(*, task_id: int):
        """记录消费者实际取得的执行令牌用于终态失权验证。"""
        token = actual_claim(task_id=task_id)
        if token is not None:
            claimed_tokens.append(token)
        return token

    monkeypatch.setattr(
        inference_command,
        "claim_task_for_execution",
        capture_claimed_token,
    )
    content = "timestampFrame,avgR,avgG,avgB,blockIndex\n0,120,128,136,1\n100,121,128,136,0\n"
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task, username = _create_ppg_v1_input_failure_task(content)
        _run_once()
        task.refresh_from_db()

        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.INVALID_INPUT"
        assert task.failure_message == "当前算法版本仅支持单块 PPG"
        assert task.failure_detail is None
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        assert task.attempt_count == 1
        assert len(claimed_tokens) == 1
        assert redis_client.xpending(stream, group)["pending"] == 0

        async def query_task_detail():
            """用任务所有者令牌读取普通详情中的受控失败文案。"""
            client = AsyncClient()
            login = await client.post(
                "/api/v1/ios-cn/auth/login",
                data=json.dumps({"username": username, "password": "Dawn-valley-483!strong"}),
                content_type="application/json",
            )
            try:
                response = await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}",
                    headers={"Authorization": f"Bearer {login.json()['data']['accessToken']}"},
                )
                return login, response
            finally:
                await sync_to_async(connections.close_all, thread_sensitive=True)()

        login, detail = asyncio.run(query_task_detail())
        assert login.status_code == 200
        assert detail.status_code == 200
        detail_data = detail.json()["data"]
        assert detail_data["status"] == Task.StatusChoices.FAILED
        assert detail_data["failureCode"] == "INFERENCE.INVALID_INPUT"
        assert detail_data["failureMessage"] == "当前算法版本仅支持单块 PPG"
        assert detail_data["result"] is None
        assert not {"failureDetail", "profileSnapshot"}.intersection(detail_data)

        original_failure = (
            task.failure_code,
            task.failure_message,
            task.failure_detail,
            task.finished_at,
        )
        assert (
            record_task_execution_failure(
                task_id=task.pk,
                execution_token=claimed_tokens[0],
                error=InputError(InputErrorReason.INVALID_TIMESTAMP),
            )
            is None
        )
        task.refresh_from_db()
        assert (
            task.failure_code,
            task.failure_message,
            task.failure_detail,
            task.finished_at,
        ) == original_failure
        assert not TaskMetric.objects.filter(task=task).exists()
