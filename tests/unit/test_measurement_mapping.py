"""验证固定来源、记录身份、原值及质量白名单的组装。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from health_service.domain.measurements import MEASUREMENT_DEFINITIONS, METRICS
from health_service.infrastructure.persistence.measurements import _record_data
from health_service.schemas.internal.measurements import MeasurementReadingData


@pytest.mark.parametrize(
    "metric,algorithm,scene,member,readings",
    [
        ("bloodPressure", "ppg_acc.active.offline.bp.v1", "ppg_acc.active.offline.all.v1", "bp",
         (("sbp", "sbp", "mmHg"), ("dbp", "dbp", "mmHg"))),
        ("heartRate", "ppg.active.offline.hr.v1", "ppg.active.offline.all.v2", "hr",
         (("hr", "hr", "beats_per_minute"),)),
        ("respirationRate", "ppg.active.offline.br.v1", "ppg.active.offline.all.v2", "br",
         (("br", "br", "breaths_per_minute"),)),
        ("oxygenSaturation", "ppg.active.offline.spo2.v1", "ppg.active.offline.all.v2", "spo2",
         (("spo2", "spo2", "percent"),)),
        ("diabetesRisk", "ppg.active.offline.diabetes.v1", "ppg.active.offline.all.v2", "diabetes",
         (("prob", "diabetes_probability", "ratio"),)),
    ],
)
def test_fixed_mapping_binds_only_supported_source_and_original_units(
    metric, algorithm, scene, member, readings,
):
    """每类指标只识别原独立算法与固定场景成员，不动态发现算法。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    independent, scene_source = definition.sources
    assert definition.algorithm_key == algorithm
    assert independent.task_algorithm_key == algorithm
    assert independent.result_path == ()
    assert independent.member is None
    assert scene_source.task_algorithm_key == scene
    assert scene_source.member == member
    assert scene_source.result_path == ("results", member, "result")
    assert tuple((item.key, item.code, item.unit) for item in definition.readings) == readings
    assert definition.risk_key == ("risk_level" if metric == "diabetesRisk" else None)


def _row():
    """提供数据库白名单标量，不传递完整结果或 ORM 对象。"""
    return {
        "id": 91,
        "submitted_at": datetime(2026, 11, 20, 23, 59, 59, 123456, tzinfo=UTC),
        "capture_method": "fingertip_chest",
        "capture_mode": "active",
        "duration_ms": 31001,
        "model_version__model_id": 41,
        "model_version_id": 81,
        "model_version__version": "old-scene-1",
        "_m_public_score": 88.65866718458382,
        "_m_public_threshold": 70,
        "_m_value_sbp": 125.85039300154746,
        "_m_value_dbp": 76.03917873786334,
        "_m_value_hr": 79.12345678901234,
        "_m_value_br": 17.12345678901234,
        "_m_value_spo2": 96.12345678901234,
        "_m_value_prob": 0.9501234567890123,
    }


@pytest.mark.parametrize("metric", METRICS)
def test_record_keeps_identity_version_submitted_precision_and_original_values(metric):
    """场景版本归属不变，实际来源为成员，组装不舍入、不补造采集属性。"""
    row = _row()
    definition = MEASUREMENT_DEFINITIONS[metric]
    if metric == "diabetesRisk":
        # 即使原编码与概率不符合当前分级规则，读取也不能重算历史编码。
        row["_m_public_risk"] = 0
    record = _record_data(row, definition)
    assert record.record_id == f"91:{metric}"
    assert record.task_id == 91
    assert record.submitted_at is row["submitted_at"]
    assert record.model_id == 41
    assert record.version_id == 81
    assert record.version == "old-scene-1"
    assert record.algorithm_key == definition.algorithm_key
    assert record.capture_method == "fingertip_chest"
    assert record.capture_mode == "active"
    assert record.duration_ms == 31001
    assert tuple(reading.value for reading in record.readings) == tuple(
        row[f"_m_value_{reading.key}"] for reading in definition.readings
    )
    assert record.risk_level == (0 if metric == "diabetesRisk" else None)


def test_independent_rates_keep_own_quality_and_exclude_private_payload_fields():
    """同源 PPG 的两项质量独立装配，只复制各自 score/threshold。"""
    row = {**_row(), "private_path": "PRIVATE", "quality_extra": "PRIVATE"}
    hr = _record_data(row, MEASUREMENT_DEFINITIONS["heartRate"])
    br = _record_data({**row, "_m_public_score": 91.25}, MEASUREMENT_DEFINITIONS["respirationRate"])
    assert br.quality.model_dump() == {"score": 91.25, "threshold": 70}
    assert hr.quality.model_dump() == {"score": 88.65866718458382, "threshold": 70}
    assert "PRIVATE" not in repr(hr.model_dump())
    assert "PRIVATE" not in repr(br.model_dump())


def test_old_record_keeps_unknown_capture_and_missing_quality():
    """缺失属性与无合法质量都保持 null，不因指标算法推断属性。"""
    row = {
        **_row(), "capture_method": None, "capture_mode": None, "duration_ms": None,
        "_m_public_score": None, "_m_public_threshold": None,
    }
    record = _record_data(row, MEASUREMENT_DEFINITIONS["bloodPressure"])
    assert record.capture_method is record.capture_mode is record.duration_ms is None
    assert record.quality is None
    assert len(record.readings) == 2


@pytest.mark.parametrize("value", [True, False, "79", None])
def test_internal_readings_do_not_coerce_non_numeric_values(value):
    """内部白名单拒绝将布尔和数字字符串转换成读数。"""
    with pytest.raises(ValidationError):
        MeasurementReadingData(code="hr", value=value, unit="beats_per_minute")
