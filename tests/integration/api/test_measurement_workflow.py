"""通过正式路由及真实消费者验证采集属性到健康记录的完整链路。"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from health_service.application.catalog import select_channel_model_version
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Task

from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.integration.workflows.test_scene_support import (
    CHANNEL,
    artifact_path,
    create_scene_version,
    run_scene_consumer,
)
from tests.integration.workflows.test_scene_support import scene_environment as scene_environment


@pytest.mark.django_db(transaction=True)
@override_settings(ALLOWED_HOSTS=["testserver"])
def test_real_submit_execute_latest_history_and_partial_failure_fallback(scene_environment):
    """真实推理后保留提交时间和质量，成员失败仅回退对应旧值。"""
    env = scene_environment
    model, version = create_scene_version(env, selected_metrics=("hr",))
    select_channel_model_version(channel=CHANNEL, model_id=model.model_id, version_id=version.version_id)
    client = Client(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(env.owner, CHANNEL).access_token}")
    measurement = {"captureMethod": "fingertip", "captureMode": "active", "durationMs": 30001}

    def submit_via_routes():
        """实际创建草稿、上传可执行波形并携带采集属性提交。"""
        draft = client.post(f"/api/v1/{CHANNEL}/tasks", {"modelId": model.model_id}, content_type="application/json")
        assert draft.status_code == 201
        task_id = draft.json()["data"]["taskId"]
        uploaded = client.post(f"/api/v1/{CHANNEL}/tasks/{task_id}/files", {
            "logicalName": "ppg", "file": SimpleUploadedFile("ppg.csv", synthetic_ppg_csv(heart_rate_hz=1.8).encode()),
        })
        assert uploaded.status_code == 201
        submitted = client.post(f"/api/v1/{CHANNEL}/tasks/{task_id}/submit", {
            "measurement": measurement, "clientMetadata": {"deviceModel": "private-workflow-device"},
        }, content_type="application/json")
        assert submitted.status_code == 200
        assert "measurement" not in submitted.json()["data"]
        return task_id, submitted.json()["data"]["submittedAt"]

    first_id, first_submitted_at = submit_via_routes()
    run_scene_consumer()
    first_detail = client.get(f"/api/v1/{CHANNEL}/tasks/{first_id}")
    assert first_detail.status_code == 200
    first = first_detail.json()["data"]
    assert first["status"] == "SUCCEEDED"
    assert first["result"]["outcome"] == "ALL_SUCCEEDED"
    assert first["measurement"] == measurement
    assert first["submittedAt"] == first_submitted_at
    latest = client.get(f"/api/v1/{CHANNEL}/measurements/latest").json()["data"]
    assert latest["bloodPressure"] is None
    for metric, member, key in (("heartRate", "hr_br", "hr"), ("respirationRate", "hr_br", "br"), ("oxygenSaturation", "spo2", "spo2"), ("diabetesRisk", "diabetes", "prob")):
        result = first["result"]["results"][member]
        record = latest[metric]
        assert record["taskId"] == first_id
        assert record["submittedAt"] == first_submitted_at
        assert record["algorithmKey"] == result["algorithmKey"]
        assert record["versionId"] == version.version_id
        assert record["quality"] == result["result"]["quality"]
        assert record["readings"][0]["value"] == result["result"][key]
        assert (record["captureMethod"], record["captureMode"], record["durationMs"]) == ("fingertip", "active", 30001)
        history = client.get(f"/api/v1/{CHANNEL}/measurements", {
            "metric": metric, "from": first_submitted_at[:10], "to": first_submitted_at[:10],
        })
        assert history.status_code == 200
        assert history.json()["data"]["items"] == [record]

    artifact_path(env, version, "diabetes").write_bytes(b"invalid-model-for-member-failure")
    second_id, second_submitted_at = submit_via_routes()
    run_scene_consumer()
    second = client.get(f"/api/v1/{CHANNEL}/tasks/{second_id}").json()["data"]
    assert second["status"] == "SUCCEEDED"
    assert second["result"]["outcome"] == "PARTIALLY_SUCCEEDED"
    assert second["result"]["results"]["diabetes"]["status"] == "FAILED"
    after = client.get(f"/api/v1/{CHANNEL}/measurements/latest").json()["data"]
    assert after["diabetesRisk"] == latest["diabetesRisk"]
    for metric in ("heartRate", "respirationRate", "oxygenSaturation"):
        assert after[metric]["taskId"] == second_id
        assert after[metric]["submittedAt"] == second_submitted_at
    assert after["bloodPressure"] is None
    assert client.get(f"/api/v1/{CHANNEL}/tasks/{first_id}").json()["data"]["result"] == first["result"]
    original = Task.objects.get(pk=first_id)
    assert original.submitted_at is not None
    assert original.capture_method == "fingertip"
