"""验证真实 PostgreSQL 下的固定映射、隔离、分页与只读标量查询。"""

from copy import deepcopy
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from health_service.domain.measurements import MEASUREMENT_DEFINITIONS, METRICS
from health_service.infrastructure.persistence.measurements import (
    latest_user_measurement, list_user_measurements,
)
from health_service.models import Channel, MetricDefinition, ModelVersion, Task, TaskMetric

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("scene", [False, True])
def test_all_supported_sources_keep_original_values_and_frozen_version(
    measurement_tasks, scene_payload, metric, scene,
):
    """四个独立算法和两个场景都按固定合同返回原值及实际成员来源。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    source = definition.sources[1 if scene else 0]
    full = scene_payload(bp=metric == "bloodPressure")
    member = definition.sources[1].member
    expected = full["results"][member]["result"]
    payload = full if scene else deepcopy(expected)
    payload["output_files"] = {"private": "PRIVATE-PATH"}
    task = measurement_tasks(
        source.task_algorithm_key, payload,
        capture_method="fingertip_chest", capture_mode="active", duration_ms=31001,
    )
    total, records = list_user_measurements(
        user_id=task.user_id, channel_label=task.channel.label, metric=metric,
    )
    assert total == 1
    assert len(records) == 1
    record = records[0]
    assert record == latest_user_measurement(
        user_id=task.user_id, channel_label=task.channel.label, metric=metric,
    )
    assert record.record_id == f"{task.pk}:{metric}"
    assert record.algorithm_key == definition.algorithm_key
    assert record.version_id == task.model_version_id
    assert record.version == task.model_version.version
    assert record.model_id == task.model_version.model_id
    assert record.submitted_at == task.submitted_at
    assert record.capture_method == "fingertip_chest"
    assert record.capture_mode == "active"
    assert record.duration_ms == 31001
    assert tuple((item.code, item.value, item.unit) for item in record.readings) == tuple(
        (reading.code, expected[reading.key], reading.unit) for reading in definition.readings
    )
    assert record.quality.model_dump() == expected["quality"]
    assert record.risk_level == (expected["risk_level"] if metric == "diabetesRisk" else None)
    assert "PRIVATE" not in repr(record.model_dump())


def test_hr_br_share_quality_and_old_unknown_capture_without_projection(
    measurement_tasks, scene_payload,
):
    """旧结果没有新增属性或投影也完整可读，HR/BR 使用同一次质量。"""
    payload = scene_payload()["results"]["hr_br"]["result"]
    task = measurement_tasks(payload=payload)
    before = deepcopy(task.result_payload)
    hr = latest_user_measurement(
        user_id=task.user_id, channel_label=task.channel.label, metric="heartRate",
    )
    br = latest_user_measurement(
        user_id=task.user_id, channel_label=task.channel.label, metric="respirationRate",
    )
    assert hr.task_id == br.task_id == task.pk
    assert hr.record_id != br.record_id
    assert hr.quality == br.quality
    assert hr.capture_method is hr.capture_mode is hr.duration_ms is None
    assert br.readings[0].value == payload["br"]
    task.refresh_from_db()
    assert task.result_payload == before
    assert not TaskMetric.objects.filter(task=task).exists()


def test_partial_metric_projection_is_not_a_query_gate_and_is_not_backfilled(measurement_tasks):
    """只投影 HR 的历史版本仍返回 BR，不补写可选投影或原结果。"""
    task = measurement_tasks(payload={"hr": 79.0, "br": 17.0})
    metric = MetricDefinition.objects.create(
        code="hr", value_type=MetricDefinition.ValueTypeChoices.NUMBER,
    )
    projection = TaskMetric.objects.create(task=task, metric=metric, numeric_value=79.0)
    before = list(TaskMetric.objects.filter(task=task).values())
    for selected in ("heartRate", "respirationRate"):
        total, records = list_user_measurements(
            user_id=task.user_id, channel_label=task.channel.label, metric=selected,
        )
        assert total == len(records) == 1
    assert list(TaskMetric.objects.filter(task=task).values()) == before
    assert TaskMetric.objects.get(pk=projection.pk).numeric_value == 79.0
    task.refresh_from_db()
    assert task.result_payload == {"hr": 79.0, "br": 17.0}


def test_unprojected_blood_pressure_is_one_paired_record(measurement_tasks):
    """血压双读数不产生 join 重复，也不依赖任意一项投影。"""
    task = measurement_tasks("ppg_acc.active.offline.bp.v1", {"sbp": 125.125, "dbp": 76.625})
    total, records = list_user_measurements(
        user_id=task.user_id, channel_label=task.channel.label, metric="bloodPressure",
    )
    assert total == len(records) == 1
    assert tuple(item.value for item in records[0].readings) == (125.125, 76.625)
    assert not TaskMetric.objects.filter(task=task).exists()


def test_count_and_full_pages_ignore_invalid_tasks_before_offset(measurement_tasks):
    """失败、缺项与非法读数穿插时 total 正确、每页不因事后过滤而缺口。"""
    valid = []
    for index in range(5):
        submitted = measurement_tasks.submitted_at + timedelta(seconds=index)
        valid.append(measurement_tasks(
            "ppg_acc.active.offline.bp.v1", {"sbp": 120 + index, "dbp": 75},
            submitted_at=submitted,
        ))
        measurement_tasks("ppg_acc.active.offline.bp.v1", {"sbp": 120}, submitted_at=submitted)
        measurement_tasks(
            "ppg_acc.active.offline.bp.v1", {"sbp": "120", "dbp": 75}, submitted_at=submitted,
        )
        measurement_tasks(
            "ppg_acc.active.offline.bp.v1", None, submitted_at=submitted, status="FAILED",
        )
    expected = [task.pk for task in reversed(valid)]
    found = []
    for offset, size in ((0, 2), (2, 2), (4, 1), (6, 0)):
        total, records = list_user_measurements(
            user_id=measurement_tasks.user.pk, channel_label=measurement_tasks.channel.label,
            metric="bloodPressure", offset=offset, limit=2,
        )
        assert total == 5
        assert len(records) == size
        found.extend(item.task_id for item in records)
    assert found == expected


def test_latest_uses_submitted_time_then_id_not_finish_time(measurement_tasks):
    """完成顺序反转、同提交时刻以及新失败均不覆盖正确最近记录。"""
    early = measurement_tasks(payload={"hr": 71}, submitted_at=measurement_tasks.submitted_at)
    later_time = measurement_tasks.submitted_at + timedelta(seconds=1, microseconds=1)
    later = measurement_tasks(payload={"hr": 72}, submitted_at=later_time)
    same_time = measurement_tasks(payload={"hr": 73}, submitted_at=later_time)
    Task.objects.filter(pk=early.pk).update(finished_at=later_time + timedelta(days=1))
    measurement_tasks(payload=None, submitted_at=later_time + timedelta(seconds=1), status="FAILED")
    measurement_tasks(payload=None, submitted_at=later_time + timedelta(seconds=2), status="RUNNING")
    latest = latest_user_measurement(
        user_id=early.user_id, channel_label=early.channel.label, metric="heartRate",
    )
    assert latest.task_id == same_time.pk
    total, records = list_user_measurements(
        user_id=early.user_id, channel_label=early.channel.label, metric="heartRate",
    )
    assert total == 3
    assert [item.task_id for item in records] == [same_time.pk, later.pk, early.pk]


def test_query_time_bounds_are_half_open_and_account_channel_scoped(measurement_tasks):
    """起点包含、终点排除，同时不泄露其他账号或来源渠道的记录与计数。"""
    start = measurement_tasks.submitted_at
    end = start + timedelta(days=1)
    measurement_tasks(payload={"hr": 60}, submitted_at=start - timedelta(microseconds=1))
    first = measurement_tasks(payload={"hr": 61}, submitted_at=start)
    last = measurement_tasks(payload={"hr": 62}, submitted_at=end - timedelta(microseconds=1))
    measurement_tasks(payload={"hr": 63}, submitted_at=end)
    other_user = get_user_model().objects.create_user(username="measurement-other")
    other_channel = Channel.objects.create(label="measurement-other")
    measurement_tasks(payload={"hr": 64}, owner=other_user)
    measurement_tasks(payload={"hr": 65}, source_channel=other_channel)
    total, records = list_user_measurements(
        user_id=first.user_id, channel_label=first.channel.label, metric="heartRate",
        from_time=start, to_time=end,
    )
    assert total == 2
    assert [item.task_id for item in records] == [last.pk, first.pk]


def test_historical_fixed_version_does_not_require_current_selection(measurement_tasks):
    """历史只依赖任务固定版本，不要求发布目录或当前渠道选择仍指向旧版本。"""
    old = measurement_tasks(payload={"hr": 79.125})
    ModelVersion.objects.create(
        model=measurement_tasks.model, version="new-current", algorithm_key="unsupported.new.v2",
    )
    record = latest_user_measurement(
        user_id=old.user_id, channel_label=old.channel.label, metric="heartRate",
    )
    assert record.version_id == old.model_version_id
    assert record.version == old.model_version.version
    assert record.readings[0].value == 79.125


def test_queries_select_only_public_scalars_and_latest_does_not_count(measurement_tasks):
    """页查询只用两条只读 SQL，latest 只用一条，不加载完整结果与私有字段。"""
    task = measurement_tasks(payload={
        "hr": 79.0, "br": 17.0, "waveform": [1.0] * 1000,
        "quality": {"score": 88.5, "threshold": 70, "private": "PRIVATE"},
        "output_files": {"ppg": "PRIVATE-PATH"},
    })
    arguments = {"user_id": task.user_id, "channel_label": task.channel.label, "metric": "heartRate"}
    with CaptureQueriesContext(connection) as page_queries:
        total, records = list_user_measurements(**arguments)
        assert records[0].readings[0].value == 79.0
        records[0].model_dump()
    assert total == 1
    assert len(page_queries) == 2
    assert "COUNT(" in page_queries[0]["sql"]
    with CaptureQueriesContext(connection) as latest_queries:
        record = latest_user_measurement(**arguments)
        record.model_dump()
    assert len(latest_queries) == 1
    assert "COUNT(" not in latest_queries[0]["sql"]
    assert "LIMIT 1" in latest_queries[0]["sql"]
    for query in [*page_queries, *latest_queries]:
        sql = query["sql"]
        assert sql.startswith("SELECT ")
        assert '"health_taskmetric"' not in sql
        assert '"profile_snapshot"' not in sql
        assert '"client_metadata"' not in sql
        assert '"failure_detail"' not in sql
        # 结果仅在 JSON 路径表达式内出现，不能作为整列独立返回。
        assert '"health_task"."result_payload",' not in sql
        assert '"health_task"."result_payload" AS ' not in sql
    assert "PRIVATE" not in repr(record.model_dump())


def test_empty_results_and_out_of_range_offset_keep_correct_count(measurement_tasks):
    """没有有效结果时 latest 为空，越界页保留全范围 total。"""
    args = {"user_id": measurement_tasks.user.pk, "channel_label": measurement_tasks.channel.label}
    for metric in METRICS:
        assert latest_user_measurement(**args, metric=metric) is None
        assert list_user_measurements(**args, metric=metric) == (0, ())
    measurement_tasks(payload={"hr": 79})
    assert list_user_measurements(**args, metric="heartRate", offset=99) == (1, ())
