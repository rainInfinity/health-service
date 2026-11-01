"""依据任务固定版本和执行令牌准备隔离的纯 Python 推理输入。"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from uuid import UUID

from django.conf import settings
from health_inference import (
    AlgorithmRuntime,
    InferenceError,
    InferenceInput,
    VersionResources,
    get_algorithm,
)

from health_service.infrastructure.persistence import catalog as catalog_persistence
from health_service.infrastructure.persistence import tasks as task_persistence
from health_service.infrastructure.storage import (
    LocalFileStorage,
    StorageArea,
    StorageError,
)
from health_service.schemas.internal.execution import PreparedInferenceResult


@dataclass(frozen=True, slots=True)
class _VersionRuntime:
    """缓存绑定到一个已发布版本的算法和模型资源。"""

    algorithm_runtime: AlgorithmRuntime  # 固定参数、模型路径及可复用执行器。


@cache
def _version_runtime(version_id: int) -> _VersionRuntime:
    """按固定版本主键加载并缓存算法运行时，不读取渠道当前选择。"""
    version = catalog_persistence.get_model_version(version_id=version_id)
    if version is None or version.published_at is None:
        raise InferenceError("TASK.INVALID_VERSION", "模型版本不可用")
    algorithm = get_algorithm(version.algorithm_key)
    if version.params is None:
        params: Mapping[str, object] = {}
    elif isinstance(version.params, Mapping):
        params = deepcopy(dict(version.params))
    else:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "模型参数无效")

    artifacts = catalog_persistence.list_model_artifacts(version_id=version_id)
    artifact_storage_keys = {artifact.logical_name: artifact.storage_key for artifact in artifacts}
    if set(artifact_storage_keys) != set(algorithm.required_artifacts):
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品配置无效")
    storage = _task_storage()
    try:
        artifact_paths = {
            logical_name: storage.resolve_file(StorageArea.MODEL_ARTIFACT, storage_key)
            for logical_name, storage_key in artifact_storage_keys.items()
        }
    except StorageError as error:
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品不可用") from error
    algorithm_runtime = algorithm.create_runtime(
        VersionResources(params=params, artifact_paths=artifact_paths)
    )
    return _VersionRuntime(algorithm_runtime=algorithm_runtime)


def clear_version_runtime_cache() -> None:
    """消费者停止并排空执行后释放进程内版本缓存，不删除模型制品。"""
    _version_runtime.cache_clear()


def run_task_inference(*, task_id: int, execution_token: UUID) -> PreparedInferenceResult | None:
    """从有效租约任务准备快照和文件并运行其固定版本算法。"""
    context = task_persistence.get_active_task_execution_context(
        task_id=task_id,
        execution_token=execution_token,
    )
    if context is None:
        return None

    runtime = _version_runtime(context.model_version_id)
    profile_snapshot = context.profile_snapshot
    if profile_snapshot is None:
        profile: dict[str, object] = {}
    elif isinstance(profile_snapshot, Mapping):
        profile = deepcopy(dict(profile_snapshot))
    else:
        raise InferenceError("INFERENCE.INVALID_PROFILE", "提交资料快照无效")
    client_metadata = context.client_metadata
    if client_metadata is not None:
        if not isinstance(client_metadata, Mapping):
            raise InferenceError("INFERENCE.INVALID_INPUT", "客户端元数据无效")
        client_metadata = deepcopy(dict(client_metadata))
    algorithm = runtime.algorithm_runtime.definition
    if any(profile.get(field) is None for field in algorithm.required_profile_fields):
        raise InferenceError("INFERENCE.MISSING_PROFILE", "计算所需资料缺失")

    missing_inputs = set(algorithm.required_inputs) - set(context.input_storage_keys)
    if missing_inputs:
        raise InferenceError("INFERENCE.INVALID_INPUT", "缺少算法所需输入文件")

    storage = _task_storage()
    try:
        files = {
            logical_name: storage.resolve_file(
                StorageArea.TASK_DATA,
                context.input_storage_keys[logical_name],
            )
            for logical_name in algorithm.required_inputs
        }
    except StorageError as error:
        raise InferenceError("INFERENCE.INVALID_INPUT", "任务输入文件不可用") from error

    try:
        output_dir = storage.create_execution_output_dir(task_id, execution_token)
    except StorageError as error:
        raise InferenceError("INFERENCE.OUTPUT_UNAVAILABLE", "计算输出目录不可用") from error

    inference_input = InferenceInput(
        files=files,
        artifacts=dict(runtime.algorithm_runtime.resources.artifact_paths),
        profile=profile,
        params=deepcopy(dict(runtime.algorithm_runtime.resources.params)),
        output_dir=output_dir,
        client_metadata=client_metadata,
    )
    output = runtime.algorithm_runtime.run(inference_input)
    if "output_files" in output.payload:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")

    output_storage_keys: dict[str, str] = {}
    for logical_name, output_path in output.output_files.items():
        if not isinstance(logical_name, str) or not logical_name.strip():
            raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
        try:
            output_storage_keys[logical_name] = storage.validate_output_file(
                task_id,
                execution_token,
                Path(output_path) if isinstance(output_path, (str, Path)) else output_path,
            )
        except (StorageError, TypeError, ValueError) as error:
            raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件无效") from error

    return PreparedInferenceResult(
        payload=deepcopy(dict(output.payload)),
        metrics=deepcopy(dict(output.metrics)),
        output_files=output_storage_keys,
    )


def _task_storage() -> LocalFileStorage:
    """按进程设置构造受保护的任务数据和模型制品存储。"""
    try:
        return LocalFileStorage(
            Path(settings.TASK_DATA_ROOT),
            Path(settings.MODEL_ARTIFACT_ROOT),
        )
    except StorageError as error:
        raise InferenceError("INFERENCE.STORAGE_UNAVAILABLE", "计算文件不可用") from error
