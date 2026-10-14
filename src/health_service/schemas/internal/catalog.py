"""定义模型目录用例返回的已求值内部数据。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CatalogModelData(BaseModel):
    """保存模型目录的公开身份字段。"""

    model_config = ConfigDict(frozen=True)

    model_id: int = Field(description="模型种类主键")
    code: str = Field(description="稳定且唯一的模型编码")
    name: str = Field(description="模型显示名称")
    description: str | None = Field(description="模型说明；未填写时为 null")


class ModelVersionData(BaseModel):
    """保存不依赖 ORM 延迟关系的版本配置。"""

    model_config = ConfigDict(frozen=True)

    version_id: int = Field(description="模型版本主键")
    model_id: int = Field(description="所属模型主键")
    model_code: str = Field(description="所属模型稳定编码")
    model_name: str = Field(description="所属模型显示名称")
    version: str = Field(description="模型版本号")
    algorithm_key: str = Field(description="受控算法标识")
    params: dict[str, object] | None = Field(description="版本固定参数")
    published_at: datetime | None = Field(description="发布时间；未发布时为 null")


class ModelArtifactData(BaseModel):
    """保存已登记制品的逻辑名、存储键和大小。"""

    model_config = ConfigDict(frozen=True)

    artifact_id: int = Field(description="制品记录主键")
    logical_name: str = Field(description="算法引用的制品逻辑名")
    storage_key: str = Field(description="相对于制品根目录的存储键")
    size_bytes: int = Field(description="制品实际大小，单位字节")


class MetricDefinitionData(BaseModel):
    """保存一个稳定指标代码的类型和单位语义。"""

    model_config = ConfigDict(frozen=True)

    metric_id: int = Field(description="指标定义主键")
    code: str = Field(description="全局稳定的指标代码")
    description: str | None = Field(description="业务含义说明")
    value_type: str = Field(description="值类型：number 或 boolean")
    unit: str | None = Field(description="数值单位；未设置时为 null")


class AvailableModelData(BaseModel):
    """保存普通渠道可以使用的模型和固定版本信息。"""

    model_config = ConfigDict(frozen=True)

    model_id: int = Field(description="模型种类主键")
    model_code: str = Field(description="模型稳定编码")
    model_name: str = Field(description="模型显示名称")
    version_id: int = Field(description="当前已发布模型版本主键")
    version: str = Field(description="当前已发布版本号")
    required_inputs: tuple[str, ...] = Field(description="算法声明的必需输入逻辑名")
    is_example: bool = Field(description="是否为非临床示例算法")


class AvailableModelPage(BaseModel):
    """封装稳定排序的普通渠道模型分页结果。"""

    model_config = ConfigDict(frozen=True)

    items: tuple[AvailableModelData, ...] = Field(description="当前页模型")
    total: int = Field(description="当前渠道可用模型总数")
    offset: int = Field(description="本页起始偏移")
    limit: int = Field(description="本页条数上限")
