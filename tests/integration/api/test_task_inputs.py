"""验证普通渠道输入上传通过 HTTP 完整登记且不泄露存储键。"""

import asyncio
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import AsyncClient, override_settings
from django.utils import timezone
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.models import Task, TaskInputFile

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


async def _login(client: AsyncClient, username: str, password: str):
    """通过真实普通渠道登录接口获取 Access。"""
    return await client.post(
        "/api/v1/ios-cn/auth/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


async def _close_worker_database_connection() -> None:
    """关闭异步请求同步线程持有的数据库连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()


def _set_task_creation_time_to_previous_day(task_id: int) -> None:
    """将测试草稿创建时间移到前一日，模拟跨日上传。"""
    Task.objects.filter(pk=task_id).update(
        created_at=timezone.now() - timedelta(days=1)
    )



@pytest.mark.django_db(transaction=True)
def test_task_input_upload_http_contract(tmp_path):
    """HTTP 上传按 ppg 逻辑名登记完整文件并拒绝未声明输入。"""
    username = "task-input-http-owner"
    password = "Canyon-silver-295!strong"
    get_user_model().objects.create_user(username=username, password=password)
    get_user_model().objects.create_user(
        username="task-input-http-other",
        password="Canyon-silver-295!strong",
    )
    model = create_model(code="task-input-http", name="Task input", description=None)
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

    async def exercise_api():
        """通过登录、建草稿和 multipart 请求验证 HTTP 行为。"""
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
        await sync_to_async(
            _set_task_creation_time_to_previous_day,
            thread_sensitive=True,
        )(task_id)
        uploaded = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile(
                    "client-name.csv",
                    b"timestampFrame,avgR\n1,2\n",
                    content_type="text/csv",
                ),
            },
            headers=headers,
        )
        uppercase = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile(
                    "client-name.CSV",
                    b"timestampFrame,avgR\n2,3\n",
                    content_type="text/csv",
                ),
            },
            headers=headers,
        )
        missing_suffix = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile("client-name", b"missing suffix"),
            },
            headers=headers,
        )
        unsupported_suffix = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile("client-name.json", b"unsupported suffix"),
            },
            headers=headers,
        )
        undeclared = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "other",
                "file": SimpleUploadedFile("other.csv", b"not registered"),
            },
            headers=headers,
        )
        other_login = await _login(
            client,
            "task-input-http-other",
            "Canyon-silver-295!strong",
        )
        other_headers = {
            "Authorization": f"Bearer {other_login.json()['data']['accessToken']}"
        }
        unauthorized = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "ppg",
                "file": SimpleUploadedFile("other.csv", b"unauthorized"),
            },
            headers=other_headers,
        )
        await _close_worker_database_connection()
        return (
            created,
            uploaded,
            uppercase,
            missing_suffix,
            unsupported_suffix,
            undeclared,
            unauthorized,
            task_id,
        )


    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        (
            created,
            uploaded,
            uppercase,
            missing_suffix,
            unsupported_suffix,
            undeclared,
            unauthorized,
            task_id,
        ) = asyncio.run(exercise_api())

    assert created.status_code == 201
    assert uploaded.status_code == 201
    assert uppercase.status_code == 201
    assert missing_suffix.status_code == 422
    assert unsupported_suffix.status_code == 422
    assert undeclared.status_code == 422
    assert unauthorized.status_code == 404
    upload_data = uploaded.json()["data"]
    assert upload_data["logicalName"] == "ppg"
    assert upload_data["sizeBytes"] > 0
    assert upload_data["isSelected"] is True
    assert not {"storageKey", "storage_key", "path"}.intersection(upload_data)
    records = tuple(TaskInputFile.objects.filter(task_id=task_id).order_by("pk"))
    assert len(records) == 2
    selected = next(item for item in records if item.is_selected)
    assert selected.logical_name == "ppg"
    task = selected.task
    task_created_at = task.created_at.astimezone(ZoneInfo("Asia/Shanghai"))
    date_part = task_created_at.strftime("%Y%m%d")
    assert date_part != timezone.now().astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%d"
    )
    filename = selected.storage_key.rsplit("/", 1)[-1]
    expected_prefix = (
        f"{task_created_at.strftime('%Y%m%d_%H%M%S')}-{task.pk}-"
        f"{task.user_id}-{task.model_version.algorithm_key}-ppg-"
    )
    assert selected.storage_key.split("/")[:2] == [task.channel.label, date_part]
    assert filename.startswith(expected_prefix)
    uuid_part, extension = filename[len(expected_prefix) :].rsplit(".", 1)
    assert extension == "csv"
    assert len(uuid_part) == 32
    assert all(character in "0123456789abcdef" for character in uuid_part)
    assert (tmp_path / "tasks" / selected.storage_key).read_bytes() == (
        b"timestampFrame,avgR\n2,3\n"
    )
