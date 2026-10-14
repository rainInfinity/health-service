"""依据任务固定版本和执行令牌准备隔离的纯 Python 推理输入。"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from uuid import UUID

from django.conf import settings

from health_inference import (
    AlgorithmDefinition,
    InferenceError,
    InferenceInput,
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
    """缓存已发布版本的受控执行器、固定参数和制品存储键。"""

    algorithm: AlgorithmDefinition  # 代码显式注册的固定算法契约。
    params: Mapping[str, object]  # 已发布版本不可变参数。
    artifact_storage_keys: Mapping[str, str]  # 制品逻辑名到相对存储键。


@cache
def _version_runtime(version_id: int) -> _VersionRuntime:
    """按固定版本主键加载一次纯元数据，不读取渠道当前选择。"""
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
    try:
        algorithm.validate_params(params)
    except (TypeError, ValueError) as error:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "模型参数无效") from error

    artifacts = catalog_persistence.list_model_artifacts(version_id=version_id)
    artifact_storage_keys = {
        artifact.logical_name: artifact.storage_key for artifact in artifacts
    }
    if set(artifact_storage_keys) != set(algorithm.required_artifacts):
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品配置无效")
    return _VersionRuntime(
        algorithm=algorithm,
        params=params,
        artifact_storage_keys=artifact_storage_keys,
    )


def clear_version_runtime_cache() -> None:
    """消费者停止并排空执行后释放进程内版本缓存，不删除模型制品。"""
    _version_runtime.cache_clear()


def run_task_inference(
    *, task_id: int, execution_token: UUID
) -> PreparedInferenceResult | None:
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
    if any(
        profile.get(field) is None
        for field in runtime.algorithm.required_profile_fields
    ):
        raise InferenceError("INFERENCE.MISSING_PROFILE", "计算所需资料缺失")

    missing_inputs = set(runtime.algorithm.required_inputs) - set(
        context.input_storage_keys
    )
    if missing_inputs:
        raise InferenceError("INFERENCE.INVALID_INPUT", "缺少算法所需输入文件")
    missing_artifacts = set(runtime.algorithm.required_artifacts) - set(
        runtime.artifact_storage_keys
    )
    if missing_artifacts:
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "缺少算法所需模型制品")

    storage = _task_storage()
    try:
        files = {
            logical_name: storage.resolve_file(
                StorageArea.TASK_DATA,
                context.input_storage_keys[logical_name],
            )
            for logical_name in runtime.algorithm.required_inputs
        }
    except StorageError as error:
        raise InferenceError("INFERENCE.INVALID_INPUT", "任务输入文件不可用") from error
    try:
        artifacts = {
            logical_name: storage.resolve_file(
                StorageArea.MODEL_ARTIFACT,
                runtime.artifact_storage_keys[logical_name],
            )
            for logical_name in runtime.algorithm.required_artifacts
        }
    except StorageError as error:
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品不可用") from error

    try:
        output_dir = storage.create_execution_output_dir(task_id, execution_token)
    except StorageError as error:
        raise InferenceError("INFERENCE.OUTPUT_UNAVAILABLE", "计算输出目录不可用") from error

    inference_input = InferenceInput(
        files=files,
        artifacts=artifacts,
        profile=profile,
        params=deepcopy(dict(runtime.params)),
        output_dir=output_dir,
    )
    output = runtime.algorithm.run(inference_input)
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
