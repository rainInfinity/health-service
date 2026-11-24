"""通过真实认证路由验证健康记录、北京时间与非快照分页合同。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Channel, ChannelModelSelection, Model, ModelVersion, Task

pytestmark = pytest.mark.django_db(transaction=True)

_METRICS = ("bloodPressure", "heartRate", "respirationRate", "oxygenSaturation", "diabetesRisk")
_HR = "ppg.active.offline.hr.v1"
_BR = "ppg.active.offline.br.v1"
_BP = "ppg_acc.active.offline.bp.v1"
_SPO2 = "ppg.active.offline.spo2.v1"
_DIABETES = "ppg.active.offline.diabetes.v1"
_SCENE = "ppg.active.offline.all.v2"
_STAMP = datetime(2026, 11, 21, 16, tzinfo=UTC)
_QUALITY = {"score": 88.123456789, "threshold": 70, "privatePath": "/private/quality"}


@pytest.fixture
def measurement_environment(settings):
    """创建独立账号和正式算法历史版本，不依赖当前渠道指针。"""
    settings.ALLOWED_HOSTS = ["testserver"]
    user = get_user_model().objects.create_user(username=f"measurement-{uuid4().hex}", password="Measurement-2026!")
    channel, _ = Channel.objects.get_or_create(label="android-cn")
    other_channel, _ = Channel.objects.get_or_create(label="ios-cn")
    model = Model.objects.create(code=f"measurement-{uuid4().hex}", name="历史健康记录")
    versions = {
        key: ModelVersion.objects.create(model=model, version=str(index), algorithm_key=key, published_at=_STAMP)
        for index, key in enumerate((_HR, _BR, _BP, _SPO2, _DIABETES, _SCENE), start=1)
    }
    client = Client(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user, channel.label).access_token}")
    return SimpleNamespace(user=user, channel=channel, other_channel=other_channel, model=model, versions=versions, client=client)


def _success(environment, key, payload, *, stamp=_STAMP, finished=None, **kwargs):
    """保存历史成功任务，包含不应公开的私有数据且不创建投影。"""
    return Task.objects.create(
        user=kwargs.pop("user", environment.user),
        channel=kwargs.pop("channel", environment.channel),
        model_version=environment.versions[key],
        status=Task.StatusChoices.SUCCEEDED,
        submitted_at=stamp,
        finished_at=finished or _STAMP + timedelta(hours=1),
        result_payload=payload,
        profile_snapshot={"privateProfile": "/private/profile"},
        client_metadata={"privateMetadata": "secret-device"},
        **kwargs,
    )


def _history(client, **kwargs):
    """读取包含当天的默认心率历史，允许覆盖筛选参数。"""
    return client.get("/api/v1/android-cn/measurements", {
        "metric": "heartRate", "from": "2026-11-22", "to": "2026-11-22", **kwargs,
    })


@pytest.mark.parametrize(("metric", "algorithm", "payload", "readings", "risk"), [
    ("bloodPressure", _BP, {"sbp": 123.456789, "dbp": 78.123456}, [("sbp", 123.456789, "mmHg"), ("dbp", 78.123456, "mmHg")], None),
    ("heartRate", _HR, {"hr": 77.123456}, [("hr", 77.123456, "beats_per_minute")], None),
    ("respirationRate", _BR, {"br": 16.987654}, [("br", 16.987654, "breaths_per_minute")], None),
    ("oxygenSaturation", _SPO2, {"spo2": 97.654321}, [("spo2", 97.654321, "percent")], None),
    ("diabetesRisk", _DIABETES, {"prob": 0.543210987, "risk_level": 2}, [("diabetes_probability", 0.543210987, "ratio")], 2),
])
def test_independent_results_keep_original_values_and_public_whitelist(measurement_environment, metric, algorithm, payload, readings, risk):
    """五项均保留原值和单位，风险不重新分级，质量只公开两键。"""
    env = measurement_environment
    task = _success(env, algorithm, {**payload, "quality": _QUALITY, "raw": [1, 2], "path": "/private/raw"})
    response = _history(env.client, metric=metric)
    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["total"], data["offset"], data["limit"]) == (1, 0, 20)
    record = data["items"][0]
    assert record == {
        "recordId": f"{task.pk}:{metric}", "taskId": task.pk, "metric": metric,
        "submittedAt": "2026-11-22 00:00:00", "captureMethod": None, "captureMode": None, "durationMs": None,
        "readings": [{"code": code, "value": value, "unit": unit} for code, value, unit in readings],
        "quality": {"score": _QUALITY["score"], "threshold": 70}, "riskLevel": risk,
        "modelId": env.model.pk, "versionId": env.versions[algorithm].pk,
        "version": env.versions[algorithm].version, "algorithmKey": algorithm,
    }
    assert "private" not in response.content.decode()
    assert env.client.get(f"/api/v1/android-cn/tasks/{task.pk}").json()["data"]["result"]["path"] == "/private/raw"


def test_latest_selects_submission_order_ties_and_fixed_versions(measurement_environment):
    """完成乱序、同提交时间和渠道版本切换都不改变按提交选择的规则。"""
    env = measurement_environment
    old = _success(env, _HR, {"hr": 60}, stamp=_STAMP - timedelta(seconds=1), finished=_STAMP + timedelta(days=2))
    new = _success(env, _HR, {"hr": 80}, finished=_STAMP + timedelta(minutes=1))
    tied = _success(env, _HR, {"hr": 81}, finished=_STAMP + timedelta(minutes=2))
    respiration = _success(env, _BR, {"br": 17}, finished=_STAMP + timedelta(minutes=2))
    newer_version = ModelVersion.objects.create(model=env.model, version="new-current", algorithm_key=_HR, published_at=_STAMP)
    ChannelModelSelection.objects.create(channel=env.channel, model=env.model, current_version=newer_version)
    Task.objects.create(user=env.user, channel=env.channel, model_version=newer_version, status="SUBMITTED", submitted_at=_STAMP + timedelta(days=1))
    Task.objects.create(user=env.user, channel=env.channel, model_version=newer_version, status="FAILED", submitted_at=_STAMP + timedelta(days=1), finished_at=_STAMP + timedelta(days=1), failure_code="INFERENCE.QUALITY_REJECTED")
    _success(env, _HR, {"hr": 200}, stamp=_STAMP + timedelta(days=3), channel=env.other_channel)
    other = get_user_model().objects.create_user(username=f"other-{uuid4().hex}")
    _success(env, _HR, {"hr": 210}, stamp=_STAMP + timedelta(days=3), user=other)
    response = env.client.get("/api/v1/android-cn/measurements/latest")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == set(_METRICS)
    assert data["heartRate"]["taskId"] == tied.pk
    assert data["respirationRate"]["taskId"] == respiration.pk
    assert data["respirationRate"]["versionId"] == env.versions[_BR].pk
    assert data["heartRate"]["versionId"] == env.versions[_HR].pk
    assert data["bloodPressure"] is data["oxygenSaturation"] is data["diabetesRisk"] is None
    page = _history(env.client, limit=2).json()["data"]
    assert page["total"] == 2
    assert [item["taskId"] for item in page["items"]] == [tied.pk, new.pk]
    previous_day = _history(env.client, **{"from": "2026-11-21", "to": "2026-11-21"}).json()["data"]
    assert [item["taskId"] for item in previous_day["items"]] == [old.pk]


def test_beijing_boundaries_pagination_and_concurrent_insert(measurement_environment):
    """包含首尾日且保留微秒排序；请求间新结果可改变 total 和后页。"""
    env = measurement_environment
    _success(env, _HR, {"hr": 50}, stamp=_STAMP - timedelta(microseconds=1))
    first = _success(env, _HR, {"hr": 61})
    last = _success(env, _HR, {"hr": 62}, stamp=_STAMP + timedelta(days=1, microseconds=-1))
    _success(env, _HR, {"hr": 99}, stamp=_STAMP + timedelta(days=1))
    page = _history(env.client, limit=1).json()["data"]
    assert page["total"] == 2
    assert [item["taskId"] for item in page["items"]] == [last.pk]
    second = _history(env.client, limit=1, offset=1).json()["data"]
    assert [item["taskId"] for item in second["items"]] == [first.pk]
    inserted = _success(env, _HR, {"hr": 63}, stamp=_STAMP + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999))
    changed = _history(env.client, limit=1, offset=1).json()["data"]
    assert changed["total"] == 3
    assert [item["taskId"] for item in changed["items"]] == [last.pk]
    assert _history(env.client, limit=1).json()["data"]["items"][0]["taskId"] == inserted.pk
    empty = _history(env.client, offset=100, limit=2).json()["data"]
    assert empty == {"items": [], "total": 3, "offset": 100, "limit": 2}


def test_full_calendar_range_is_not_artificially_limited(measurement_environment):
    """合法的公历年份边界不触发时间转换溢出或新增日期跨度限制。"""
    env = measurement_environment
    task = _success(env, _HR, {"hr": 75})
    response = _history(env.client, **{"from": "0001-01-01", "to": "9999-12-31"})
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["taskId"] == task.pk


def test_empty_data_authentication_and_channel_boundary(measurement_environment):
    """空数据保留五键，未认证与路径渠道不匹配沿用既有错误。"""
    env = measurement_environment
    latest = env.client.get("/api/v1/android-cn/measurements/latest")
    assert latest.json()["data"] == dict.fromkeys(_METRICS)
    assert _history(env.client).json()["data"] == {"items": [], "total": 0, "offset": 0, "limit": 20}
    for suffix in ("/latest", "?metric=heartRate&from=2026-11-22&to=2026-11-22"):
        unauthenticated = Client().get(f"/api/v1/android-cn/measurements{suffix}")
        mismatch = env.client.get(f"/api/v1/ios-cn/measurements{suffix}")
        assert (unauthenticated.status_code, unauthenticated.json()["code"]) == (401, "INVALID_TOKEN")
        assert (mismatch.status_code, mismatch.json()["code"]) == (403, "CHANNEL_FORBIDDEN")
        assert unauthenticated.json()["data"] is mismatch.json()["data"] is None


@pytest.mark.parametrize("query", [
    {}, {"metric": "heartRate"}, {"from": "2026-11-22", "to": "2026-11-22"},
    {"metric": "unknown"}, {"from": "2026-1-02"}, {"from": "2026-02-30"},
    {"from": "2026-11-22T00:00:00Z"}, {"to": "2026-11-21"},
    {"offset": -1}, {"offset": "1.0"}, {"offset": "true"},
    {"limit": 0}, {"limit": 101}, {"limit": "1.5"},
])
def test_invalid_history_parameters_return_structured_422(measurement_environment, query):
    """必填参数、严格日历日期和分页错误不被静默修正。"""
    defaults = {"metric": "heartRate", "from": "2026-11-22", "to": "2026-11-22"}
    parameters = query if query in ({}, {"metric": "heartRate"}, {"from": "2026-11-22", "to": "2026-11-22"}) else {**defaults, **query}
    response = measurement_environment.client.get("/api/v1/android-cn/measurements", parameters)
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_INPUT"
    assert response.json()["data"] is None
    assert response.json()["requestId"] == response.headers["X-Request-ID"]


def test_measurement_openapi_matches_public_fields_and_beijing_dates(measurement_environment):
    """OpenAPI 声明五键、白名单记录、必填首尾日和北京时间响应。"""
    document = measurement_environment.client.get("/api/v1/openapi.json").json()
    schemas = document["components"]["schemas"]
    assert set(schemas["LatestMeasurementsResponse"]["properties"]) == set(_METRICS)
    record = schemas["MeasurementRecordResponse"]["properties"]
    assert set(record) == {"recordId", "taskId", "metric", "submittedAt", "captureMethod", "captureMode", "durationMs", "readings", "quality", "riskLevel", "modelId", "versionId", "version", "algorithmKey"}
    assert "Asia/Shanghai" in record["submittedAt"]["description"]
    assert record["submittedAt"]["pattern"] == r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$"
    parameters = {item["name"]: item for item in document["paths"]["/api/v1/{channel}/measurements"]["get"]["parameters"]}
    assert all(parameters[name]["required"] for name in ("metric", "from", "to"))
    assert "北京时间" in parameters["from"]["schema"]["description"]
    assert parameters["limit"]["schema"]["maximum"] == 100
