"""验证任务草稿、固定渠道授权和普通用户安全响应。"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import AsyncClient
from django.utils import timezone

from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.models import Task


async def _login(client: AsyncClient, channel: str, username: str, password: str):
    """通过真实登录接口获取指定渠道令牌。"""
    return await client.post(
        f"/api/v1/{channel}/auth/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


async def _close_worker_database_connection() -> None:
    """关闭异步请求同步线程持有的数据库连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()


@pytest.mark.django_db(transaction=True)
def test_user_task_routes_enforce_owner_channel_and_request_contract():
    """HTTP 任务路径拒绝伪造身份与版本，并隔离账号和来源渠道。"""
    user_model = get_user_model()
    user_model.objects.create_user(
        username="task-api-owner",
        password="Juniper-field-519!strong",
    )
    user_model.objects.create_user(
        username="task-api-other",
        password="Juniper-field-619!strong",
    )
    model = create_model(code="task-api", name="Task API", description=None)
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
    unselected_model = create_model(
        code="task-api-unselected",
        name="Unselected task API",
        description=None,
    )

    async def exercise_api():
        """验证创建、列表、详情和跨来源渠道的 HTTP 行为。"""
        client = AsyncClient()
        owner_login = await _login(
            client,
            "ios-cn",
            "task-api-owner",
            "Juniper-field-519!strong",
        )
        owner_other_channel_login = await _login(
            client,
            "wxapp",
            "task-api-owner",
            "Juniper-field-519!strong",
        )
        other_login = await _login(
            client,
            "ios-cn",
            "task-api-other",
            "Juniper-field-619!strong",
        )
        owner_headers = {
            "Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"
        }
        other_channel_headers = {
            "Authorization": (
                f"Bearer {owner_other_channel_login.json()['data']['accessToken']}"
            )
        }
        other_headers = {
            "Authorization": f"Bearer {other_login.json()['data']['accessToken']}"
        }

        created = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps({"modelId": model.model_id}),
            content_type="application/json",
            headers=owner_headers,
        )
        task_data = created.json()["data"]
        task_id = task_data["taskId"]
        forged_user = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps({"modelId": model.model_id, "userId": 99999}),
            content_type="application/json",
            headers=owner_headers,
        )
        forged_version = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps(
                {"modelId": model.model_id, "versionId": version.version_id}
            ),
            content_type="application/json",
            headers=owner_headers,
        )
        unconfigured = await client.post(
            "/api/v1/ios-cn/tasks",
            data=json.dumps({"modelId": unselected_model.model_id}),
            content_type="application/json",
            headers=owner_headers,
        )
        page = await client.get(
            "/api/v1/ios-cn/tasks?offset=0&limit=1",
            headers=owner_headers,
        )
        invalid_offset = await client.get(
            "/api/v1/ios-cn/tasks?offset=-1&limit=1",
            headers=owner_headers,
        )
        invalid_limit = await client.get(
            "/api/v1/ios-cn/tasks?offset=0&limit=101",
            headers=owner_headers,
        )
        detail = await client.get(
            f"/api/v1/ios-cn/tasks/{task_id}",
            headers=owner_headers,
        )
        other_account = await client.get(
            f"/api/v1/ios-cn/tasks/{task_id}",
            headers=other_headers,
        )
        other_source_channel = await client.get(
            f"/api/v1/wxapp/tasks/{task_id}",
            headers=other_channel_headers,
        )

        await sync_to_async(Task.objects.filter(pk=task_id).update, thread_sensitive=True)(
            status=Task.StatusChoices.FAILED,
            finished_at=timezone.now(),
            failure_code="INFERENCE.INVALID_INPUT",
            failure_message="输入格式无效",
            failure_detail={"path": "/private/health.csv", "raw": "sensitive"},
        )
        failed_detail = await client.get(
            f"/api/v1/ios-cn/tasks/{task_id}",
            headers=owner_headers,
        )
        await _close_worker_database_connection()
        return (
            created,
            forged_user,
            forged_version,
            unconfigured,
            page,
            invalid_offset,
            invalid_limit,
            detail,
            other_account,
            other_source_channel,
            failed_detail,
            task_id,
        )

    (
        created,
        forged_user,
        forged_version,
        unconfigured,
        page,
        invalid_offset,
        invalid_limit,
        detail,
        other_account,
        other_source_channel,
        failed_detail,
        task_id,
    ) = asyncio.run(exercise_api())

    assert created.status_code == 201
    assert created.json()["data"]["status"] == Task.StatusChoices.DRAFT
    assert created.json()["data"]["versionId"] == version.version_id
    assert created.json()["data"]["isExample"] is True
    assert forged_user.status_code == forged_version.status_code == 422
    assert unconfigured.status_code == 404
    assert page.status_code == 200
    assert page.json()["data"]["total"] == 1
    assert [item["taskId"] for item in page.json()["data"]["items"]] == [task_id]
    assert invalid_offset.status_code == invalid_limit.status_code == 422
    assert detail.status_code == 200
    assert detail.json()["data"]["result"] is None
    assert not {"profileSnapshot", "failureDetail"}.intersection(detail.json()["data"])
    assert other_account.status_code == other_source_channel.status_code == 404
    failure_data = failed_detail.json()["data"]
    assert failed_detail.status_code == 200
    assert failure_data["failureCode"] == "INFERENCE.INVALID_INPUT"
    assert failure_data["failureMessage"] == "输入格式无效"
    assert not {"failureDetail", "profileSnapshot"}.intersection(failure_data)
    assert "/private/health.csv" not in json.dumps(failure_data, ensure_ascii=False)
