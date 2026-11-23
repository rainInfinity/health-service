"""公开健康记录的读数、质量及任务固定来源白名单。"""

from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field

from health_service.domain.measurements import Metric
from health_service.schemas.internal.measurements import MeasurementRecordData
from health_service.schemas.responses.types import BeijingDateTime


class MeasurementReadingResponse(Schema):
    """保留权威读数原值及算法单位。"""

    code: str = Field(description="稳定读数代码")
    value: int | float = Field(description="权威结果原值，不重新舍入或分级")
    unit: str = Field(description="算法原始单位")


class MeasurementQualityResponse(Schema):
    """只公开本次实际质量分数及阈值。"""

    score: int | float = Field(description="本次实际质量分数")
    threshold: int | float = Field(description="本次实际质量阈值")


class MeasurementRecordResponse(Schema):
    """返回一项指标的一次有效结果，不携带完整报告或私有字段。"""

    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId", description="稳定身份：<taskId>:<metric>")
    task_id: int = Field(alias="taskId", description="关联原任务主键")
    metric: Metric = Field(description="健康指标")
    submitted_at: BeijingDateTime = Field(
        alias="submittedAt", description="产品检测时间：服务端接受提交时刻（Asia/Shanghai），非原生采集起点"
    )
    capture_method: Literal["fingertip", "fingertip_chest"] | None = Field(
        alias="captureMethod", description="自报采集方式；旧记录未知为 null"
    )
    capture_mode: Literal["active"] | None = Field(
        alias="captureMode", description="自报来源；未知为 null"
    )
    duration_ms: int | None = Field(alias="durationMs", description="实际采集时长，毫秒；未知为 null")
    readings: tuple[MeasurementReadingResponse, ...] = Field(description="同一次结果的读数；血压成对")
    quality: MeasurementQualityResponse | None = Field(description="对应结果的实际质量；缺失或不合法为 null")
    risk_level: int | None = Field(alias="riskLevel", description="原 risk_level 编码；非风险指标为 null")
    model_id: int = Field(alias="modelId", description="任务固定模型主键")
    version_id: int = Field(alias="versionId", description="任务固定版本主键；场景时为场景版本")
    version: str = Field(description="任务固定版本号")
    algorithm_key: str = Field(alias="algorithmKey", description="实际独立或成功成员算法，不是当前渠道指针")


class LatestMeasurementsResponse(Schema):
    """固定五个键，各自独立返回最新有效记录或 null。"""

    model_config = ConfigDict(populate_by_name=True)

    blood_pressure: MeasurementRecordResponse | None = Field(alias="bloodPressure", description="最近有效血压")
    heart_rate: MeasurementRecordResponse | None = Field(alias="heartRate", description="最近有效心率")
    respiration_rate: MeasurementRecordResponse | None = Field(alias="respirationRate", description="最近有效呼吸率")
    oxygen_saturation: MeasurementRecordResponse | None = Field(alias="oxygenSaturation", description="最近有效血氧")
    diabetes_risk: MeasurementRecordResponse | None = Field(alias="diabetesRisk", description="最近有效风险编码及概率")


class MeasurementPageResponse(Schema):
    """返回筛选有效集合的 offset 页，不提供跨请求固定快照。"""

    items: tuple[MeasurementRecordResponse, ...] = Field(description="提交时间、任务 ID 稳定倒序记录")
    total: int = Field(description="整个筛选范围的有效记录数，非页长度")
    offset: int = Field(description="本页起始偏移")
    limit: int = Field(description="本页条数上限")


def to_measurement_response(record: MeasurementRecordData) -> MeasurementRecordResponse:
    """逐项选择公开字段，避免扩展内部数据时泄漏私有内容。"""
    return MeasurementRecordResponse(
        record_id=record.record_id,
        task_id=record.task_id,
        metric=record.metric,
        submitted_at=record.submitted_at,
        capture_method=record.capture_method,
        capture_mode=record.capture_mode,
        duration_ms=record.duration_ms,
        readings=tuple(
            MeasurementReadingResponse(code=item.code, value=item.value, unit=item.unit)
            for item in record.readings
        ),
        quality=(
            MeasurementQualityResponse(score=record.quality.score, threshold=record.quality.threshold)
            if record.quality is not None else None
        ),
        risk_level=record.risk_level,
        model_id=record.model_id,
        version_id=record.version_id,
        version=record.version,
        algorithm_key=record.algorithm_key,
    )
