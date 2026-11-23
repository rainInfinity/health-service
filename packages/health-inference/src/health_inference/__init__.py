"""公开纯 Python 推理契约并注册内置算法。"""

from .algorithms import (
    PPG_ACC_ACTIVE_OFFLINE_ALL_V1,
    PPG_ACC_ACTIVE_OFFLINE_BP_V1,
    PPG_ACTIVE_OFFLINE_ALL_V1,
    PPG_ACTIVE_OFFLINE_DIABETES_V1,
    PPG_ACTIVE_OFFLINE_HR_BR_V1,
    PPG_ACTIVE_OFFLINE_SPO2_V1,
)
from .exceptions import InferenceError, QualityRejectedError, UnknownAlgorithmError
from .quality import LowScoreDetails, QualityRejection, QualityRejectionReason
from .registry import get_algorithm, list_algorithms, register_algorithm
from .types import (
    AlgorithmDefinition,
    AlgorithmRuntime,
    InferenceInput,
    InferenceOutput,
    MetricContract,
    VersionResources,
)

__all__ = [
    "PPG_ACC_ACTIVE_OFFLINE_ALL_V1",
    "PPG_ACTIVE_OFFLINE_ALL_V1",
    "PPG_ACC_ACTIVE_OFFLINE_BP_V1",
    "PPG_ACTIVE_OFFLINE_DIABETES_V1",
    "PPG_ACTIVE_OFFLINE_HR_BR_V1",
    "PPG_ACTIVE_OFFLINE_SPO2_V1",
    "AlgorithmDefinition",
    "AlgorithmRuntime",
    "InferenceError",
    "InferenceInput",
    "InferenceOutput",
    "LowScoreDetails",
    "MetricContract",
    "QualityRejectedError",
    "QualityRejection",
    "QualityRejectionReason",
    "UnknownAlgorithmError",
    "VersionResources",
    "get_algorithm",
    "list_algorithms",
    "register_algorithm",
]
