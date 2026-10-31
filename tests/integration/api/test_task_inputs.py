"""验证普通渠道输入上传通过 HTTP 完整登记且不泄露存储键。"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import AsyncClient, override_settings
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.models import TaskInputFile

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


@pytest.mark.django_db(transaction=True)
def test_task_input_upload_http_contract(tmp_path):
    """HTTP 上传按 ppg 逻辑名登记完整文件并拒绝未声明输入。"""
    username = "task-input-http-owner"
    password = "Canyon-silver-295!strong"
    get_user_model().objects.create_user(username=username, password=password)
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
        undeclared = await client.post(
            f"/api/v1/ios-cn/tasks/{task_id}/files",
            data={
                "logicalName": "other",
                "file": SimpleUploadedFile("other.csv", b"not registered"),
            },
            headers=headers,
        )
        await _close_worker_database_connection()
        return created, uploaded, undeclared, task_id

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        created, uploaded, undeclared, task_id = asyncio.run(exercise_api())

    assert created.status_code == 201
    assert uploaded.status_code == 201
    upload_data = uploaded.json()["data"]
    assert upload_data["logicalName"] == "ppg"
    assert upload_data["sizeBytes"] > 0
    assert upload_data["isSelected"] is True
    assert not {"storageKey", "storage_key", "path"}.intersection(upload_data)
    assert undeclared.status_code == 422
    input_file = TaskInputFile.objects.get(task_id=task_id)
    assert input_file.logical_name == "ppg"
    assert input_file.is_selected is True
    assert (tmp_path / "tasks" / input_file.storage_key).read_bytes() == (
        b"timestampFrame,avgR\n1,2\n"
    )
