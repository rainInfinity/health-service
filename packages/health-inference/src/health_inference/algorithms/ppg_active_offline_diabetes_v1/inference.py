"""运行糖尿病 ONNX 模型并校验目标类别概率。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    POSITIVE_CLASS_INDEX,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.features import (
    build_diabetes_features,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.resources import (
    DiabetesResources,
)
from health_inference.exceptions import InferenceError


@dataclass(frozen=True, slots=True)
class RawDiabetesPrediction:
    """保存模型返回的未经舍入目标类别概率。"""

    probability: float  # ONNX 类别 1 对应的原始有限概率。


def run_diabetes_inference(
    prepared: PreprocessedDiabetes,
    resources: DiabetesResources,
) -> RawDiabetesPrediction:
    """构造固定 918 维输入并验证 ONNX 标签与概率列一致。"""
    import numpy as np

    features = build_diabetes_features(prepared).values
    if features.shape != (1, 918) or features.dtype != np.float32:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型输入格式无效")
    try:
        outputs = resources.session.run(
            [resources.label_output_name, resources.probabilities_output_name],
            {resources.input_name: features},
        )
    except Exception as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "推理执行失败") from error
    if not isinstance(outputs, (list, tuple)) or len(outputs) != 2:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型输出格式无效")

    labels = np.asarray(outputs[0])
    probabilities = np.asarray(outputs[1])
    if (
        labels.shape != (1,)
        or labels.dtype != np.int64
        or probabilities.shape != (1, 2)
        or probabilities.dtype != np.float32
        or not np.isfinite(probabilities).all()
        or np.any(probabilities < 0.0)
        or np.any(probabilities > 1.0)
        or not math.isclose(float(probabilities[0].sum()), 1.0, rel_tol=0.0, abs_tol=1e-6)
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型输出格式无效")
    if int(labels[0]) not in (0, 1) or int(labels[0]) != int(np.argmax(probabilities[0])):
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型类别映射无效")

    probability = float(probabilities[0, POSITIVE_CLASS_INDEX])
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型概率无效")
    return RawDiabetesPrediction(probability=probability)
