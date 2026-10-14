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
from health_service.models import Task, TaskMetric


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
        algorithm_key="hr_br.example",
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
        submitted = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            headers=headers,
        )
        repeated = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/submit",
            headers=headers,
        )
        detail = await client.get(
            f"/api/v1/ios-cn/tasks/{task_id}",
            headers=headers,
        )
        await _close_worker_database_connection()
        return created, missing_input, uploaded, submitted, repeated, detail, task_id

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
            submitted,
            repeated,
            detail,
            task_id,
        ) = asyncio.run(exercise_api())

    assert created.status_code == 201
    assert missing_input.status_code == 422
    assert uploaded.status_code == 201
    assert submitted.status_code == 200
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
def test_success_detail_returns_authoritative_result_without_snapshot_or_metric_rebuild():
    """HTTP 详情返回保存结果原值，不公开快照或重组指标。"""
    username = "task-result-http-owner"
    password = "Prairie-cloud-427!strong"
    user = get_user_model().objects.create_user(username=username, password=password)
    model = create_model(code="task-result-http", name="Task result", description=None)
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="hr_br.example",
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
        "diagnostics": {"sample_count": 4, "duration_seconds": 1.25},
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
    assert data["isExample"] is True
    assert data["result"] == stored_result
    assert "metrics" not in data
    response_text = json.dumps(data, ensure_ascii=False)
    assert "private-name" not in response_text
    assert "private-history" not in response_text
    assert "/private/health.csv" not in response_text
