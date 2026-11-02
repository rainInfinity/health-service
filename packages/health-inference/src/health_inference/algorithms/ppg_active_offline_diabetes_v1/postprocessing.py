"""按 ECMAScript toFixed 舍入规则生成风险三分类。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from health_inference.algorithms.ppg_active_offline_diabetes_v1.inference import (
    RawDiabetesPrediction,
)
from health_inference.exceptions import InferenceError

_ROUNDING_QUANTUM = Decimal("0.01")
_LOW_RISK_LIMIT = Decimal("0.66")
_MEDIUM_RISK_LIMIT = Decimal("0.76")


@dataclass(frozen=True, slots=True)
class DiabetesPrediction:
    """保存原始概率与服务端生成的旧版风险档位。"""

    probability: float  # 未舍入的 ONNX 目标类别概率。
    risk_level: int  # 0 低风险、1 中风险、2 高风险。


class DiabetesPostprocessingStrategy:
    """验证概率并将其按旧客户端规则转换为整数分类。"""

    def apply(self, prediction: RawDiabetesPrediction) -> DiabetesPrediction:
        """保留概率原值，并按舍入后的阈值比较值生成风险档位。"""
        risk_level = classify_probability(prediction.probability)
        return DiabetesPrediction(
            probability=prediction.probability,
            risk_level=risk_level,
        )


def classify_probability(probability: object) -> int:
    """按二进制浮点数精确舍入到两位并应用固定三分类阈值。"""
    if type(probability) is not float or not math.isfinite(probability):
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型概率无效")
    if not 0.0 <= probability <= 1.0:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型概率无效")

    rounded = Decimal.from_float(probability).quantize(
        _ROUNDING_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if rounded < _LOW_RISK_LIMIT:
        return 0
    if rounded < _MEDIUM_RISK_LIMIT:
        return 1
    return 2
