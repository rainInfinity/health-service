"""公开纯 Python 推理契约并注册内置算法。"""

from .algorithms import PPG_ACTIVE_OFFLINE_HR_BR_V1
from .exceptions import InferenceError, QualityRejectedError, UnknownAlgorithmError
from .quality import LowScoreDetails, QualityRejection, QualityRejectionReason
from .registry import get_algorithm, list_algorithms, register_algorithm
from .types import AlgorithmDefinition, InferenceInput, InferenceOutput, MetricContract

__all__ = [
    "PPG_ACTIVE_OFFLINE_HR_BR_V1",
    "AlgorithmDefinition",
    "InferenceError",
    "InferenceInput",
    "InferenceOutput",
    "LowScoreDetails",
    "MetricContract",
    "QualityRejectedError",
    "QualityRejection",
    "QualityRejectionReason",
    "UnknownAlgorithmError",
    "get_algorithm",
    "list_algorithms",
    "register_algorithm",
]
