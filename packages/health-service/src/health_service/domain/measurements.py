"""定义健康记录的固定指标、结果路径与原始单位。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Metric = Literal[
    "bloodPressure", "heartRate", "respirationRate", "oxygenSaturation", "diabetesRisk"
]
METRICS: tuple[Metric, ...] = (
    "bloodPressure", "heartRate", "respirationRate", "oxygenSaturation", "diabetesRisk"
)


@dataclass(frozen=True, slots=True)
class MeasurementReading:
    """描述一个公开读数及原算法已经约束的数值范围。"""

    key: str  # 权威结果中的读数键。
    code: str  # 对外稳定读数编码。
    unit: str  # 原算法单位，不在查询时转换。
    minimum: int | None = None  # 原合同下界；None 表示不增加下界。
    maximum: int | None = None  # 原合同上界；None 表示不增加上界。


@dataclass(frozen=True, slots=True)
class MeasurementSource:
    """固定独立算法或场景成员的结果位置。"""

    task_algorithm_key: str  # 任务固定版本算法标识。
    result_path: tuple[str, ...]  # 独立结果为空路径，场景指向成员 result。
    member: str | None = None  # 场景成员名；独立算法为 None。


@dataclass(frozen=True, slots=True)
class MeasurementDefinition:
    """保存同一指标共享的固定结果合同。"""

    metric: Metric  # 健康记录类别。
    algorithm_key: str  # 实际执行的独立算法或成员算法标识。
    sources: tuple[MeasurementSource, ...]  # 仅显式支持的两个来源。
    readings: tuple[MeasurementReading, ...]  # 血压的两项必须同时有效。
    risk_key: str | None = None  # 风险编码字段；非风险指标不读取。
    quality_threshold: int = 70  # 五个独立算法实际采用的固定质量阈值。


def _sources(algorithm_key: str, scene_key: str, member: str) -> tuple[MeasurementSource, ...]:
    """将独立算法与唯一合法场景成员绑定到同一结果合同。"""
    return (
        MeasurementSource(algorithm_key, ()),
        MeasurementSource(scene_key, ("results", member, "result"), member),
    )


_BP = "ppg_acc.active.offline.bp.v1"
_HR = "ppg.active.offline.hr.v1"
_BR = "ppg.active.offline.br.v1"
_SPO2 = "ppg.active.offline.spo2.v1"
_DIABETES = "ppg.active.offline.diabetes.v1"
_PPG_SCENE = "ppg.active.offline.all.v2"
_BP_SCENE = "ppg_acc.active.offline.all.v1"

MEASUREMENT_DEFINITIONS = MappingProxyType({
    "bloodPressure": MeasurementDefinition(
        "bloodPressure", _BP, _sources(_BP, _BP_SCENE, "bp"),
        (
            MeasurementReading("sbp", "sbp", "mmHg"),
            MeasurementReading("dbp", "dbp", "mmHg"),
        ),
    ),
    "heartRate": MeasurementDefinition(
        "heartRate", _HR, _sources(_HR, _PPG_SCENE, "hr"),
        (MeasurementReading("hr", "hr", "beats_per_minute"),),
    ),
    "respirationRate": MeasurementDefinition(
        "respirationRate", _BR, _sources(_BR, _PPG_SCENE, "br"),
        (MeasurementReading("br", "br", "breaths_per_minute", minimum=8, maximum=20),),
    ),
    "oxygenSaturation": MeasurementDefinition(
        "oxygenSaturation", _SPO2, _sources(_SPO2, _PPG_SCENE, "spo2"),
        (MeasurementReading("spo2", "spo2", "percent", minimum=90, maximum=100),),
    ),
    "diabetesRisk": MeasurementDefinition(
        "diabetesRisk", _DIABETES, _sources(_DIABETES, _PPG_SCENE, "diabetes"),
        (MeasurementReading("prob", "diabetes_probability", "ratio", minimum=0, maximum=1),),
        risk_key="risk_level",
    ),
})
