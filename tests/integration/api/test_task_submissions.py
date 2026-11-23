"""验证任务最终提交 HTTP 契约及快照隐私。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import AsyncClient, override_settings
from django.utils import timezone
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.task_exceptions import TaskConflict
from health_service.application.tasks import create_task_draft, submit_user_task
from health_service.infrastructure.persistence import tasks as task_persistence
from health_service.models import Channel, Task, TaskMetric
from health_service.schemas.internal.tasks import TaskMeasurementData

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


async def _login(client: AsyncClient, username: str, password: str):
    """通过真实普通渠道登录接口取得 Access。"""
    return await client.post(
        "/api/v1/ios-cn/auth/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


async def _close_worker_database_connection() -> None:
    """关闭异步请求同步线程持有的数据库连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()


async def _create_uploaded_task(
    client: AsyncClient,
    headers: dict[str, str],
    model_id: int,
) -> int:
    """创建任务草稿并上传有效 PPG 输入。"""
    created = await client.post(
        "/api/v1/ios-cn/tasks",
        data=json.dumps({"modelId": model_id}),
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
                b"timestampFrame,avgR\n1,2\n",
                content_type="text/csv",
            ),
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert uploaded.status_code == 201
    return task_id


@pytest.mark.django_db(transaction=True)
def test_submit_route_requires_uploaded_input_and_hides_profile_snapshot(tmp_path):
    """HTTP 提交缺文件时不转态，成功后返回状态而不暴露快照。"""
    username = "task-submit-http-owner"
    password = "Prairie-cloud-327!strong"
    get_user_model().objects.create_user(username=username, password=password)
    model = create_model(code="task-submit-http", name="Task submit", description=None)
    version_draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=version_draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )

    async def exercise_api():
        """登录并提交一个完整输入草稿，随后检查重复提交。"""
        client = AsyncClient()
        login = await _login(client, username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        created = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps({"modelId": model.model_id}),
            content_type="application/json",
            headers=headers,
        )
        task_id = created.json()["data"]["taskId"]
        missing_input = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=b"",
            content_type="application/octet-stream",
            headers=headers,
        )
        uploaded = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile(
                    "ppg.csv",
                    b"timestampFrame,avgR\n1,2\n",
                    content_type="text/csv",
                ),
            },
            headers=headers,
        )
        invalid_metadata = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=json.dumps({"clientMetadata": []}),
            content_type="application/json",
            headers=headers,
        )
        submitted = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=b"",
            content_type="application/octet-stream",
            headers=headers,
        )
        repeated = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=b"",
            content_type="application/octet-stream",
            headers=headers,
        )
        detail = await client.get(
            f"/api/v1/ios-cn/tasks/{task_id}",
            headers=headers,
        )
        await _close_worker_database_connection()
        return (
            created,
            missing_input,
            uploaded,
            invalid_metadata,
            submitted,
            repeated,
            detail,
            task_id,
        )

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
        REDIS_URL="redis://127.0.0.1:1/0",
        INFERENCE_STREAM=f"test:inference:{uuid4().hex}",
    ):
        (
            created,
            missing_input,
            uploaded,
            invalid_metadata,
            submitted,
            repeated,
            detail,
            task_id,
        ) = asyncio.run(exercise_api())

    assert created.status_code == 201
    assert missing_input.status_code == 422
    assert invalid_metadata.status_code == 422
    assert uploaded.status_code == 201
    assert submitted.status_code == 200, submitted.content
    assert submitted.json()["data"]["status"] == Task.StatusChoices.SUBMITTED
    assert submitted.json()["data"]["submittedAt"] is not None
    assert repeated.status_code == 409
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == Task.StatusChoices.SUBMITTED
    assert detail.json()["data"]["measurement"] is None
    assert not {"profileSnapshot", "profile_snapshot"}.intersection(detail.json()["data"])
    record = Task.objects.get(pk=task_id)
    assert record.profile_snapshot["age_years"] is None
    assert record.profile_snapshot["history_json"] is None
    assert record.status == Task.StatusChoices.SUBMITTED
    assert record.last_dispatched_at is None


@pytest.mark.django_db(transaction=True)
def test_submit_accepts_optional_client_metadata_without_exposing_it(
    tmp_path,
    capsys,
    monkeypatch,
):
    """验证元数据可选持久化、不可改写，且不进入公开响应或日志。"""
    username = "task-client-metadata-owner"
    password = "Prairie-cloud-227!strong"
    get_user_model().objects.create_user(username=username, password=password)
    model = create_model(
        code="task-client-metadata",
        name="Task metadata",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    metadata = {
        "deviceModel": "CLIENT-METADATA-SECRET-7E30",
        "brand": "Example",
        "custom": {"appBuild": 42},
    }
    measurement = {
        "captureMethod": "fingertip_chest",
        "captureMode": "active",
        "durationMs": 30251,
    }

    async def exercise_api():
        """通过真实认证和任务接口覆盖元数据的三种可选输入。"""
        client = AsyncClient()
        login = await _login(client, username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}

        object_task_id = await _create_uploaded_task(
            client,
            headers,
            model.model_id,
        )
        object_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{object_task_id}/submit",
            data=json.dumps({"clientMetadata": metadata, "measurement": measurement}),
            content_type="application/json",
            headers=headers,
        )
        repeated_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{object_task_id}/submit",
            data=json.dumps({
                "clientMetadata": {"deviceModel": "changed"},
                "measurement": {**measurement, "durationMs": 1},
            }),
            content_type="application/json",
            headers=headers,
        )
        object_detail = await client.get(
            f"/api/v1/ios-cn/tasks/{object_task_id}",
            headers=headers,
        )

        omitted_task_id = await _create_uploaded_task(
            client,
            headers,
            model.model_id,
        )
        omitted_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{omitted_task_id}/submit",
            data="{}",
            content_type="application/json",
            headers=headers,
        )

        null_task_id = await _create_uploaded_task(
            client,
            headers,
            model.model_id,
        )
        null_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{null_task_id}/submit",
            data=json.dumps({"clientMetadata": None, "measurement": None}),
            content_type="application/json",
            headers=headers,
        )

        invalid_task_id = await _create_uploaded_task(
            client,
            headers,
            model.model_id,
        )
        invalid_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{invalid_task_id}/submit",
            data=json.dumps({"clientMetadata": []}),
            content_type="application/json",
            headers=headers,
        )
        invalid_detail = await client.get(
            f"/api/v1/ios-cn/tasks/{invalid_task_id}",
            headers=headers,
        )
        await _close_worker_database_connection()
        rollback_task_id = await _create_uploaded_task(
            client,
            headers,
            model.model_id,
        )
        mark_task_submitted = task_persistence.mark_task_submitted

        def fail_after_persisting_submission(**kwargs):
            """在 SQL 更新后模拟事务内故障。"""
            mark_task_submitted(**kwargs)
            raise RuntimeError("injected submission failure")

        with monkeypatch.context() as patch:
            patch.setattr(
                task_persistence,
                "mark_task_submitted",
                fail_after_persisting_submission,
            )
            rollback_submit = await client.post(
                f"/api/v1/ios-cn/tasks/{rollback_task_id}/submit",
                data=json.dumps({"clientMetadata": metadata, "measurement": measurement}),
                content_type="application/json",
                headers=headers,
            )
        rollback_detail = await client.get(
            f"/api/v1/ios-cn/tasks/{rollback_task_id}",
            headers=headers,
        )
        return (
            object_task_id,
            object_submit,
            repeated_submit,
            object_detail,
            omitted_task_id,
            omitted_submit,
            null_task_id,
            null_submit,
            invalid_task_id,
            invalid_submit,
            invalid_detail,
            rollback_task_id,
            rollback_submit,
            rollback_detail,
        )

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
        REDIS_URL="redis://127.0.0.1:1/0",
        INFERENCE_STREAM=f"test:inference:{uuid4().hex}",
    ):
        (
            object_task_id,
            object_submit,
            repeated_submit,
            object_detail,
            omitted_task_id,
            omitted_submit,
            null_task_id,
            null_submit,
            invalid_task_id,
            invalid_submit,
            invalid_detail,
            rollback_task_id,
            rollback_submit,
            rollback_detail,
        ) = asyncio.run(exercise_api())

    assert object_submit.status_code == 200
    assert repeated_submit.status_code == 409
    assert object_detail.status_code == 200
    assert omitted_submit.status_code == 200
    assert null_submit.status_code == 200
    assert invalid_submit.status_code == 422
    assert invalid_detail.status_code == 200
    assert invalid_detail.json()["data"]["status"] == Task.StatusChoices.DRAFT
    assert rollback_submit.status_code == 500
    assert rollback_detail.status_code == 200
    assert rollback_detail.json()["data"]["status"] == Task.StatusChoices.DRAFT

    object_record = Task.objects.get(pk=object_task_id)
    assert object_record.client_metadata == metadata
    assert "clientMetadata" not in object_record.profile_snapshot
    assert object_record.status == Task.StatusChoices.SUBMITTED
    assert (object_record.capture_method, object_record.capture_mode, object_record.duration_ms) == (
        "fingertip_chest", "active", 30251
    )
    assert "measurement" not in object_record.profile_snapshot
    assert object_detail.json()["data"]["measurement"] == measurement
    assert "measurement" not in object_submit.json()["data"]
    assert Task.objects.get(pk=omitted_task_id).client_metadata is None
    assert Task.objects.get(pk=null_task_id).client_metadata is None
    invalid_record = Task.objects.get(pk=invalid_task_id)
    assert invalid_record.client_metadata is None
    assert invalid_record.profile_snapshot is None
    assert invalid_record.status == Task.StatusChoices.DRAFT
    rollback_record = Task.objects.get(pk=rollback_task_id)
    assert rollback_record.status == Task.StatusChoices.DRAFT
    assert rollback_record.profile_snapshot is None
    assert rollback_record.submitted_at is None
    assert rollback_record.client_metadata is None
    assert (rollback_record.capture_method, rollback_record.capture_mode, rollback_record.duration_ms) == (
        None, None, None
    )
    assert rollback_detail.json()["data"]["measurement"] is None

    for response in (object_submit, object_detail):
        assert "clientMetadata" not in response.json()["data"]
        assert metadata["deviceModel"] not in response.content.decode()
    assert metadata["deviceModel"] not in capsys.readouterr().out


@pytest.mark.django_db(transaction=True)
def test_success_detail_returns_authoritative_result_without_snapshot_or_metric_rebuild():
    """HTTP 详情返回保存结果原值，不公开快照或重组指标。"""
    username = "task-result-http-owner"
    password = "Prairie-cloud-427!strong"
    user = get_user_model().objects.create_user(username=username, password=password)
    model = create_model(code="task-result-http", name="Task result", description=None)
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    task = create_task_draft(
        user_id=user.pk,
        channel="ios-cn",
        model_id=model.model_id,
    )
    metric = create_metric_definition(
        code="task.result.stored_count",
        description="Stored count",
        value_type="number",
        unit="samples",
    )
    stored_result = {
        "completed": True,
        "execution_marker": "persisted-result",
    }
    submitted_at = timezone.now()
    Task.objects.filter(pk=task.task_id).update(
        status=Task.StatusChoices.SUCCEEDED,
        profile_snapshot={
            "name": "private-name",
            "medical_history": "private-history",
            "absolute_path": "/private/health.csv",
        },
        submitted_at=submitted_at,
        started_at=submitted_at,
        finished_at=timezone.now(),
        result_payload=stored_result,
    )
    TaskMetric.objects.create(
        task_id=task.task_id,
        metric_id=metric.metric_id,
        numeric_value=999.0,
    )

    async def read_detail():
        """通过真实 Access 请求成功任务详情。"""
        client = AsyncClient()
        login = await _login(client, username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        response = await client.get(
            f"/api/v1/ios-cn/tasks/{task.task_id}",
            headers=headers,
        )
        await _close_worker_database_connection()
        return response

    response = asyncio.run(read_detail())
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["status"] == Task.StatusChoices.SUCCEEDED
    assert data["isExample"] is False
    assert data["measurement"] is None
    assert data["result"] == stored_result
    assert "metrics" not in data
    response_text = json.dumps(data, ensure_ascii=False)
    assert "private-name" not in response_text
    assert "private-history" not in response_text
    assert "/private/health.csv" not in response_text


@pytest.fixture
def measurement_submission_context(tmp_path, monkeypatch):
    """为采集提交测试建立真实用户、渠道配置及隔离文件目录。"""
    username = "measurement-submit-owner"
    password = "Prairie-cloud-527!strong"
    user = get_user_model().objects.create_user(username=username, password=password)
    model = create_model(code="measurement-submit", name="采集属性", description=None)
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn", model_id=model.model_id, version_id=version.version_id
    )

    def skip_message_publication(task_id):
        """提交测试不访问消息服务；后台持久化另外显式验证。"""
        return None

    monkeypatch.setattr(
        "health_service.application.tasks.dispatch_task", skip_message_publication
    )
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        yield user, password, model, version


@pytest.mark.django_db(transaction=True)
def test_measurement_rejects_all_invalid_input_without_freezing(
    measurement_submission_context,
):
    """真实路由拒绝不完整、非法类型、边界及所有归属时间覆盖。"""
    user, password, model, _ = measurement_submission_context
    valid = {"captureMethod": "fingertip", "captureMode": "active", "durationMs": 1}
    invalid = [[], "", 1, True, {}]
    invalid += [
        {key: value for key, value in valid.items() if key != missing}
        for missing in valid
    ]
    invalid += [{"captureMethod": "fingertip"}, {"captureMode": "active"}, {"durationMs": 1}]
    invalid += [
        {**valid, "captureMethod": value}
        for value in ("face", "unknown", "", None, True, 1)
    ]
    invalid += [
        {**valid, "captureMode": value}
        for value in ("passive", "", None, True, 1)
    ]
    invalid += [
        {**valid, "durationMs": value}
        for value in (None, True, False, "1", "", 0, -1, 1.0, 1.5, 2**63, -(2**63) - 1)
    ]
    invalid += [
        {**valid, key: "override"}
        for key in (
            "capturedAt", "submittedAt", "user", "userId", "channel", "model",
            "modelId", "version", "versionId", "extra",
        )
    ]
    invalid += [
        {"capture_method": "fingertip", "captureMode": "active", "durationMs": 1},
        {**valid, "capture_method": "fingertip"},
    ]

    async def exercise_api():
        """依次提交非法载荷并确认所有提交快照仍为空。"""
        client = AsyncClient()
        login = await _login(client, user.username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        task_id = await _create_uploaded_task(client, headers, model.model_id)
        draft = await client.get(f"/api/v1/ios-cn/tasks/{task_id}", headers=headers)
        assert draft.json()["data"]["measurement"] is None
        for measurement in invalid:
            response = await client.post(
                f"/api/v1/ios-cn/tasks/{task_id}/submit",
                data=json.dumps({"measurement": measurement, "clientMetadata": {"private": True}}),
                content_type="application/json",
                headers=headers,
            )
            assert response.status_code == 422, (measurement, response.content)
            assert response.json()["code"] == "INVALID_INPUT"
        detail = await client.get(f"/api/v1/ios-cn/tasks/{task_id}", headers=headers)
        assert detail.json()["data"]["status"] == "DRAFT"
        assert detail.json()["data"]["submittedAt"] is None
        assert detail.json()["data"]["measurement"] is None
        await _close_worker_database_connection()
        return task_id

    task_id = asyncio.run(exercise_api())
    record = Task.objects.get(pk=task_id)
    assert record.profile_snapshot is None
    assert record.client_metadata is None
    assert record.submitted_at is None
    assert (record.capture_method, record.capture_mode, record.duration_ms) == (None, None, None)


@pytest.mark.django_db(transaction=True)
def test_measurement_compatibility_and_int64_bounds(measurement_submission_context):
    """兼容旧提交形式，并按原值公开两种方式和严格整数的两端。"""
    user, password, model, _ = measurement_submission_context
    bodies = [
        None,
        {},
        {"measurement": None},
        {"clientMetadata": {"legacy": True}},
        {"measurement": {"captureMethod": "fingertip", "captureMode": "active", "durationMs": 1}},
        {
            "measurement": {
                "captureMethod": "fingertip_chest", "captureMode": "active",
                "durationMs": 2**63 - 1,
            }
        },
    ]

    async def exercise_api():
        """通过认证路由逐个冻结并验证详情和列表的公开边界。"""
        client = AsyncClient()
        login = await _login(client, user.username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        records = []
        for body in bodies:
            task_id = await _create_uploaded_task(client, headers, model.model_id)
            submitted = await client.post(
                f"/api/v1/ios-cn/tasks/{task_id}/submit",
                data=b"" if body is None else json.dumps(body),
                content_type="application/json",
                headers=headers,
            )
            assert submitted.status_code == 200, submitted.content
            assert "measurement" not in submitted.json()["data"]
            detail = await client.get(f"/api/v1/ios-cn/tasks/{task_id}", headers=headers)
            expected = body.get("measurement") if body else None
            assert detail.json()["data"]["measurement"] == expected
            assert not {"clientMetadata", "profileSnapshot"}.intersection(detail.json()["data"])
            records.append((task_id, expected))
        page = await client.get("/api/v1/ios-cn/tasks", headers=headers)
        assert all("measurement" not in item for item in page.json()["data"]["items"])
        await _close_worker_database_connection()
        return records

    for task_id, measurement in asyncio.run(exercise_api()):
        record = Task.objects.get(pk=task_id)
        expected = (
            (measurement["captureMethod"], measurement["captureMode"], measurement["durationMs"])
            if measurement else (None, None, None)
        )
        assert (record.capture_method, record.capture_mode, record.duration_ms) == expected


@pytest.mark.django_db(transaction=True)
def test_concurrent_measurement_submission_freezes_one_whole_snapshot(
    measurement_submission_context,
):
    """不同数据库连接同时提交时只接受一份完整属性和独立元数据。"""
    user, password, model, _ = measurement_submission_context

    async def prepare_task():
        """通过真实上传接口准备并释放异步线程的数据库连接。"""
        client = AsyncClient()
        login = await _login(client, user.username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        task_id = await _create_uploaded_task(client, headers, model.model_id)
        await _close_worker_database_connection()
        return task_id

    task_id = asyncio.run(prepare_task())
    barrier = Barrier(2)

    def submit_candidate(duration):
        """让两个独立连接同时竞争原渠道与任务锁。"""
        try:
            barrier.wait(timeout=10)
            task = submit_user_task(
                task_id=task_id,
                user_id=user.pk,
                channel="ios-cn",
                client_metadata={"candidate": duration},
                measurement=TaskMeasurementData(
                    capture_method="fingertip" if duration == 1 else "fingertip_chest",
                    capture_mode="active",
                    duration_ms=duration,
                ),
            )
            return duration, task.submitted_at
        except TaskConflict:
            return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit_candidate, (1, 2)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    duration, submitted_at = winners[0]
    record = Task.objects.get(pk=task_id)
    assert record.submitted_at == submitted_at
    assert record.client_metadata == {"candidate": duration}
    assert (record.capture_method, record.capture_mode, record.duration_ms) == (
        "fingertip" if duration == 1 else "fingertip_chest", "active", duration
    )
    with pytest.raises(TaskConflict):
        submit_user_task(
            task_id=task_id,
            user_id=user.pk,
            channel="ios-cn",
            measurement=TaskMeasurementData(
                capture_method="fingertip", capture_mode="active", duration_ms=999
            ),
        )
    record.refresh_from_db()
    assert record.duration_ms == duration
    assert record.submitted_at == submitted_at


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["success", "failure", "exhausted"])
def test_background_updates_preserve_measurement_and_first_submission(
    measurement_submission_context, outcome
):
    """派发、领取、续约、失租恢复和两类终态不覆盖冻结采集属性。"""
    user, password, model, _ = measurement_submission_context
    measurement = {
        "captureMethod": "fingertip_chest", "captureMode": "active", "durationMs": 31251
    }

    async def prepare_task():
        """通过真实提交路由建立已冻结采集任务。"""
        client = AsyncClient()
        login = await _login(client, user.username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        task_id = await _create_uploaded_task(client, headers, model.model_id)
        response = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=json.dumps({"measurement": measurement, "clientMetadata": {"kept": True}}),
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 200
        await _close_worker_database_connection()
        return task_id

    task_id = asyncio.run(prepare_task())
    record = Task.objects.get(pk=task_id)
    submitted_at = record.submitted_at
    profile_snapshot = record.profile_snapshot

    def assert_frozen():
        """每个后台状态变化后重新读取完整冻结快照。"""
        record.refresh_from_db()
        assert record.submitted_at == submitted_at
        assert record.profile_snapshot == profile_snapshot
        assert record.client_metadata == {"kept": True}
        assert (record.capture_method, record.capture_mode, record.duration_ms) == (
            "fingertip_chest", "active", 31251
        )

    assert task_persistence.mark_task_dispatched(task_id=task_id)
    token = task_persistence.claim_task_for_execution(
        task_id=task_id, lease_seconds=60, max_attempts=2
    )
    assert token is not None
    assert_frozen()
    assert task_persistence.renew_task_execution_lease(
        task_id=task_id, execution_token=token, lease_seconds=60
    )
    assert_frozen()
    Task.objects.filter(pk=task_id).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert task_persistence.mark_task_dispatched(task_id=task_id)
    recovered_token = task_persistence.claim_task_for_execution(
        task_id=task_id, lease_seconds=60, max_attempts=2
    )
    assert recovered_token is not None and recovered_token != token
    assert_frozen()
    if outcome == "success":
        assert task_persistence.save_task_execution_success(
            task_id=task_id, execution_token=recovered_token,
            result_payload={"completed": True}, metrics=(),
        )
    elif outcome == "failure":
        assert task_persistence.record_task_execution_failure(
            task_id=task_id, execution_token=recovered_token,
            failure_code="TASK.ERROR", failure_message="执行失败", failure_detail=None,
        )
    else:
        Task.objects.filter(pk=task_id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        assert task_persistence.claim_task_for_execution(
            task_id=task_id, lease_seconds=60, max_attempts=2
        ) is None
        record.refresh_from_db()
        assert record.failure_code == "TASK.RETRY_EXHAUSTED"
    assert_frozen()


@pytest.mark.django_db(transaction=True)
def test_measurement_task_detail_and_submission_keep_account_channel_isolation(
    measurement_submission_context,
):
    """跨账号或来源渠道的采集信息继续被安全 404 隔离。"""
    user, password, model, version = measurement_submission_context
    other = get_user_model().objects.create_user(
        username="measurement-other-owner", password=password
    )
    channel, _ = Channel.objects.get_or_create(label="android-cn")
    foreign_channel_task = Task.objects.create(
        user=user, channel=channel, model_version_id=version.version_id,
        status="SUBMITTED", submitted_at=timezone.now(),
        capture_method="fingertip_chest", capture_mode="active", duration_ms=98765,
    )

    async def exercise_api():
        """本人先合法提交，再从不同账号和来源尝试读取或覆盖。"""
        owner_client = AsyncClient()
        login = await _login(owner_client, user.username, password)
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        task_id = await _create_uploaded_task(owner_client, headers, model.model_id)
        response = await owner_client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            data=json.dumps({"measurement": {
                "captureMethod": "fingertip", "captureMode": "active", "durationMs": 123
            }}),
            content_type="application/json", headers=headers,
        )
        assert response.status_code == 200
        other_client = AsyncClient()
        other_login = await _login(other_client, other.username, password)
        other_headers = {"Authorization": f"Bearer {other_login.json()['data']['accessToken']}"}
        for client, auth, hidden_id in (
            (other_client, other_headers, task_id),
            (owner_client, headers, foreign_channel_task.pk),
        ):
            detail = await client.get(f"/api/v1/ios-cn/tasks/{hidden_id}", headers=auth)
            submitted = await client.post(
                f"/api/v1/ios-cn/tasks/{hidden_id}/submit",
                data=json.dumps({"measurement": {
                    "captureMethod": "fingertip", "captureMode": "active", "durationMs": 1
                }}),
                content_type="application/json", headers=auth,
            )
            assert detail.status_code == submitted.status_code == 404
            assert detail.json()["data"] is None
        mismatched = await owner_client.get(
            f"/api/v1/android-cn/tasks/{task_id}", headers=headers
        )
        assert mismatched.status_code == 403
        await _close_worker_database_connection()
        return task_id

    task_id = asyncio.run(exercise_api())
    assert Task.objects.get(pk=task_id).duration_ms == 123
    foreign_channel_task.refresh_from_db()
    assert foreign_channel_task.duration_ms == 98765
