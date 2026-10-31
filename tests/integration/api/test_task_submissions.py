"""验证任务最终提交 HTTP 契约及快照隐私。"""

import asyncio
import json
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
from health_service.application.tasks import create_task_draft
from health_service.infrastructure.persistence import tasks as task_persistence
from health_service.models import Task, TaskMetric

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
            data=json.dumps({"clientMetadata": metadata}),
            content_type="application/json",
            headers=headers,
        )
        repeated_submit = await client.post(
            f"/api/v1/ios-cn/tasks/{object_task_id}/submit",
            data=json.dumps({"clientMetadata": {"deviceModel": "changed"}}),
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
            data=json.dumps({"clientMetadata": None}),
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
                data=json.dumps({"clientMetadata": metadata}),
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
    assert data["result"] == stored_result
    assert "metrics" not in data
    response_text = json.dumps(data, ensure_ascii=False)
    assert "private-name" not in response_text
    assert "private-history" not in response_text
    assert "/private/health.csv" not in response_text
