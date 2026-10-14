"""实现受控模型目录、发布校验和渠道版本选择用例。"""

from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from health_inference.exceptions import InferenceError, UnknownAlgorithmError
from health_inference.registry import get_algorithm
from health_inference.types import AlgorithmDefinition
from health_service.application.catalog_exceptions import (
    CatalogConfigurationInvalid,
    CatalogConflict,
    CatalogNotFound,
)
from health_service.domain.channels import ORDINARY_CHANNELS, ChannelLabel
from health_service.infrastructure.persistence import catalog as persistence
from health_service.infrastructure.persistence.exceptions import (
    ChannelModelSelectionConflictError,
    MetricCodeConflictError,
    ModelArtifactConflictError,
    ModelCodeConflictError,
    ModelRecordNotFoundError,
    ModelVersionConflictError,
    ModelVersionRecordNotFoundError,
)
from health_service.infrastructure.storage import (
    EmptyUploadError,
    LocalFileStorage,
    StorageArea,
    StorageError,
    UploadTooLargeError,
)
from health_service.schemas.internal.catalog import (
    AvailableModelData,
    AvailableModelPage,
    CatalogModelData,
    MetricDefinitionData,
    ModelArtifactData,
    ModelVersionData,
)

_KNOWN_CHANNELS = frozenset(channel.value for channel in ChannelLabel)


def create_model(
    *, code: str, name: str, description: str | None
) -> CatalogModelData:
    """创建具有稳定编码的模型种类。"""
    if not code.strip() or len(code) > 100:
        raise CatalogConfigurationInvalid
    try:
        return persistence.create_model(code=code, name=name, description=description)
    except ModelCodeConflictError as error:
        raise CatalogConflict from error


def create_model_version(
    *,
    model_id: int,
    version: str,
    algorithm_key: str,
    params: dict[str, object] | None,
) -> ModelVersionData:
    """为已有模型创建未发布版本。"""
    if not version.strip() or len(version) > 64 or not algorithm_key.strip():
        raise CatalogConfigurationInvalid
    try:
        return persistence.create_model_version(
            model_id=model_id,
            version=version,
            algorithm_key=algorithm_key,
            params=params,
        )
    except ModelRecordNotFoundError as error:
        raise CatalogNotFound from error
    except ModelVersionConflictError as error:
        raise CatalogConflict from error


def list_model_versions(
    *, model_id: int, offset: int, limit: int
) -> tuple[int, tuple[ModelVersionData, ...]]:
    """稳定分页读取模型版本。"""
    _validate_pagination(offset=offset, limit=limit)
    if persistence.get_model(model_id=model_id) is None:
        raise CatalogNotFound
    return persistence.list_model_versions(model_id=model_id, offset=offset, limit=limit)


def update_model_version(
    *, version_id: int, changes: dict[str, object]
) -> ModelVersionData:
    """更新未发布版本的算法标识或固定参数。"""
    if set(changes) - {"algorithm_key", "params"}:
        raise CatalogConfigurationInvalid
    if not changes:
        version = persistence.get_model_version(version_id=version_id)
        if version is None:
            raise CatalogNotFound
        return version

    try:
        with transaction.atomic():
            version = persistence.lock_model_version(version_id=version_id)
            if version.published_at is not None:
                raise CatalogConflict
            updated = persistence.save_model_version(
                version_id=version_id,
                changes=changes,
            )
            if updated is None:
                raise CatalogConflict
            return updated
    except ModelVersionRecordNotFoundError as error:
        raise CatalogNotFound from error


def upload_model_artifact(
    *,
    version_id: int,
    logical_name: str,
    source: BinaryIO | object,
) -> ModelArtifactData:
    """先完整保存制品，再在同一版本锁内登记；登记失败删除本次文件。"""
    if not logical_name.strip() or len(logical_name) > 255:
        raise CatalogConfigurationInvalid
    storage = _local_storage()
    stored_file = None
    try:
        with transaction.atomic():
            version = persistence.lock_model_version(version_id=version_id)
            if version.published_at is not None:
                raise CatalogConflict
            stored_file = storage.save_upload(
                source,
                area=StorageArea.MODEL_ARTIFACT,
                max_bytes=settings.MAX_UPLOAD_BYTES,
            )
            return persistence.create_model_artifact(
                version_id=version_id,
                logical_name=logical_name,
                storage_key=stored_file.storage_key,
                size_bytes=stored_file.size_bytes,
            )
    except BaseException as error:
        if stored_file is not None:
            storage.delete_file(StorageArea.MODEL_ARTIFACT, stored_file.storage_key)
        if isinstance(error, ModelVersionRecordNotFoundError):
            raise CatalogNotFound from error
        if isinstance(error, ModelArtifactConflictError):
            raise CatalogConflict from error
        if isinstance(error, (UploadTooLargeError, EmptyUploadError)):
            raise CatalogConfigurationInvalid from error
        raise


def create_metric_definition(
    *, code: str, description: str | None, value_type: str, unit: str | None
) -> MetricDefinitionData:
    """创建不可复用语义的全局指标定义。"""
    if (
        not code.strip()
        or len(code) > 128
        or value_type not in {"number", "boolean"}
        or (value_type == "boolean" and unit is not None)
    ):
        raise CatalogConfigurationInvalid
    try:
        return persistence.create_metric_definition(
            code=code,
            description=description,
            value_type=value_type,
            unit=unit,
        )
    except MetricCodeConflictError as error:
        raise CatalogConflict from error


def set_model_version_metrics(
    *, version_id: int, metric_codes: tuple[str, ...]
) -> tuple[MetricDefinitionData, ...]:
    """验证指标代码及算法语义后原子替换未发布版本投影集合。"""
    if len(set(metric_codes)) != len(metric_codes):
        raise CatalogConfigurationInvalid
    try:
        with transaction.atomic():
            version = persistence.lock_model_version(version_id=version_id)
            if version.published_at is not None:
                raise CatalogConflict
            algorithm = _registered_algorithm(version.algorithm_key)
            metrics = persistence.get_metric_definitions(metric_codes=metric_codes)
            if len(metrics) != len(metric_codes):
                raise CatalogConfigurationInvalid
            _validate_metric_contracts(algorithm, metrics)
            return persistence.replace_model_version_metrics(
                version_id=version_id,
                metric_ids=tuple(metric.metric_id for metric in metrics),
            )
    except ModelVersionRecordNotFoundError as error:
        raise CatalogNotFound from error


def publish_model_version(*, version_id: int) -> ModelVersionData:
    """校验受控算法、参数、制品文件与指标契约并首次发布版本。"""
    try:
        with transaction.atomic():
            version = persistence.lock_model_version(version_id=version_id)
            if version.published_at is not None:
                return version

            algorithm = _registered_algorithm(version.algorithm_key)
            params = version.params if version.params is not None else {}
            if not isinstance(params, Mapping):
                raise CatalogConfigurationInvalid
            try:
                algorithm.validate_params(params)
            except (InferenceError, TypeError, ValueError) as error:
                raise CatalogConfigurationInvalid from error

            artifacts = persistence.list_model_artifacts(version_id=version_id)
            if {artifact.logical_name for artifact in artifacts} != set(
                algorithm.required_artifacts
            ):
                raise CatalogConfigurationInvalid
            storage = _local_storage()
            try:
                for artifact in artifacts:
                    storage.resolve_file(
                        StorageArea.MODEL_ARTIFACT,
                        artifact.storage_key,
                    )
            except StorageError as error:
                raise CatalogConfigurationInvalid from error

            metrics = persistence.list_model_version_metrics(version_id=version_id)
            _validate_metric_contracts(algorithm, metrics)
            published = persistence.publish_model_version(
                version_id=version_id,
                published_at=timezone.now(),
            )
            if published is None:
                raise CatalogConflict
            return published
    except ModelVersionRecordNotFoundError as error:
        raise CatalogNotFound from error


def select_channel_model_version(
    *, channel: str, model_id: int, version_id: int
) -> ModelVersionData:
    """锁定渠道选择并原子终止旧版本全部草稿。"""
    if channel not in _KNOWN_CHANNELS:
        raise CatalogNotFound
    if persistence.get_model(model_id=model_id) is None:
        raise CatalogNotFound
    version = persistence.get_model_version(version_id=version_id)
    if version is None:
        raise CatalogNotFound
    if version.model_id != model_id or version.published_at is None:
        raise CatalogConfigurationInvalid

    try:
        with transaction.atomic():
            channel_id = persistence.create_or_lock_channel(label=channel)
            selection = persistence.lock_model_selection(
                channel_id=channel_id,
                model_id=model_id,
            )
            if selection is not None and selection[1] == version_id:
                return version
            if selection is None:
                persistence.create_model_selection(
                    channel_id=channel_id,
                    model_id=model_id,
                    version_id=version_id,
                )
            else:
                persistence.update_model_selection(
                    selection_id=selection[0],
                    version_id=version_id,
                )
                task_ids = persistence.lock_draft_task_ids(
                    channel_id=channel_id,
                    version_id=selection[1],
                )
                persistence.fail_model_switched_drafts(
                    task_ids=task_ids,
                    finished_at=timezone.now(),
                )
            return version
    except ChannelModelSelectionConflictError as error:
        raise CatalogConflict from error


def list_available_models(
    *, channel: str, offset: int, limit: int
) -> AvailableModelPage:
    """返回当前普通渠道已发布模型，不公开参数或文件路径。"""
    _validate_pagination(offset=offset, limit=limit)
    if channel not in ORDINARY_CHANNELS:
        raise CatalogNotFound
    total, versions = persistence.list_selected_versions(
        channel_label=channel,
        offset=offset,
        limit=limit,
    )
    items: list[AvailableModelData] = []
    for version in versions:
        algorithm = _registered_algorithm(version.algorithm_key)
        items.append(
            AvailableModelData(
                model_id=version.model_id,
                model_code=version.model_code,
                model_name=version.model_name,
                version_id=version.version_id,
                version=version.version,
                required_inputs=algorithm.required_inputs,
                is_example=algorithm.is_example,
            )
        )
    return AvailableModelPage(
        items=tuple(items),
        total=total,
        offset=offset,
        limit=limit,
    )


def _registered_algorithm(key: str) -> AlgorithmDefinition:
    """将未注册或仅有模板的算法标识转为稳定目录校验错误。"""
    try:
        return get_algorithm(key)
    except UnknownAlgorithmError as error:
        raise CatalogConfigurationInvalid from error


def _validate_metric_contracts(
    algorithm: AlgorithmDefinition,
    metrics: tuple[MetricDefinitionData, ...],
) -> None:
    """要求每个被选指标均与算法声明的代码、类型和单位完全一致。"""
    contracts = {contract.code: contract for contract in algorithm.metric_contracts}
    if len(contracts) != len(algorithm.metric_contracts):
        raise CatalogConfigurationInvalid
    for metric in metrics:
        contract = contracts.get(metric.code)
        if (
            contract is None
            or contract.value_type != metric.value_type
            or contract.unit != metric.unit
        ):
            raise CatalogConfigurationInvalid


def _validate_pagination(*, offset: int, limit: int) -> None:
    """拒绝负偏移、非正页长和超过 100 的无界分页。"""
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise CatalogConfigurationInvalid


def _local_storage() -> LocalFileStorage:
    """使用进程配置构造已校验的隔离存储根目录。"""
    return LocalFileStorage(
        Path(settings.TASK_DATA_ROOT),
        Path(settings.MODEL_ARTIFACT_ROOT),
    )
