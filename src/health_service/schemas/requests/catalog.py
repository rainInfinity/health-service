"""定义模型目录管理请求的严格字段和公开别名。"""

from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field, StrictInt, model_validator


class CreateModelRequest(Schema):
    """接收模型稳定编码、名称和可选说明。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str = Field(min_length=1, max_length=100, description="全局唯一模型编码")
    name: str = Field(min_length=1, max_length=255, description="模型显示名称")
    description: str | None = Field(default=None, description="模型说明")


class CreateModelVersionRequest(Schema):
    """接收新版本固定的算法标识和参数。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str = Field(min_length=1, max_length=64, description="模型版本号")
    algorithm_key: str = Field(
        alias="algorithmKey",
        min_length=1,
        max_length=255,
        description="受控算法注册标识",
    )
    params: dict[str, object] | None = Field(default=None, description="固定算法参数")


class PatchModelVersionRequest(Schema):
    """保留缺席与 null 区别地修改未发布版本配置。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    algorithm_key: str | None = Field(
        default=None,
        alias="algorithmKey",
        min_length=1,
        max_length=255,
        description="受控算法注册标识；缺席时保持不变",
    )
    params: dict[str, object] | None = Field(
        default=None,
        description="固定算法参数；null 表示清空",
    )

    @model_validator(mode="after")
    def reject_null_algorithm_key(self) -> "PatchModelVersionRequest":
        """只允许缺席 algorithmKey，不允许显式清空算法标识。"""
        if "algorithm_key" in self.model_fields_set and self.algorithm_key is None:
            raise ValueError("algorithmKey 不能为 null")
        return self


class CreateMetricDefinitionRequest(Schema):
    """接收全局稳定指标代码、值类型及单位语义。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str = Field(min_length=1, max_length=128, description="全局唯一指标代码")
    description: str | None = Field(default=None, description="指标业务含义")
    value_type: Literal["number", "boolean"] = Field(
        alias="valueType",
        description="值类型：number 或 boolean",
    )
    unit: str | None = Field(default=None, max_length=32, description="指标单位")

    @model_validator(mode="after")
    def reject_boolean_unit(self) -> "CreateMetricDefinitionRequest":
        """布尔指标不接受数值单位。"""
        if self.value_type == "boolean" and self.unit is not None:
            raise ValueError("布尔指标不能设置数值单位")
        return self


class SetModelVersionMetricsRequest(Schema):
    """接收版本完整的指标代码集合，空列表表示清空。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metric_codes: list[str] = Field(
        alias="metricCodes",
        description="完整投影指标代码集合",
    )


class SelectModelVersionRequest(Schema):
    """接收渠道当前版本主键。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version_id: StrictInt = Field(alias="versionId", gt=0, description="已发布版本主键")
