"""定义已求值、无 ORM 依赖的健康记录内部类型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from health_service.domain.measurements import Metric


class MeasurementReadingData(BaseModel):
    """保存未经舍入或单位转换的权威读数。"""

    model_config = ConfigDict(frozen=True, strict=True)

    code: str = Field(description="稳定读数编码")
    value: int | float = Field(description="权威结果原始数值")
    unit: str = Field(description="原算法单位")


class MeasurementQualityData(BaseModel):
    """只保留对应成功结果的实际质量白名单。"""

    model_config = ConfigDict(frozen=True, strict=True)

    score: int | float = Field(description="实际质量分数")
    threshold: int | float = Field(description="该次计算实际质量阈值")


class MeasurementRecordData(BaseModel):
    """保存按任务及指标唯一标识的健康记录。"""

    model_config = ConfigDict(frozen=True, strict=True)

    record_id: str = Field(description="稳定身份，格式为 taskId:metric")
    task_id: int = Field(description="产生该记录的任务主键")
    metric: Metric = Field(description="健康指标类别")
    submitted_at: datetime = Field(description="原提交时间，作为产品检测时间")
    capture_method: str | None = Field(description="冻结采集方式；历史缺失时为 null")
    capture_mode: str | None = Field(description="冻结采集来源；历史缺失时为 null")
    duration_ms: int | None = Field(description="冻结实际时长，单位毫秒；历史缺失时为 null")
    readings: tuple[MeasurementReadingData, ...] = Field(description="该指标的完整成组原值")
    quality: MeasurementQualityData | None = Field(description="合法实际质量；缺失或非法时为 null")
    risk_level: int | None = Field(description="原风险编码 0/1/2；非风险记录为 null")
    model_id: int = Field(description="任务固定模型主键，场景记录保留场景模型")
    version_id: int = Field(description="任务固定版本主键")
    version: str = Field(description="任务固定版本号")
    algorithm_key: str = Field(description="实际成功算法或场景成员算法标识")
