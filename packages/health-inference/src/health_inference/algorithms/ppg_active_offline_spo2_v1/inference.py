"""按任务稳定特征矩阵执行标准化并构建 ONNX float32 输入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from health_inference.exceptions import InferenceError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from health_inference.algorithms.ppg_active_offline_spo2_v1.postprocessing import (
        Spo2Prediction,
    )
    from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
        PreprocessedSpo2,
    )
    from health_inference.algorithms.ppg_active_offline_spo2_v1.resources import (
        Spo2Resources,
    )


def _standardize_task_matrix(
    stable_matrix: NDArray[np.float32],
) -> NDArray[np.float32]:
    """用当前任务窗口拟合 StandardScaler，不复用其他任务的统计量。"""
    import numpy as np

    matrix = np.asarray(stable_matrix, dtype=np.float32)
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵形状无效")
    if not np.isfinite(matrix).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵无效")
    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧标准化依赖不可用") from error
    try:
        standardized = StandardScaler().fit_transform(matrix)
    except Exception as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征标准化失败") from error
    if standardized.shape != matrix.shape or not np.isfinite(standardized).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧标准化结果无效")
    return standardized


def prepare_model_input(
    full_feature_matrix: NDArray[np.float32], stable_indices: tuple[int, ...]
) -> NDArray[np.float32]:
    """按稳定索引筛选特征、逐任务归一化并返回有限 float32 矩阵。"""
    import numpy as np

    try:
        with np.errstate(over="ignore", invalid="ignore"):
            full_matrix = np.asarray(full_feature_matrix, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵无效") from error
    if full_matrix.ndim != 2 or not full_matrix.shape[0] or not full_matrix.shape[1]:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵形状无效")
    if not np.isfinite(full_matrix).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵无效")
    if (
        not isinstance(stable_indices, tuple)
        or not stable_indices
        or any(type(index) is not int or index < 0 or index >= full_matrix.shape[1] for index in stable_indices)
        or len(set(stable_indices)) != len(stable_indices)
    ):
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "稳定特征索引无效")

    stable_matrix = full_matrix[:, stable_indices]
    standardized = _standardize_task_matrix(stable_matrix)
    minimum = standardized.min(axis=0)
    maximum = standardized.max(axis=0)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        normalized = (standardized - minimum) / (maximum - minimum + 1e-8)
        model_input = np.asarray(normalized, dtype=np.float32)
    if model_input.shape != stable_matrix.shape or not np.isfinite(model_input).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧模型输入无效")
    return model_input


@dataclass(frozen=True, slots=True)
class Spo2InferencePrediction:
    """将模型预测与逐窗口起点绑定，避免结果数组错位。"""

    prediction: Spo2Prediction  # 已校验并按旧规则聚合的窗口预测。
    window_starts: tuple[int, ...]  # 对应每个预测的输入样本起点。


def run_spo2_inference(
    prepared: PreprocessedSpo2, resources: Spo2Resources
) -> Spo2InferencePrediction:
    """按完整特征清单构建模型输入并校验每个窗口的 ONNX 输出。"""
    from health_inference.algorithms.ppg_active_offline_spo2_v1.features import (
        build_9ch_matrix,
        dicts_to_matrix,
        extract_window_features,
    )
    from health_inference.algorithms.ppg_active_offline_spo2_v1.postprocessing import (
        postprocess_predictions,
    )

    subject_data = build_9ch_matrix(prepared.feature_rgb, prepared.sampling_rate_hz)
    feature_dicts, window_starts = extract_window_features(
        subject_data, prepared.sampling_rate_hz
    )
    full_matrix = dicts_to_matrix(feature_dicts, list(resources.feature_names))
    model_input = prepare_model_input(full_matrix, resources.stable_indices)
    outputs = resources.session.run(
        [resources.output_name], {resources.input_name: model_input}
    )
    if not isinstance(outputs, (list, tuple)) or len(outputs) != 1:
        raise InferenceError("RESULT.INVALID_OUTPUT", "模型预测格式无效")
    prediction = postprocess_predictions(outputs[0], len(window_starts))
    return Spo2InferencePrediction(
        prediction=prediction,
        window_starts=tuple(int(start) for start in window_starts),
    )
