"""提供真实 PostgreSQL 健康查询所需的历史任务数据。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from health_service.models import Channel, Model, ModelVersion, Task

_SUBMITTED = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def measurement_tasks():
    """直接保存固定结果，覆盖历史读取而不重新执行算法或依赖文件。"""
    assert connection.vendor == "postgresql", "健康查询测试必须使用真实 PostgreSQL"
    user = get_user_model().objects.create_user(username="measurement-owner")
    channel = Channel.objects.create(label="measurement-cn")
    model = Model.objects.create(code="measurement-history", name="历史固定模型")
    versions = {}

    def create(
        algorithm="ppg.active.offline.hr_br.v1", payload=None, *,
        submitted_at=_SUBMITTED, status=Task.StatusChoices.SUCCEEDED,
        owner=None, source_channel=None, version=None, **capture,
    ):
        """创建符合任务状态约束、可包含损坏历史 JSON 的数据库记录。"""
        if version is None:
            if algorithm not in versions:
                versions[algorithm] = ModelVersion.objects.create(
                    model=model, version=f"v{len(versions) + 1}", algorithm_key=algorithm,
                )
            version = versions[algorithm]
        fields = {
            "user": owner or user,
            "channel": source_channel or channel,
            "model_version": version,
            "status": status,
            "submitted_at": submitted_at,
            "result_payload": payload if status == Task.StatusChoices.SUCCEEDED else None,
            **capture,
        }
        if status in (Task.StatusChoices.SUCCEEDED, Task.StatusChoices.FAILED):
            fields["finished_at"] = _SUBMITTED + timedelta(minutes=1)
        if status == Task.StatusChoices.FAILED:
            fields["failure_code"] = "INFERENCE.INPUT_INVALID"
            fields["failure_message"] = "输入无效"
        if status == Task.StatusChoices.RUNNING:
            fields["execution_token"] = uuid4()
            fields["lease_expires_at"] = _SUBMITTED + timedelta(minutes=1)
        return Task.objects.create(**fields)

    create.user = user
    create.channel = channel
    create.model = model
    create.submitted_at = _SUBMITTED
    return create


@pytest.fixture
def scene_payload():
    """构造具有完整成员身份与状态字段的固定场景结果。"""
    def build(*, bp=False):
        """为两类固定场景保留各自实际成功成员及原始结果。"""
        definitions = (
            {"bp": ("ppg_acc.active.offline.bp.v1", {"sbp": 125.85039300154746, "dbp": 76.03917873786334})}
            if bp else {
                "hr_br": ("ppg.active.offline.hr_br.v1", {"hr": 79.12345678901234, "br": 17.12345678901234}),
                "spo2": ("ppg.active.offline.spo2.v1", {"spo2": 96.12345678901234}),
                "diabetes": ("ppg.active.offline.diabetes.v1", {"prob": 0.5523077249526978, "risk_level": 0}),
            }
        )
        return {
            "outcome": "ALL_SUCCEEDED",
            "results": {
                name: {
                    "algorithmKey": algorithm,
                    "status": "SUCCEEDED",
                    "result": {**result, "quality": {"score": 88.65866718458382, "threshold": 70}},
                    "failureCode": None,
                    "failureMessage": None,
                    "qualityFailure": None,
                }
                for name, (algorithm, result) in definitions.items()
            },
        }
    return build
