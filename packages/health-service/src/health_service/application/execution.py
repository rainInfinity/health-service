"""编排任务领取、租约续约及成功结果持久化。"""

import math
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import transaction
from health_inference import (
    AlgorithmDefinition,
    InferenceError,
    InferenceOutput,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
    get_algorithm,
)
from health_inference.exceptions import InputError, InputErrorReason

from health_service.inference_logging import log_inference_event
from health_service.infrastructure.persistence import catalog as catalog_persistence
from health_service.infrastructure.persistence import tasks as persistence
from health_service.infrastructure.storage import (
    LocalFileStorage,
    StorageArea,
    StorageError,
)
from health_service.schemas.internal.execution import (
    PreparedInferenceResult,
    ProjectedTaskMetricData,
)

_SAFE_FAILURE_MESSAGES = {
    "TASK.INVALID_VERSION": "固定模型版本不可用",
    "TASK.UNKNOWN_ALGORITHM": "算法不可用",
    "TASK.RETRY_EXHAUSTED": "任务已达到最大执行次数",
    "TASK.MODEL_SWITCHED": "任务使用的模型版本已切换",
    "TASK.EXECUTION_ERROR": "任务处理失败",
    "INFERENCE.INVALID_INPUT": "推理输入无效",
    "INFERENCE.INVALID_ARTIFACT": "模型制品不可用",
    "INFERENCE.MISSING_PROFILE": "计算所需资料缺失",
    "INFERENCE.INVALID_PARAMETERS": "模型参数无效",
    "INFERENCE.INVALID_PROFILE": "提交资料快照无效",
    "INFERENCE.OUTPUT_UNAVAILABLE": "计算输出目录不可用",
    "INFERENCE.STORAGE_UNAVAILABLE": "推理文件存储不可用",
    "INFERENCE.EXECUTION_ERROR": "推理执行失败",
    "INFERENCE.EXECUTION_TIMEOUT": "推理执行超时",
    "RESULT.INVALID_OUTPUT": "推理结果格式无效",
}

_INPUT_FAILURE_MESSAGES = {
    InputErrorReason.CSV_FORMAT: "PPG CSV 格式无效",
    InputErrorReason.MISSING_COLUMN: "PPG CSV 缺少必需列",
    InputErrorReason.INVALID_CHANNELS: "PPG 必须完整提供一组 RGB 或 YUV 通道",
    InputErrorReason.INVALID_NUMERIC_VALUE: "PPG 通道必须为合法数值",
    InputErrorReason.NON_FINITE_VALUE: "PPG 输入数值必须为有限值",
    InputErrorReason.INVALID_TIMESTAMP: "PPG 时间戳必须为有限且严格递增的毫秒数值",
    InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT: "当前算法版本仅支持单块 PPG",
    InputErrorReason.INSUFFICIENT_DATA: "PPG 数据不足以执行本算法所需处理",
    InputErrorReason.INVALID_SAMPLING_RATE: "PPG 采样率不支持本算法所需处理",
}

_QUALITY_FAILURE_MESSAGES = {
    QualityRejectionReason.LOW_SCORE: "信号质量评分未达到要求，请重新采集",
    QualityRejectionReason.NO_VALID_SIGNAL: "未检测到可用信号，请重新采集",
}


def record_task_execution_failure(
    *,
    task_id: int,
    execution_token: UUID,
    error: InferenceError | None,
) -> str | None:
    """提交安全失败终态后返回实际失败码；失权时返回空值。"""
    failure_code, failure_message, failure_detail = _safe_failure_fields(error)
    committed = persistence.record_task_execution_failure(
        task_id=task_id,
        execution_token=execution_token,
        failure_code=failure_code,
        failure_message=failure_message,
        failure_detail=failure_detail,
    )
    return failure_code if committed else None


def _safe_failure_fields(
    error: InferenceError | None,
) -> tuple[str, str, dict[str, object] | None]:
    """将受控输入原因和质量拒绝映射为固定安全失败。"""
    if type(error) is InputError:
        if getattr(error, "code", None) != "INFERENCE.INVALID_INPUT":
            return (
                "INFERENCE.EXECUTION_ERROR",
                _SAFE_FAILURE_MESSAGES["INFERENCE.EXECUTION_ERROR"],
                None,
            )
        reason = getattr(error, "reason", None)
        message = _INPUT_FAILURE_MESSAGES.get(reason) if type(reason) is InputErrorReason else None
        return (
            "INFERENCE.INVALID_INPUT",
            message or _SAFE_FAILURE_MESSAGES["INFERENCE.INVALID_INPUT"],
            None,
        )
    if isinstance(error, QualityRejectedError):
        rejection = _validated_quality_rejection(error)
        if rejection is None:
            return (
                "INFERENCE.EXECUTION_ERROR",
                _SAFE_FAILURE_MESSAGES["INFERENCE.EXECUTION_ERROR"],
                None,
            )
        details = None
        if rejection.details is not None:
            details = {
                "score": rejection.details.score,
                "threshold": rejection.details.threshold,
            }
        return (
            "INFERENCE.QUALITY_REJECTED",
            _QUALITY_FAILURE_MESSAGES[rejection.reason],
            {
                "quality": {
                    "reason": rejection.reason.value,
                    "details": details,
                }
            },
        )
    if error is not None and isinstance(error.code, str):
        message = _SAFE_FAILURE_MESSAGES.get(error.code)
        if message is not None:
            return error.code, message, None
        if error.code.startswith("TASK."):
            return (
                "TASK.EXECUTION_ERROR",
                _SAFE_FAILURE_MESSAGES["TASK.EXECUTION_ERROR"],
                None,
            )
        if error.code.startswith("RESULT."):
            return (
                "RESULT.INVALID_OUTPUT",
                _SAFE_FAILURE_MESSAGES["RESULT.INVALID_OUTPUT"],
                None,
            )
    return (
        "INFERENCE.EXECUTION_ERROR",
        _SAFE_FAILURE_MESSAGES["INFERENCE.EXECUTION_ERROR"],
        None,
    )


def _validated_quality_rejection(
    error: QualityRejectedError,
) -> QualityRejection | None:
    """重建并校验异常数据，避免伪造错误码或损坏对象成为质量结论。"""
    if error.code != "INFERENCE.QUALITY_REJECTED":
        return None
    try:
        rejection = error.rejection
        return QualityRejection(rejection.reason, rejection.details)
    except (AttributeError, TypeError, ValueError):
        return None


def claim_task_for_execution(*, task_id: int) -> UUID | None:
    """按数据库租约配置尝试取得任务执行权。"""
    return persistence.claim_task_for_execution(
        task_id=task_id,
        lease_seconds=settings.INFERENCE_LEASE_SECONDS,
        max_attempts=settings.INFERENCE_MAX_ATTEMPTS,
    )


def renew_task_execution_lease(*, task_id: int, execution_token: UUID) -> bool:
    """以当前执行令牌续约，过期或失效令牌返回 false。"""
    return persistence.renew_task_execution_lease(
        task_id=task_id,
        execution_token=execution_token,
        lease_seconds=settings.INFERENCE_LEASE_SECONDS,
    )


def save_task_execution_success(
    *,
    task_id: int,
    execution_token: UUID,
    result: PreparedInferenceResult,
) -> bool:
    """校验固定版本结果并在当前有效租约内原子写入成功状态。"""
    context = persistence.get_active_task_execution_context(
        task_id=task_id,
        execution_token=execution_token,
    )
    if context is None:
        return False
    version = catalog_persistence.get_model_version(version_id=context.model_version_id)
    if version is None or version.published_at is None:
        raise InferenceError("TASK.INVALID_VERSION", "模型版本不可用")
    algorithm = get_algorithm(version.algorithm_key)

    if not isinstance(result.payload, Mapping) or "output_files" in result.payload:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if not isinstance(result.output_files, Mapping):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
    output_storage_keys: dict[str, str] = {}
    output_paths: dict[str, Path] = {}
    if result.output_files:
        storage = _task_storage()
        for logical_name, storage_key in result.output_files.items():
            if (
                not isinstance(logical_name, str)
                or not logical_name.strip()
                or not isinstance(storage_key, str)
                or not storage_key
            ):
                raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
            try:
                output_path = storage.resolve_file(
                    StorageArea.TASK_DATA,
                    storage_key,
                )
                validated_key = storage.validate_output_file(
                    task_id,
                    execution_token,
                    output_path,
                )
            except (StorageError, TypeError, ValueError) as error:
                raise InferenceError(
                    "RESULT.INVALID_OUTPUT",
                    "处理后文件无效",
                ) from error
            if validated_key != storage_key:
                raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件无效")
            output_storage_keys[logical_name] = validated_key
            output_paths[logical_name] = output_path

    inference_output = InferenceOutput(
        payload=result.payload,
        metrics=result.metrics,
        output_files=output_paths,
    )
    try:
        algorithm.result_validator(inference_output)
    except InferenceError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效") from error

    result_payload = _json_object(result.payload)
    if output_storage_keys:
        result_payload["output_files"] = output_storage_keys
    projected_metrics = _project_selected_metrics(
        version_id=context.model_version_id,
        algorithm=algorithm,
        values=result.metrics,
    )
    success_committed = persistence.save_task_execution_success(
        task_id=task_id,
        execution_token=execution_token,
        result_payload=result_payload,
        metrics=projected_metrics,
    )
    if success_committed:
        transaction.on_commit(lambda: publish_successful_task_outputs(task_id=task_id))
    return success_committed


def publish_successful_task_outputs(*, task_id: int) -> bool:
    """锁定并发布数据库权威成功结果；文件系统故障不撤销任务成功。"""
    with transaction.atomic():
        publication = persistence.lock_successful_task_output_publication(
            task_id=task_id,
        )
        if publication is None or not publication.output_storage_keys:
            return False
        try:
            return _task_storage().publish_task_output_directory(
                channel=publication.channel_label,
                created_at=publication.created_at,
                task_id=publication.task_id,
                output_storage_keys=publication.output_storage_keys,
            )
        except StorageError:
            log_inference_event(
                "inference.output.publish_failed",
                task_id=task_id,
                reason="output_publish_failed",
            )
            return False


def _project_selected_metrics(
    *,
    version_id: int,
    algorithm: AlgorithmDefinition,
    values: Mapping[str, object],
) -> tuple[ProjectedTaskMetricData, ...]:
    """仅投影版本选定且确由本次计算返回的有效指标。"""
    if not isinstance(values, Mapping):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
    contracts = {contract.code: contract for contract in algorithm.metric_contracts}
    if len(contracts) != len(algorithm.metric_contracts):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标契约无效")
    selected = catalog_persistence.list_model_version_metrics(version_id=version_id)
    selected_by_code = {metric.code: metric for metric in selected}
    for metric in selected:
        contract = contracts.get(metric.code)
        if (
            contract is None
            or contract.value_type != metric.value_type
            or contract.unit != metric.unit
        ):
            raise InferenceError("RESULT.INVALID_OUTPUT", "模型指标配置无效")

    projected: list[ProjectedTaskMetricData] = []
    for code, value in values.items():
        contract = contracts.get(code) if isinstance(code, str) else None
        if contract is None or not _metric_value_matches(contract.value_type, value):
            raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
        definition = selected_by_code.get(code)
        if definition is not None:
            projected.append(ProjectedTaskMetricData(metric_id=definition.metric_id, value=value))
    return tuple(projected)


def _metric_value_matches(value_type: str, value: object) -> bool:
    """拒绝布尔与数值混型，并确保数值有限。"""
    if value_type == "boolean":
        return type(value) is bool
    if value_type != "number" or type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _json_object(value: Mapping[str, object]) -> dict[str, object]:
    """递归复制为严格 JSON 对象，拒绝路径、非有限数及非字符串键。"""
    normalized = _json_value(value)
    if not isinstance(normalized, dict):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    return normalized


def _json_value(value: object) -> object:
    """将支持的 JSON 原生值复制为不含惰性对象的结果。"""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")


def _task_storage() -> LocalFileStorage:
    """按进程配置构造结果文件验证所需的受保护存储。"""
    try:
        return LocalFileStorage(
            Path(settings.TASK_DATA_ROOT),
            Path(settings.MODEL_ARTIFACT_ROOT),
        )
    except StorageError as error:
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件不可用") from error
