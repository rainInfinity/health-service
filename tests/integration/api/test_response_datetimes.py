"""通过真实业务路由验证北京时间展示及原始数据边界。"""

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import ModelVersion, Profile, Task
from health_service.schemas.responses.tasks import TaskInputFileResponse
from tests.integration.workflows.test_inference_consumer import _PPG_CSV, _create_example_task


@pytest.mark.django_db(transaction=True)
@override_settings(ALLOWED_HOSTS=["testserver"])
def test_business_routes_show_beijing_time_without_rewriting_payloads(tmp_path):
    """分页、详情、上传和版本统一转换，纯日期、任意结果及数据库精度不变。"""
    stamp = datetime(2026, 10, 10, 18, 19, 19, 999999, tzinfo=UTC)
    expected = "2026-10-11 02:19:19"
    raw_iso = "2026-10-10T18:19:19.999999Z"
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks", MODEL_ARTIFACT_ROOT=tmp_path / "models"
    ):
        task = _create_example_task()
        Task.objects.filter(pk=task.pk).update(
            created_at=stamp,
            submitted_at=stamp,
            started_at=None,
            finished_at=stamp,
            status=Task.StatusChoices.SUCCEEDED,
            result_payload={"original": raw_iso},
        )
        ModelVersion.objects.filter(pk=task.model_version_id).update(published_at=stamp)
        user = task.user
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        Profile.objects.create(
            user=user, birth_date=date(1990, 1, 2), history_json={"original": raw_iso}
        )
        client = Client(
            HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user, 'ios-cn').access_token}"
        )
        page = client.get("/api/v1/ios-cn/tasks")
        assert page.status_code == 200
        summary = page.json()["data"]["items"][0]
        assert summary["createdAt"] == summary["submittedAt"] == expected
        assert summary["startedAt"] is None
        assert summary["finishedAt"] == expected
        detail = client.get(f"/api/v1/ios-cn/tasks/{task.pk}")
        assert detail.status_code == 200
        assert detail.json()["data"]["result"] == {"original": raw_iso}
        profile = client.get("/api/v1/ios-cn/users/me/profile")
        assert profile.status_code == 200
        assert profile.json()["data"]["birthDate"] == "1990-01-02"
        assert profile.json()["data"]["historyJson"] == {"original": raw_iso}
        created = client.post(
            "/api/v1/ios-cn/tasks",
            {"modelId": task.model_version.model_id},
            content_type="application/json",
        )
        assert created.status_code == 201
        draft_id = created.json()["data"]["taskId"]
        with patch(
            "health_service.infrastructure.persistence.tasks.timezone.now", return_value=stamp
        ):
            uploaded = client.post(
                f"/api/v1/ios-cn/tasks/{draft_id}/files",
                {
                    "logicalName": "ppg",
                    "file": SimpleUploadedFile("ppg.csv", _PPG_CSV.encode()),
                },
            )
        assert uploaded.status_code == 201
        assert uploaded.json()["data"]["uploadedAt"] == expected
        manager = Client(
            HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user, 'manager').access_token}"
        )
        versions = manager.get(f"/api/v1/manager/models/{task.model_version.model_id}/versions")
        assert versions.status_code == 200
        assert versions.json()["data"]["items"][0]["publishedAt"] == expected
        task.refresh_from_db()
        assert task.created_at == stamp
        assert task.created_at.microsecond == 999999


def test_public_time_serializer_handles_python_and_json_modes():
    """相同瞬间的不同偏移表示均省略小数秒，不改写原 datetime。"""
    for value in (
        datetime(2026, 10, 10, 11, 19, 19, 999999, tzinfo=UTC),
        datetime(2026, 10, 10, 19, 19, 19, 999999, tzinfo=timezone(timedelta(hours=8))),
    ):
        response = TaskInputFileResponse(
            file_id=1, logical_name="ppg", size_bytes=8, uploaded_at=value, is_selected=True
        )
        assert response.uploaded_at is value
        assert (
            response.model_dump(mode="python", by_alias=True)["uploadedAt"] == "2026-10-10 19:19:19"
        )
        assert (
            response.model_dump(mode="json", by_alias=True)["uploadedAt"] == "2026-10-10 19:19:19"
        )


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_openapi_describes_datetime_strings_but_preserves_date_contract():
    """客户端看到的响应时间约束与实际格式一致，出生日期不变。"""
    response = Client().get("/api/v1/openapi.json")
    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    for name, field in (
        ("TaskSummaryResponse", "createdAt"),
        ("TaskSummaryResponse", "submittedAt"),
        ("TaskInputFileResponse", "uploadedAt"),
        ("ModelVersionResponse", "publishedAt"),
    ):
        declaration = schemas[name]["properties"][field]
        string = next(
            (branch for branch in declaration.get("anyOf", []) if branch.get("type") == "string"),
            declaration,
        )
        assert string["type"] == "string"
        assert "format" not in string
        assert string["pattern"] == r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$"
        assert string["examples"] == ["2026-10-10 19:19:19"]
        assert "Asia/Shanghai" in string["description"]
    birth = schemas["MyProfileResponse"]["properties"]["birthDate"]
    assert any(branch.get("format") == "date" for branch in birth["anyOf"])
