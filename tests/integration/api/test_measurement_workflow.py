"""通过正式路由及真实消费者验证采集属性到健康记录的完整链路。"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from health_service.application.catalog import select_channel_model_version
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Task, TaskMetric

from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.integration.workflows.test_scene_support import (
    CHANNEL,
    artifact_path,
    create_scene_version,
    run_scene_consumer,
    submit_scene_task,
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
    for metric, member, key in (("heartRate", "hr", "hr"), ("respirationRate", "br", "br"), ("oxygenSaturation", "spo2", "spo2"), ("diabetesRisk", "diabetes", "prob")):
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


@pytest.mark.django_db(transaction=True)
@override_settings(ALLOWED_HOSTS=["testserver"])
def test_real_short_ppg_keeps_hr_when_br_is_not_computable(scene_environment):
    """确定性低频呼吸波真实触发 BR 非有限，HR 保存、下载和查询均不受牵连。"""
    env = scene_environment
    model, version = create_scene_version(env)
    prior = submit_scene_task(
        env, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8),
    )
    run_scene_consumer()
    prior.refresh_from_db()
    assert prior.result_payload["results"]["br"]["status"] == "SUCCEEDED"
    task = submit_scene_task(
        env, model, version,
        ppg_content=synthetic_ppg_csv(
            heart_rate_hz=1.2, duration_seconds=30.0, respiration_rate_hz=0.1,
        ),
    )
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == "PARTIALLY_SUCCEEDED"
    members = task.result_payload["results"]
    hr = members["hr"]
    br = members["br"]
    assert hr["status"] == "SUCCEEDED"
    assert hr["result"]["hr"] == pytest.approx(72.0, abs=1.0)
    assert set(hr["result"]) == {"hr", "quality"}
    assert br["status"] == "FAILED"
    assert br["failureCode"] == "RESULT.INVALID_OUTPUT"
    assert br["result"] is None
    projections = {
        item.metric.code: item.numeric_value
        for item in TaskMetric.objects.filter(task=task).select_related("metric")
    }
    assert projections["hr"] == hr["result"]["hr"]
    assert "br" not in projections
    files = task.result_payload["output_files"]
    assert "hr_processed_ppg" in files
    assert "br_processed_rsp" not in files
    assert (env.storage.task_data_root / files["hr_processed_ppg"]).is_file()
    execution_dir = (env.storage.task_data_root / files["hr_processed_ppg"]).parent.parent
    assert not (execution_dir / "br").exists()

    client = Client(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(env.owner, CHANNEL).access_token}")
    detail = client.get(f"/api/v1/{CHANNEL}/tasks/{task.pk}")
    assert detail.status_code == 200
    assert detail.json()["data"]["result"]["results"] == members
    downloaded = client.get(f"/api/v1/{CHANNEL}/tasks/{task.pk}/outputs/hr_processed_ppg/download")
    assert downloaded.status_code == 200
    assert b"".join(downloaded.streaming_content) == (
        env.storage.task_data_root / files["hr_processed_ppg"]
    ).read_bytes()
    denied = client.get(f"/api/v1/{CHANNEL}/tasks/{task.pk}/outputs/br_processed_rsp/download")
    assert denied.status_code == 404

    latest = client.get(f"/api/v1/{CHANNEL}/measurements/latest").json()["data"]
    assert latest["heartRate"]["taskId"] == task.pk
    assert latest["heartRate"]["algorithmKey"] == "ppg.active.offline.hr.v1"
    assert latest["heartRate"]["readings"][0]["value"] == hr["result"]["hr"]
    assert latest["heartRate"]["quality"] == hr["result"]["quality"]
    assert latest["respirationRate"]["taskId"] == prior.pk
    assert latest["respirationRate"]["algorithmKey"] == "ppg.active.offline.br.v1"
    day = latest["heartRate"]["submittedAt"][:10]
    for metric, expected_ids in (
        ("heartRate", [task.pk, prior.pk]), ("respirationRate", [prior.pk]),
    ):
        history = client.get(f"/api/v1/{CHANNEL}/measurements", {
            "metric": metric, "from": day, "to": day,
        })
        assert history.status_code == 200
        page = history.json()["data"]
        assert page["total"] == len(expected_ids)
        assert [item["taskId"] for item in page["items"]] == expected_ids
