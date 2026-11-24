"""验证模型目录管理 API、渠道隔离及普通用户公开字段。"""

import asyncio
import json
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import AsyncClient, override_settings
from health_service.models import ModelArtifact


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
def test_manager_publishes_ppg_v1_and_users_see_only_their_channel():
    """HTTP 发布正式 PPG v1，普通响应不泄露内部配置。"""
    user_model = get_user_model()
    manager = user_model.objects.create_user(
        username="catalog-manager",
        password="Amber-forest-736!strong",
        is_staff=True,
        is_superuser=False,
    )
    user_model.objects.create_user(
        username="catalog-ordinary",
        password="Amber-forest-836!strong",
    )

    async def exercise_api():
        """通过 HTTP 执行管理发布与普通目录查询流程。"""
        client = AsyncClient()
        manager_login = await _login(
            client,
            "manager",
            "catalog-manager",
            "Amber-forest-736!strong",
        )
        ios_login = await _login(
            client,
            "ios-cn",
            "catalog-ordinary",
            "Amber-forest-836!strong",
        )
        android_login = await _login(
            client,
            "android-cn",
            "catalog-ordinary",
            "Amber-forest-836!strong",
        )
        manager_token = manager_login.json()["data"]["accessToken"]
        ios_token = ios_login.json()["data"]["accessToken"]
        android_token = android_login.json()["data"]["accessToken"]
        manager_headers = {"Authorization": f"Bearer {manager_token}"}

        model_response = await client.post(
            "/api/v1/manager/models",
            data=json.dumps({"code": "hr", "name": "HR v1"}),
            content_type="application/json",
            headers=manager_headers,
        )
        model_id = model_response.json()["data"]["modelId"]
        version_response = await client.post(
            f"/api/v1/manager/models/{model_id}/versions",
            data=json.dumps(
                {
                    "version": "1.0.0",
                    "algorithmKey": "ppg.active.offline.hr.v1",
                    "params": None,
                }
            ),
            content_type="application/json",
            headers=manager_headers,
        )
        version_data = version_response.json()["data"]
        metric_payloads = (
            {
                "code": "hr",
                "description": "心率",
                "valueType": "number",
                "unit": "beats_per_minute",
            },
        )
        metric_responses = []
        for payload in metric_payloads:
            response = await client.post(
                "/api/v1/manager/metrics",
                data=json.dumps(payload),
                content_type="application/json",
                headers=manager_headers,
            )
            metric_responses.append(response)
        metrics_response = await client.put(
            f"/api/v1/manager/model-versions/{version_data['versionId']}/metrics",
            data=json.dumps(
                {"metricCodes": [payload["code"] for payload in metric_payloads]}
            ),
            content_type="application/json",
            headers=manager_headers,
        )
        publish_response = await client.post(
            f"/api/v1/manager/model-versions/{version_data['versionId']}/publish",
            headers=manager_headers,
        )
        selection_response = await client.put(
            f"/api/v1/manager/channels/ios-cn/models/{model_id}/selection",
            data=json.dumps({"versionId": version_data["versionId"]}),
            content_type="application/json",
            headers=manager_headers,
        )
        ios_models = await client.get(
            "/api/v1/ios-cn/models?offset=0&limit=20",
            headers={"Authorization": f"Bearer {ios_token}"},
        )
        android_models = await client.get(
            "/api/v1/android-cn/models?offset=0&limit=20",
            headers={"Authorization": f"Bearer {android_token}"},
        )
        wrong_channel = await client.get(
            "/api/v1/android-cn/models",
            headers={"Authorization": f"Bearer {ios_token}"},
        )
        regular_manager_write = await client.post(
            "/api/v1/manager/models",
            data=json.dumps({"code": "forbidden", "name": "Forbidden"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {ios_token}"},
        )
        await sync_to_async(
            user_model.objects.filter(pk=manager.pk).update,
            thread_sensitive=True,
        )(is_staff=False)
        revoked_manager_write = await client.post(
            "/api/v1/manager/models",
            data=json.dumps({"code": "revoked", "name": "Revoked"}),
            content_type="application/json",
            headers=manager_headers,
        )
        invalid_page = await client.get(
            "/api/v1/ios-cn/models?offset=-1&limit=101",
            headers={"Authorization": f"Bearer {ios_token}"},
        )
        await _close_worker_database_connection()
        return (
            manager_login,
            ios_login,
            android_login,
            model_response,
            version_response,
            metric_responses,
            metrics_response,
            publish_response,
            selection_response,
            ios_models,
            android_models,
            wrong_channel,
            regular_manager_write,
            revoked_manager_write,
            invalid_page,
        )

    (
        manager_login,
        ios_login,
        android_login,
        model_response,
        version_response,
        metric_responses,
        metrics_response,
        publish_response,
        selection_response,
        ios_models,
        android_models,
        wrong_channel,
        regular_manager_write,
        revoked_manager_write,
        invalid_page,
    ) = asyncio.run(exercise_api())

    assert [
        manager_login.status_code,
        ios_login.status_code,
        android_login.status_code,
        model_response.status_code,
        version_response.status_code,
        metrics_response.status_code,
        publish_response.status_code,
        selection_response.status_code,
    ] == [200, 200, 200, 201, 201, 200, 200, 200]
    assert [response.status_code for response in metric_responses] == [201]
    ios_payload = ios_models.json()["data"]
    assert ios_payload["total"] == 1
    assert ios_payload["items"] == [
        {
            "modelId": model_response.json()["data"]["modelId"],
            "modelCode": "hr",
            "modelName": "HR v1",
            "versionId": version_response.json()["data"]["versionId"],
            "version": "1.0.0",
            "requiredInputs": ["ppg"],
            "isExample": False,
        }
    ]
    assert android_models.json()["data"]["items"] == []
    assert not {
        "params",
        "algorithmKey",
        "storageKey",
        "path",
    }.intersection(ios_payload["items"][0])
    assert wrong_channel.status_code == 403
    assert regular_manager_write.status_code == 403
    assert revoked_manager_write.status_code == 403
    assert invalid_page.status_code == 422


@pytest.mark.django_db(transaction=True)
def test_manager_artifact_upload_uses_logical_name_and_hides_storage_key(tmp_path):
    """multipart 上传保存完整制品，响应不暴露服务器存储键。"""
    get_user_model().objects.create_user(
        username="catalog-artifact-manager",
        password="Cedar-summit-846!strong",
        is_staff=True,
        is_superuser=False,
    )

    async def exercise_api():
        """创建未发布版本并通过 HTTP 上传制品。"""
        client = AsyncClient()
        login = await _login(
            client,
            "manager",
            "catalog-artifact-manager",
            "Cedar-summit-846!strong",
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        model_response = await client.post(
            "/api/v1/manager/models",
            data=json.dumps({"code": "artifact", "name": "Artifact"}),
            content_type="application/json",
            headers=headers,
        )
        model_id = model_response.json()["data"]["modelId"]
        version_response = await client.post(
            f"/api/v1/manager/models/{model_id}/versions",
            data=json.dumps(
                {"version": "1.0", "algorithmKey": "ppg.active.offline.spo2.v1", "params": None}
            ),
            content_type="application/json",
            headers=headers,
        )
        version_id = version_response.json()["data"]["versionId"]
        upload = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/artifacts",
            data={
                "logicalName": "spo2",
                "file": SimpleUploadedFile("weights.ONNX", b"weights-data"),
            },
            headers=headers,
        )
        duplicate = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/artifacts",
            data={
                "logicalName": "spo2",
                "file": SimpleUploadedFile("other.onnx", b"other-data"),
            },
            headers=headers,
        )
        undeclared = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/artifacts",
            data={
                "logicalName": "unknown",
                "file": SimpleUploadedFile("unknown.json", b"unknown"),
            },
            headers=headers,
        )
        unsupported = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/artifacts",
            data={
                "logicalName": "spo2",
                "file": SimpleUploadedFile("spo2.json", b"wrong suffix"),
            },
            headers=headers,
        )
        missing_suffix = await client.post(
            f"/api/v1/manager/model-versions/{version_id}/artifacts",
            data={
                "logicalName": "spo2",
                "file": SimpleUploadedFile("spo2", b"missing suffix"),
            },
            headers=headers,
        )
        await _close_worker_database_connection()
        return (
            model_response,
            version_response,
            upload,
            duplicate,
            undeclared,
            unsupported,
            missing_suffix,
        )

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=64,
    ):
        (
            model_response,
            version_response,
            upload,
            duplicate,
            undeclared,
            unsupported,
            missing_suffix,
        ) = asyncio.run(exercise_api())

    assert model_response.status_code == version_response.status_code == 201
    assert upload.status_code == 201
    assert upload.json()["data"]["logicalName"] == "spo2"
    assert upload.json()["data"]["sizeBytes"] == len(b"weights-data")
    assert not {"storageKey", "storage_key", "path"}.intersection(upload.json()["data"])
    assert duplicate.status_code == 409
    assert undeclared.status_code == 422
    assert unsupported.status_code == 422
    assert missing_suffix.status_code == 422
    assert (
        ModelArtifact.objects.filter(model_version__model__code="artifact").count()
        == 1
    )
    artifact_files = tuple(Path(tmp_path / "artifacts").rglob("*.onnx"))
    assert len(artifact_files) == 1
    assert artifact_files[0].read_bytes() == b"weights-data"
    artifact_path = artifact_files[0]
    relative_path = artifact_path.relative_to(tmp_path / "artifacts")
    assert relative_path.parts[:2] == ("artifact", "1.0")
    assert artifact_path.name.startswith("spo2-")
    assert artifact_path.name.endswith(".onnx")
    uuid_part = artifact_path.name[len("spo2-") : -len(".onnx")]
    assert len(uuid_part) == 32
    assert all(character in "0123456789abcdef" for character in uuid_part)
    assert (
        ModelArtifact.objects.get(model_version__model__code="artifact").storage_key
        == relative_path.as_posix()
    )
