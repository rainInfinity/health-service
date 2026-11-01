"""校验血氧模型逐窗口预测并按旧规则聚合限幅。"""

import math
from dataclasses import dataclass

from health_inference.exceptions import InferenceError


@dataclass(frozen=True, slots=True)
class Spo2Prediction:
    """保存未限幅窗口预测和最终血氧百分数。"""

    window_predictions: tuple[float, ...]  # ONNX 原始逐窗口输出，尚未限幅。
    spo2: float  # 原始预测最大值经 [90, 100] 限幅后的结果。


def postprocess_predictions(
    raw_predictions: object, expected_window_count: int
) -> Spo2Prediction:
    """先校验窗口数与有限性，再取最大值并限幅到 [90, 100]。"""
    import numpy as np

    if type(expected_window_count) is not int or expected_window_count <= 0:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型窗口数量无效")
    try:
        predictions = np.asarray(raw_predictions)
    except (TypeError, ValueError, OverflowError) as error:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型预测格式无效") from error

    if predictions.ndim == 2 and predictions.shape[1] == 1:
        predictions = predictions[:, 0]
    elif predictions.ndim != 1:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型预测形状无效")
    if (
        predictions.size == 0
        or predictions.size != expected_window_count
        or not np.issubdtype(predictions.dtype, np.number)
        or np.issubdtype(predictions.dtype, np.complexfloating)
        or not np.isfinite(predictions).all()
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型预测值无效")

    window_predictions = tuple(float(value) for value in predictions)
    maximum = max(window_predictions)
    if not math.isfinite(maximum):
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型预测值无效")
    spo2 = min(max(maximum, 90.0), 100.0)
    return Spo2Prediction(window_predictions=window_predictions, spo2=spo2)
