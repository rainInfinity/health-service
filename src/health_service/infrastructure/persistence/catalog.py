"""实现模型目录相关的具体 ORM 查询、锁定和写入。"""

from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from health_service.models import (
    Channel,
    ChannelModelSelection,
    MetricDefinition,
    Model,
    ModelArtifact,
    ModelVersion,
    ModelVersionMetric,
    Task,
)
from health_service.schemas.internal.catalog import (
    CatalogModelData,
    MetricDefinitionData,
    ModelArtifactData,
    ModelVersionData,
)

from .exceptions import (
    ChannelModelSelectionConflictError,
    MetricCodeConflictError,
    ModelArtifactConflictError,
    ModelCodeConflictError,
    ModelRecordNotFoundError,
    ModelVersionConflictError,
    ModelVersionRecordNotFoundError,
)


def create_model(*, code: str, name: str, description: str | None) -> CatalogModelData:
    """创建模型并返回不依赖 ORM 的已求值结果。"""
    try:
        with transaction.atomic():
            model = Model.objects.create(code=code, name=name, description=description)
    except IntegrityError as error:
        if _constraint_name(error) == "health_model_code_key":
            raise ModelCodeConflictError from error
        raise
    return _model_data(model)


def get_model(*, model_id: int) -> CatalogModelData | None:
    """按主键读取模型公开目录字段。"""
    model = Model.objects.filter(pk=model_id).first()
    return None if model is None else _model_data(model)


def create_model_version(
    *,
    model_id: int,
    version: str,
    algorithm_key: str,
    params: dict[str, object] | None,
) -> ModelVersionData:
    """为已有模型创建未发布版本并一次性求值所属模型字段。"""
    try:
        with transaction.atomic():
            try:
                model = (
                    Model.objects.select_for_update()
                    .only("id", "code", "name")
                    .get(pk=model_id)
                )
            except Model.DoesNotExist as error:
                raise ModelRecordNotFoundError from error
            model_version = ModelVersion.objects.create(
                model=model,
                version=version,
                algorithm_key=algorithm_key,
                params=params,
            )
    except IntegrityError as error:
        if _constraint_name(error) == "health_model_version_unique":
            raise ModelVersionConflictError from error
        raise
    return _model_version_data(model_version)


def get_model_version(*, version_id: int) -> ModelVersionData | None:
    """读取版本和所属模型字段，不返回延迟 ORM 关系。"""
    model_version = (
        ModelVersion.objects.select_related("model").filter(pk=version_id).first()
    )
    return None if model_version is None else _model_version_data(model_version)


def list_model_versions(
    *,
    model_id: int,
    offset: int,
    limit: int,
) -> tuple[int, tuple[ModelVersionData, ...]]:
    """按主键稳定分页读取模型版本并返回总数。"""
    versions = ModelVersion.objects.filter(model_id=model_id)
    total = versions.count()
    page = versions.select_related("model").order_by("id")[offset : offset + limit]
    return total, tuple(_model_version_data(item) for item in page)


def lock_model_version(*, version_id: int) -> ModelVersionData:
    """在调用方事务内锁定版本行并返回已求值配置。"""
    try:
        version = (
            ModelVersion.objects.select_for_update(of=("self",))
            .select_related("model")
            .get(pk=version_id)
        )
    except ModelVersion.DoesNotExist as error:
        raise ModelVersionRecordNotFoundError from error
    return _model_version_data(version)


def save_model_version(
    *,
    version_id: int,
    changes: dict[str, object],
) -> ModelVersionData | None:
    """更新调用方已锁定且尚未发布的版本配置。"""
    updated = ModelVersion.objects.filter(
        pk=version_id,
        published_at__isnull=True,
    ).update(**changes, updated_at=timezone.now())
    return get_model_version(version_id=version_id) if updated else None


def list_model_artifacts(*, version_id: int) -> tuple[ModelArtifactData, ...]:
    """按逻辑名读取版本制品及相对存储键。"""
    artifacts = ModelArtifact.objects.filter(model_version_id=version_id).order_by(
        "logical_name",
        "id",
    )
    return tuple(_artifact_data(item) for item in artifacts)


def create_model_artifact(
    *,
    version_id: int,
    logical_name: str,
    storage_key: str,
    size_bytes: int,
) -> ModelArtifactData:
    """为已锁定的未发布版本登记完整制品。"""
    try:
        with transaction.atomic():
            artifact = ModelArtifact.objects.create(
                model_version_id=version_id,
                logical_name=logical_name,
                storage_key=storage_key,
                size_bytes=size_bytes,
            )
    except IntegrityError as error:
        if _constraint_name(error) in {
            "health_artifact_name_unique",
            "health_model_artifact_storage_key_key",
        }:
            raise ModelArtifactConflictError from error
        raise
    return _artifact_data(artifact)


def create_metric_definition(
    *,
    code: str,
    description: str | None,
    value_type: str,
    unit: str | None,
) -> MetricDefinitionData:
    """创建全局稳定指标定义并返回已求值结果。"""
    try:
        with transaction.atomic():
            metric = MetricDefinition.objects.create(
                code=code,
                description=description,
                value_type=value_type,
                unit=unit,
            )
    except IntegrityError as error:
        if _constraint_name(error) == "health_metric_definition_code_key":
            raise MetricCodeConflictError from error
        raise
    return _metric_data(metric)


def get_metric_definitions(*, metric_codes: tuple[str, ...]) -> tuple[MetricDefinitionData, ...]:
    """按稳定代码读取投影指标定义。"""
    metrics = MetricDefinition.objects.filter(code__in=metric_codes).order_by("id")
    return tuple(_metric_data(item) for item in metrics)


def replace_model_version_metrics(
    *,
    version_id: int,
    metric_ids: tuple[int, ...],
) -> tuple[MetricDefinitionData, ...]:
    """替换持有版本行锁的投影集合并返回已求值定义。"""
    ModelVersionMetric.objects.filter(model_version_id=version_id).delete()
    metrics = tuple(MetricDefinition.objects.filter(pk__in=metric_ids).order_by("id"))
    for metric in metrics:
        ModelVersionMetric.objects.create(
            model_version_id=version_id,
            metric_id=metric.pk,
        )
    return tuple(_metric_data(metric) for metric in metrics)


def list_model_version_metrics(*, version_id: int) -> tuple[MetricDefinitionData, ...]:
    """读取版本已选择的指标定义，避免逐行延迟查询。"""
    metrics = MetricDefinition.objects.filter(
        model_versions__model_version_id=version_id
    ).order_by("id")
    return tuple(_metric_data(item) for item in metrics)


def publish_model_version(
    *,
    version_id: int,
    published_at: datetime,
) -> ModelVersionData | None:
    """为调用方已锁定且尚未发布的版本写入首次发布时间。"""
    updated = ModelVersion.objects.filter(
        pk=version_id,
        published_at__isnull=True,
    ).update(published_at=published_at, updated_at=published_at)
    return get_model_version(version_id=version_id) if updated else None


def create_or_lock_channel(*, label: str) -> int:
    """确保已知渠道存在并锁定渠道行作为并发串行点。"""
    channel, _ = Channel.objects.get_or_create(label=label)
    Channel.objects.select_for_update().only("id").get(pk=channel.pk)
    return channel.pk


def lock_model_selection(
    *,
    channel_id: int,
    model_id: int,
) -> tuple[int, int] | None:
    """按渠道与模型锁定现有选择并返回选择 ID 和当前版本 ID。"""
    selection = (
        ChannelModelSelection.objects.select_for_update(of=("self",))
        .filter(channel_id=channel_id, model_id=model_id)
        .values_list("id", "current_version_id")
        .first()
    )
    return selection


def create_model_selection(
    *,
    channel_id: int,
    model_id: int,
    version_id: int,
) -> None:
    """创建首次渠道模型选择。"""
    try:
        with transaction.atomic():
            ChannelModelSelection.objects.create(
                channel_id=channel_id,
                model_id=model_id,
                current_version_id=version_id,
            )
    except IntegrityError as error:
        if _constraint_name(error) == "health_channel_model_unique":
            raise ChannelModelSelectionConflictError from error
        raise


def update_model_selection(*, selection_id: int, version_id: int) -> None:
    """更新已锁定选择的当前版本。"""
    ChannelModelSelection.objects.filter(pk=selection_id).update(
        current_version_id=version_id,
        updated_at=timezone.now(),
    )


def lock_draft_task_ids(*, channel_id: int, version_id: int) -> tuple[int, ...]:
    """按主键稳定锁定旧版本的全部渠道草稿。"""
    task_ids = Task.objects.select_for_update().filter(
        channel_id=channel_id,
        model_version_id=version_id,
        status=Task.StatusChoices.DRAFT,
    ).order_by("pk").values_list("pk", flat=True)
    return tuple(task_ids)


def fail_model_switched_drafts(*, task_ids: tuple[int, ...], finished_at: datetime) -> None:
    """将已锁定的旧版本草稿安全终止为模型切换失败。"""
    if not task_ids:
        return
    Task.objects.filter(
        pk__in=task_ids,
        status=Task.StatusChoices.DRAFT,
    ).update(
        status=Task.StatusChoices.FAILED,
        finished_at=finished_at,
        failure_code="TASK.MODEL_SWITCHED",
        failure_message="任务使用的模型版本已切换",
        failure_detail=None,
        execution_token=None,
        lease_expires_at=None,
        updated_at=finished_at,
    )


def list_selected_versions(
    *,
    channel_label: str,
    offset: int,
    limit: int,
) -> tuple[int, tuple[ModelVersionData, ...]]:
    """稳定分页读取渠道当前已发布版本及所属模型字段。"""
    selections = ChannelModelSelection.objects.filter(
        channel__label=channel_label,
        current_version__published_at__isnull=False,
    )
    total = selections.count()
    page = (
        selections.select_related("model", "current_version")
        .order_by("model_id")[offset : offset + limit]
    )
    versions = tuple(
        _model_version_data(item.current_version, model=item.model) for item in page
    )
    return total, versions


def _constraint_name(error: IntegrityError) -> str | None:
    """读取 PostgreSQL 约束名以区分可预期冲突与其他数据库故障。"""
    cause = error.__cause__
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _model_data(model: Model) -> CatalogModelData:
    """将模型 ORM 实例投影为已求值内部结果。"""
    return CatalogModelData(
        model_id=model.pk,
        code=model.code,
        name=model.name,
        description=model.description,
    )


def _model_version_data(
    version: ModelVersion,
    *,
    model: Model | None = None,
) -> ModelVersionData:
    """将已加载模型版本与所属模型投影为内部结果。"""
    owner = model if model is not None else version.model
    return ModelVersionData(
        version_id=version.pk,
        model_id=version.model_id,
        model_code=owner.code,
        model_name=owner.name,
        version=version.version,
        algorithm_key=version.algorithm_key,
        params=version.params,
        published_at=version.published_at,
    )


def _artifact_data(artifact: ModelArtifact) -> ModelArtifactData:
    """将制品 ORM 实例投影为不含绝对路径的内部结果。"""
    return ModelArtifactData(
        artifact_id=artifact.pk,
        logical_name=artifact.logical_name,
        storage_key=artifact.storage_key,
        size_bytes=artifact.size_bytes,
    )


def _metric_data(metric: MetricDefinition) -> MetricDefinitionData:
    """将指标 ORM 实例投影为已求值的类型与单位信息。"""
    return MetricDefinitionData(
        metric_id=metric.pk,
        code=metric.code,
        description=metric.description,
        value_type=metric.value_type,
        unit=metric.unit,
    )
