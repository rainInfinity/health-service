"""验证异常历史 JSON、成员失败与质量缺失都在真实 SQL 中正确处理。"""

from copy import deepcopy
from datetime import timedelta

import pytest
from health_service.domain.measurements import MEASUREMENT_DEFINITIONS, METRICS
from health_service.infrastructure.persistence.measurements import (
    latest_user_measurement, list_user_measurements,
)

pytestmark = pytest.mark.django_db


def _query(factory, metric):
    """执行当前账号及来源渠道的完整有效性查询。"""
    return list_user_measurements(
        user_id=factory.user.pk, channel_label=factory.channel.label, metric=metric,
    )


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("bad_value", [None, True, False, "79.5", {}, [], {"value": 79.5}])
def test_json_numeric_types_are_checked_before_cast_and_count(
    measurement_tasks, scene_payload, metric, bad_value,
):
    """数字字符串、布尔、null 和错误结构不触发转换异常，也不进入计数。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    member = definition.sources[1].member
    good = scene_payload(bp=metric == "bloodPressure")["results"][member]["result"]
    task = measurement_tasks(definition.algorithm_key, good)
    bad = deepcopy(good)
    bad[definition.readings[0].key] = bad_value
    measurement_tasks(definition.algorithm_key, bad)
    total, records = _query(measurement_tasks, metric)
    assert total == len(records) == 1
    assert records[0].task_id == task.pk


@pytest.mark.parametrize("payload", [[], [79.0], "hr", 79, True])
def test_wrong_top_level_payload_shape_is_excluded(measurement_tasks, payload):
    """错误顶层结构不会被 JSON 路径当作合法正式读数。"""
    measurement_tasks(payload=payload)
    assert _query(measurement_tasks, "heartRate") == (0, ())


@pytest.mark.parametrize("missing", ["sbp", "dbp"])
@pytest.mark.parametrize("scene", [False, True])
def test_blood_pressure_requires_both_same_payload_values(
    measurement_tasks, scene_payload, missing, scene,
):
    """独立与场景血压都在 count 前要求同次两项，不能跨任务拼接。"""
    full = scene_payload(bp=True)
    result = full["results"]["bp"]["result"]
    del result[missing]
    algorithm = "ppg_acc.active.offline.all.v1" if scene else "ppg_acc.active.offline.bp.v1"
    measurement_tasks(algorithm, full if scene else result)
    measurement_tasks("ppg_acc.active.offline.bp.v1", {missing: 120})
    assert _query(measurement_tasks, "bloodPressure") == (0, ())


@pytest.mark.parametrize(
    "patch",
    [
        {"prob": None}, {"prob": True}, {"prob": "0.5"}, {"prob": -0.001}, {"prob": 1.001},
        {"risk_level": None}, {"risk_level": True}, {"risk_level": "0"},
        {"risk_level": -1}, {"risk_level": 3}, {"risk_level": 1.5}, {"risk_level": 1.0},
    ],
)
@pytest.mark.parametrize("scene", [False, True])
def test_probability_and_integer_risk_code_are_required_before_pagination(
    measurement_tasks, scene_payload, patch, scene,
):
    """风险只接受概率区间和原整数 0/1/2，不把布尔、浮点或字符串当档位。"""
    full = scene_payload()
    result = full["results"]["diabetes"]["result"]
    result.update(patch)
    algorithm = "ppg.active.offline.all.v1" if scene else "ppg.active.offline.diabetes.v1"
    measurement_tasks(algorithm, full if scene else result)
    assert _query(measurement_tasks, "diabetesRisk") == (0, ())


@pytest.mark.parametrize("missing", ["prob", "risk_level"])
def test_risk_missing_necessary_field_is_not_counted(measurement_tasks, missing):
    """概率和档位任一缺失都不生成风险记录。"""
    payload = {"prob": 0.5, "risk_level": 0}
    del payload[missing]
    measurement_tasks("ppg.active.offline.diabetes.v1", payload)
    assert _query(measurement_tasks, "diabetesRisk") == (0, ())


@pytest.mark.parametrize("probability", [0, 1, 0.9501234567890123])
@pytest.mark.parametrize("risk_level", [0, 1, 2])
def test_risk_preserves_probability_edges_and_never_reclassifies(
    measurement_tasks, probability, risk_level,
):
    """查询只保留原概率与合法编码，不调用现行模型规则重新分级。"""
    task = measurement_tasks(
        "ppg.active.offline.diabetes.v1", {"prob": probability, "risk_level": risk_level},
    )
    total, records = _query(measurement_tasks, "diabetesRisk")
    assert total == 1
    assert records[0].task_id == task.pk
    assert records[0].readings[0].value == probability
    assert records[0].risk_level == risk_level


@pytest.mark.parametrize(
    "metric,algorithm,payload",
    [
        ("respirationRate", "ppg.active.offline.hr_br.v1", {"br": 20.0001}),
        ("oxygenSaturation", "ppg.active.offline.spo2.v1", {"spo2": 89.999}),
        ("oxygenSaturation", "ppg.active.offline.spo2.v1", {"spo2": 100.001}),
    ],
)
def test_existing_algorithm_numeric_ranges_are_not_silently_clamped(
    measurement_tasks, metric, algorithm, payload,
):
    """超出原算法读数范围的历史值被排除，而不是改写为边界值。"""
    task = measurement_tasks(algorithm, payload)
    assert _query(measurement_tasks, metric) == (0, ())
    task.refresh_from_db()
    assert task.result_payload == payload


@pytest.mark.parametrize(
    "br,expected",
    [(7.999, None), (8.0, 8.0), (18.0001, 18.0001), (19.5, 19.5), (20.0, 20.0), (20.0001, None)],
)
@pytest.mark.parametrize("scene", [False, True])
def test_respiration_range_applies_to_history_and_latest(
    measurement_tasks, scene_payload, br, expected, scene,
):
    """独立及场景读数仅在 [8, 20] 内进入历史和最新结果，不改写原值。"""
    full = scene_payload()
    result = full["results"]["hr_br"]["result"]
    result["br"] = br
    algorithm = "ppg.active.offline.all.v1" if scene else "ppg.active.offline.hr_br.v1"
    payload = full if scene else result
    task = measurement_tasks(algorithm, payload)
    total, records = _query(measurement_tasks, "respirationRate")
    latest = latest_user_measurement(
        user_id=task.user_id, channel_label=task.channel.label, metric="respirationRate",
    )
    if expected is None:
        assert (total, records) == (0, ())
        assert latest is None
    else:
        assert total == len(records) == 1
        assert records[0].task_id == task.pk
        assert records[0].readings[0].value == expected
        assert latest == records[0]
    task.refresh_from_db()
    assert task.result_payload == payload


@pytest.mark.parametrize(
    "quality",
    [
        None, {}, [], "quality", {"score": 88.5}, {"threshold": 70},
        {"score": "88.5", "threshold": 70}, {"score": True, "threshold": 70},
        {"score": 69.99, "threshold": 70}, {"score": 88.5, "threshold": "70"},
        {"score": 88.5, "threshold": True}, {"score": 88.5, "threshold": 71},
        {"score": 88.5, "threshold": 70.0}, {"score": {}, "threshold": 70},
    ],
)
@pytest.mark.parametrize("scene", [False, True])
def test_invalid_actual_quality_returns_null_without_losing_valid_readings(
    measurement_tasks, scene_payload, quality, scene,
):
    """质量非法只使白名单为空，不制造默认值，也不使合法页面缩短。"""
    full = scene_payload()
    full["results"]["hr_br"]["result"]["quality"] = quality
    algorithm = "ppg.active.offline.all.v1" if scene else "ppg.active.offline.hr_br.v1"
    task = measurement_tasks(algorithm, full if scene else full["results"]["hr_br"]["result"])
    for metric in ("heartRate", "respirationRate"):
        total, records = _query(measurement_tasks, metric)
        assert total == len(records) == 1
        assert records[0].task_id == task.pk
        assert records[0].quality is None


@pytest.mark.parametrize("score", [70, 70.0, 88.65866718458382, 120.125])
def test_actual_quality_whitelist_preserves_score_without_artificial_upper_bound(
    measurement_tasks, score,
):
    """原合同只规定实际分数不低于 70，不裁剪高分或公开额外质量键。"""
    task = measurement_tasks(payload={
        "hr": 79.0, "quality": {"score": score, "threshold": 70, "diagnostic": "PRIVATE"},
    })
    total, records = _query(measurement_tasks, "heartRate")
    assert total == 1
    assert records[0].quality.model_dump() == {"score": score, "threshold": 70}
    assert "PRIVATE" not in repr(records[0].model_dump())
    task.refresh_from_db()
    assert task.result_payload["quality"]["diagnostic"] == "PRIVATE"


def test_partial_scene_only_returns_successful_members_and_latest_falls_back(
    measurement_tasks, scene_payload,
):
    """新场景部分成功仅更新成功指标，其余指标独立回退旧有效任务。"""
    old = measurement_tasks("ppg.active.offline.all.v1", scene_payload())
    mixed = scene_payload()
    mixed["outcome"] = "PARTIALLY_SUCCEEDED"
    for member in ("spo2", "diabetes"):
        mixed["results"][member].update(
            status="FAILED", result=None, failureCode="INFERENCE.QUALITY_REJECTED",
            failureMessage="信号质量不足", qualityFailure=None,
        )
    newer = measurement_tasks(
        "ppg.active.offline.all.v1", mixed,
        submitted_at=measurement_tasks.submitted_at + timedelta(seconds=1),
    )
    for metric, count, latest_task in (
        ("heartRate", 2, newer), ("respirationRate", 2, newer),
        ("oxygenSaturation", 1, old), ("diabetesRisk", 1, old),
    ):
        total, records = _query(measurement_tasks, metric)
        assert total == count
        assert records[0].task_id == latest_task.pk
        record = latest_user_measurement(
            user_id=old.user_id, channel_label=old.channel.label, metric=metric,
        )
        assert record.task_id == latest_task.pk


@pytest.mark.parametrize("bp", [False, True])
@pytest.mark.parametrize("keep_success_member", [False, True])
def test_all_failed_scene_never_yields_records_even_with_contradictory_member(
    measurement_tasks, scene_payload, bp, keep_success_member,
):
    """显式排除 ALL_FAILED，不仅依赖成员状态，原任务汇总保持可读取。"""
    full = scene_payload(bp=bp)
    full["outcome"] = "ALL_FAILED"
    if not keep_success_member:
        for member in full["results"].values():
            member.update(status="FAILED", result=None)
    algorithm = "ppg_acc.active.offline.all.v1" if bp else "ppg.active.offline.all.v1"
    task = measurement_tasks(algorithm, full)
    for metric in METRICS:
        assert _query(measurement_tasks, metric) == (0, ())
    task.refresh_from_db()
    assert task.result_payload == full


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("corruption", ["identity", "status", "missing", "shape", "outcome"])
def test_scene_member_identity_status_and_structure_are_required(
    measurement_tasks, scene_payload, metric, corruption,
):
    """固定成员位置、身份及成功状态共同决定准入，伪装读数不能被计数。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    full = scene_payload(bp=metric == "bloodPressure")
    member = definition.sources[1].member
    if corruption == "identity":
        full["results"][member]["algorithmKey"] = "test.example.fake"
    elif corruption == "status":
        full["results"][member]["status"] = "FAILED"
    elif corruption == "missing":
        del full["results"][member]
    elif corruption == "shape":
        full["results"][member]["result"] = []
    else:
        full["outcome"] = "UNKNOWN"
    measurement_tasks(definition.sources[1].task_algorithm_key, full)
    assert _query(measurement_tasks, metric) == (0, ())


@pytest.mark.parametrize("status", ["DRAFT", "SUBMITTED", "RUNNING", "FAILED"])
def test_non_successful_task_never_yields_records(measurement_tasks, status):
    """顶层质量拒绝、技术失败和未完成任务都不参与有效记录。"""
    measurement_tasks(payload=None, status=status)
    for metric in METRICS:
        assert _query(measurement_tasks, metric) == (0, ())


@pytest.mark.parametrize("bp", [False, True])
def test_missing_submitted_at_excludes_successful_history(measurement_tasks, scene_payload, bp):
    """无提交时间不回退完成或创建时间，也不推导采集属性。"""
    algorithm = "ppg_acc.active.offline.all.v1" if bp else "ppg.active.offline.all.v1"
    measurement_tasks(algorithm, scene_payload(bp=bp), submitted_at=None)
    for metric in METRICS:
        assert _query(measurement_tasks, metric) == (0, ())


@pytest.mark.parametrize(
    "algorithm", ["example.completed.v1", "test.integration.completed", "removed.unknown.v1"],
)
def test_unknown_and_example_algorithms_do_not_spoof_supported_payloads(
    measurement_tasks, scene_payload, algorithm,
):
    """不查询未知注册项，也不能只凭相似读数或模型名称判断正式来源。"""
    payload = {"hr": 79, "br": 17, "spo2": 96, "sbp": 120, "dbp": 75, "prob": 0.5, "risk_level": 0}
    measurement_tasks(algorithm, payload)
    measurement_tasks(algorithm, scene_payload())
    for metric in METRICS:
        assert _query(measurement_tasks, metric) == (0, ())
