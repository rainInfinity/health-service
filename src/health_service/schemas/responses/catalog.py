"""定义管理目录与普通渠道可见的模型响应。"""

from ninja import Schema
from pydantic import ConfigDict, Field

from health_service.schemas.internal.catalog import (
    AvailableModelPage,
    CatalogModelData,
    MetricDefinitionData,
    ModelArtifactData,
    ModelVersionData,
)
from health_service.schemas.responses.types import BeijingDateTime


class CatalogModelResponse(Schema):
    """公开模型身份，不包含数据库对象。"""

    model_config = ConfigDict(populate_by_name=True)

    model_id: int = Field(alias="modelId", description="模型主键")
    code: str = Field(description="稳定模型编码")
    name: str = Field(description="模型名称")
    description: str | None = Field(description="模型说明")


class ModelVersionResponse(Schema):
    """管理端读取版本配置及发布时间。"""

    model_config = ConfigDict(populate_by_name=True)

    version_id: int = Field(alias="versionId", description="版本主键")
    model_id: int = Field(alias="modelId", description="所属模型主键")
    model_code: str = Field(alias="modelCode", description="所属模型编码")
    model_name: str = Field(alias="modelName", description="所属模型名称")
    version: str = Field(description="版本号")
    algorithm_key: str = Field(alias="algorithmKey", description="受控算法标识")
    params: dict[str, object] | None = Field(description="固定算法参数")
    published_at: BeijingDateTime | None = Field(
        alias="publishedAt",
        description="首次发布时间（Asia/Shanghai）；未发布时为 null",
    )


class ModelVersionPageResponse(Schema):
    """封装稳定分页的模型版本结果。"""

    model_config = ConfigDict(populate_by_name=True)

    items: tuple[ModelVersionResponse, ...] = Field(description="当前页版本")
    total: int = Field(description="版本总数")
    offset: int = Field(description="本页起始偏移")
    limit: int = Field(description="本页条数上限")


class ModelArtifactResponse(Schema):
    """返回制品逻辑身份和大小，不暴露存储路径。"""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: int = Field(alias="artifactId", description="制品记录主键")
    logical_name: str = Field(alias="logicalName", description="算法制品逻辑名")
    size_bytes: int = Field(alias="sizeBytes", description="制品大小，单位字节")


class MetricDefinitionResponse(Schema):
    """返回稳定指标代码及其值类型和单位语义。"""

    model_config = ConfigDict(populate_by_name=True)

    metric_id: int = Field(alias="metricId", description="指标定义主键")
    code: str = Field(description="稳定指标代码")
    description: str | None = Field(description="指标业务含义")
    value_type: str = Field(alias="valueType", description="指标值类型")
    unit: str | None = Field(description="数值单位；未设置时为 null")


class AvailableModelResponse(Schema):
    """仅向普通渠道公开当前版本、输入逻辑名及示例标志。"""

    model_config = ConfigDict(populate_by_name=True)

    model_id: int = Field(alias="modelId", description="模型主键")
    model_code: str = Field(alias="modelCode", description="模型编码")
    model_name: str = Field(alias="modelName", description="模型名称")
    version_id: int = Field(alias="versionId", description="当前已发布版本主键")
    version: str = Field(description="当前已发布版本号")
    required_inputs: tuple[str, ...] = Field(
        alias="requiredInputs",
        description="算法要求上传的输入逻辑名",
    )
    is_example: bool = Field(
        alias="isExample",
        description="是否为非临床示例算法",
    )


class AvailableModelPageResponse(Schema):
    """封装当前普通渠道模型目录的有界分页结果。"""

    model_config = ConfigDict(populate_by_name=True)

    items: tuple[AvailableModelResponse, ...] = Field(description="当前页可用模型")
    total: int = Field(description="当前渠道模型总数")
    offset: int = Field(description="本页起始偏移")
    limit: int = Field(description="本页条数上限")


def to_catalog_model_response(model: CatalogModelData) -> CatalogModelResponse:
    """转换模型创建结果为公开响应。"""
    return CatalogModelResponse(
        model_id=model.model_id,
        code=model.code,
        name=model.name,
        description=model.description,
    )


def to_model_version_response(version: ModelVersionData) -> ModelVersionResponse:
    """转换版本内部投影为管理端响应。"""
    return ModelVersionResponse(
        version_id=version.version_id,
        model_id=version.model_id,
        model_code=version.model_code,
        model_name=version.model_name,
        version=version.version,
        algorithm_key=version.algorithm_key,
        params=version.params,
        published_at=version.published_at,
    )


def to_model_version_page_response(
    total: int,
    versions: tuple[ModelVersionData, ...],
    *,
    offset: int,
    limit: int,
) -> ModelVersionPageResponse:
    """转换模型版本分页并保留固定排序。"""
    return ModelVersionPageResponse(
        items=tuple(to_model_version_response(version) for version in versions),
        total=total,
        offset=offset,
        limit=limit,
    )


def to_model_artifact_response(artifact: ModelArtifactData) -> ModelArtifactResponse:
    """转换制品结果且省略内部存储键。"""
    return ModelArtifactResponse(
        artifact_id=artifact.artifact_id,
        logical_name=artifact.logical_name,
        size_bytes=artifact.size_bytes,
    )


def to_metric_definition_response(
    metric: MetricDefinitionData,
) -> MetricDefinitionResponse:
    """转换全局指标定义为管理端响应。"""
    return MetricDefinitionResponse(
        metric_id=metric.metric_id,
        code=metric.code,
        description=metric.description,
        value_type=metric.value_type,
        unit=metric.unit,
    )


def to_available_model_page_response(
    page: AvailableModelPage,
) -> AvailableModelPageResponse:
    """转换普通渠道目录分页且只保留允许公开字段。"""
    return AvailableModelPageResponse(
        items=tuple(
            AvailableModelResponse(
                model_id=item.model_id,
                model_code=item.model_code,
                model_name=item.model_name,
                version_id=item.version_id,
                version=item.version,
                required_inputs=item.required_inputs,
                is_example=item.is_example,
            )
            for item in page.items
        ),
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )
