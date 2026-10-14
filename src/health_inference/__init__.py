"""公开纯 Python 推理契约并注册内置算法。"""

from .algorithms import HR_BR_EXAMPLE
from .exceptions import InferenceError, UnknownAlgorithmError
from .registry import get_algorithm, list_algorithms, register_algorithm
from .types import AlgorithmDefinition, InferenceInput, InferenceOutput, MetricContract

__all__ = [
    "AlgorithmDefinition",
    "HR_BR_EXAMPLE",
    "InferenceError",
    "InferenceInput",
    "MetricContract",
    "InferenceOutput",
    "UnknownAlgorithmError",
    "get_algorithm",
    "list_algorithms",
    "register_algorithm",
]