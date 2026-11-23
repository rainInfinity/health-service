"""统一独立任务与场景成员的安全失败分类及白名单校验。"""

from collections.abc import Mapping

from .exceptions import InferenceError, InputError, InputErrorReason, QualityRejectedError
from .quality import QualityRejection, QualityRejectionReason, quality_rejection_from_mapping

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


def safe_failure_fields(
    error: InferenceError | None,
) -> tuple[str, str, dict[str, object] | None]:
    """将受控错误转换为固定摘要，仅类型化质量拒绝产生私有明细。"""
    if type(error) is InputError:
        if getattr(error, "code", None) != "INFERENCE.INVALID_INPUT":
            return safe_failure_fields(None)
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
            return safe_failure_fields(None)
        details = None
        if rejection.details is not None:
            details = {
                "score": rejection.details.score,
                "threshold": rejection.details.threshold,
            }
        return (
            "INFERENCE.QUALITY_REJECTED",
            _QUALITY_FAILURE_MESSAGES[rejection.reason],
            {"quality": {"reason": rejection.reason.value, "details": details}},
        )
    code = getattr(error, "code", None)
    if isinstance(code, str):
        message = _SAFE_FAILURE_MESSAGES.get(code)
        if message is not None:
            return code, message, None
        if code.startswith("TASK."):
            return "TASK.EXECUTION_ERROR", _SAFE_FAILURE_MESSAGES["TASK.EXECUTION_ERROR"], None
        if code.startswith("RESULT."):
            return "RESULT.INVALID_OUTPUT", _SAFE_FAILURE_MESSAGES["RESULT.INVALID_OUTPUT"], None
    return "INFERENCE.EXECUTION_ERROR", _SAFE_FAILURE_MESSAGES["INFERENCE.EXECUTION_ERROR"], None


def _validated_quality_rejection(error: QualityRejectedError) -> QualityRejection | None:
    """重建异常中的拒绝对象，拒绝伪造代码和被篡改的数值。"""
    if getattr(error, "code", None) != "INFERENCE.QUALITY_REJECTED":
        return None
    try:
        rejection = error.rejection
        return QualityRejection(rejection.reason, rejection.details)
    except (AttributeError, TypeError, ValueError):
        return None


def is_safe_failure(code: object, message: object, quality_failure: object) -> bool:
    """验证最终成员失败的精确公开字段，禁止借汇总注入诊断信息。"""
    if type(code) is not str or type(message) is not str:
        return False
    if code == "INFERENCE.QUALITY_REJECTED":
        if not isinstance(quality_failure, Mapping) or set(quality_failure) != {"reason", "details"}:
            return False
        details = quality_failure["details"]
        if details is not None and (
            not isinstance(details, Mapping) or set(details) != {"score", "threshold"}
        ):
            return False
        rejection = quality_rejection_from_mapping(quality_failure)
        return rejection is not None and message == _QUALITY_FAILURE_MESSAGES[rejection.reason]
    if quality_failure is not None:
        return False
    if code == "INFERENCE.INVALID_INPUT" and message in _INPUT_FAILURE_MESSAGES.values():
        return True
    return code in _SAFE_FAILURE_MESSAGES and message == _SAFE_FAILURE_MESSAGES[code]
