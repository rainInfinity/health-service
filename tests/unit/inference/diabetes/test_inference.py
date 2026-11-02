"""验证糖尿病 v1 目标类别概率及模型输出错误边界。"""

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.features import (
    DiabetesFeatureMatrix,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.inference import (
    RawDiabetesPrediction,
    run_diabetes_inference,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.resources import (
    DiabetesResources,
)
from health_inference.exceptions import InferenceError


class _FixedSession:
    """返回指定类别及概率以覆盖真实消费者可见契约。"""

    def __init__(self, outputs: object):
        """保存固定模型输出或待抛出的异常。"""
        self.outputs = outputs

    def run(self, output_names: list[str], inputs: dict[str, np.ndarray]):
        """按资源节点名返回固定模型输出。"""
        if isinstance(self.outputs, Exception):
            raise self.outputs
        return self.outputs


def _prepared() -> PreprocessedDiabetes:
    """创建供模型输入契约测试使用的有限标准化波形。"""
    return PreprocessedDiabetes(
        timestamps_ms=tuple(float(index) for index in range(450)),
        ppg_signal=np.linspace(-1.0, 1.0, 450),
        sampling_rate_hz=30.0,
        channel_format="rgb",
    )


def _resources(outputs: object) -> DiabetesResources:
    """绑定可控会话和固定 ONNX 节点名。"""
    return DiabetesResources(
        session=_FixedSession(outputs),
        input_name="input",
        label_output_name="label",
        probabilities_output_name="probabilities",
    )


def test_returns_class_one_probability_after_validating_model_label(
    monkeypatch: pytest.MonkeyPatch,
):
    """标签与 argmax 一致时返回类别 1 概率，而非类别 0 概率。"""
    feature_values = np.zeros((1, 918), dtype=np.float32)
    monkeypatch.setattr(
        "health_inference.algorithms.ppg_active_offline_diabetes_v1.inference.build_diabetes_features",
        lambda prepared: DiabetesFeatureMatrix(feature_values, ("feature",) * 918),
    )

    result = run_diabetes_inference(
        _prepared(),
        _resources(
            [
                np.asarray([1], dtype=np.int64),
                np.asarray([[0.31, 0.69]], dtype=np.float32),
            ]
        ),
    )

    assert result.probability == pytest.approx(0.69, abs=1e-8)
    assert isinstance(result, RawDiabetesPrediction)


@pytest.mark.parametrize(
    ("outputs", "expected_code"),
    (
        (
            [np.asarray([0], dtype=np.int64), np.asarray([[0.31, 0.69]], dtype=np.float32)],
            "INFERENCE.INVALID_ARTIFACT",
        ),
        (
            [np.asarray([2], dtype=np.int64), np.asarray([[0.31, 0.69]], dtype=np.float32)],
            "INFERENCE.INVALID_ARTIFACT",
        ),
        (
            [np.asarray([1], dtype=np.int64), np.asarray([[0.31, 0.60]], dtype=np.float32)],
            "RESULT.INVALID_OUTPUT",
        ),
        (
            [np.asarray([1], dtype=np.int64), np.asarray([[np.nan, np.nan]], dtype=np.float32)],
            "RESULT.INVALID_OUTPUT",
        ),
        (
            [np.asarray([1], dtype=np.int64), np.asarray([[0.31, 0.69]], dtype=np.int64)],
            "RESULT.INVALID_OUTPUT",
        ),
        (
            [np.asarray([1], dtype=np.int64), np.asarray([[0.31, 0.69, 0.0]], dtype=np.float32)],
            "RESULT.INVALID_OUTPUT",
        ),
        (
            [np.asarray([1], dtype=np.int64), np.asarray([[-0.01, 1.01]], dtype=np.float32)],
            "RESULT.INVALID_OUTPUT",
        ),
        (
            [np.asarray([1], dtype=np.int64)],
            "RESULT.INVALID_OUTPUT",
        ),
    ),
    ids=(
        "label_disagrees",
        "label_outside_mapping",
        "probability_sum",
        "non_finite_probabilities",
        "wrong_probability_dtype",
        "wrong_probability_shape",
        "out_of_range_probabilities",
        "missing_output",
    ),
)
def test_rejects_inconsistent_or_invalid_model_outputs(
    monkeypatch: pytest.MonkeyPatch,
    outputs: object,
    expected_code: str,
):
    """不配套类别映射作为制品错误，其他非法输出按结果错误处理。"""
    monkeypatch.setattr(
        "health_inference.algorithms.ppg_active_offline_diabetes_v1.inference.build_diabetes_features",
        lambda prepared: DiabetesFeatureMatrix(
            np.zeros((1, 918), dtype=np.float32),
            ("feature",) * 918,
        ),
    )

    with pytest.raises(InferenceError) as error:
        run_diabetes_inference(_prepared(), _resources(outputs))

    assert error.value.code == expected_code


def test_maps_onnx_runtime_failure_to_stable_inference_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """ONNX Runtime 异常转为不泄露运行时正文的稳定错误。"""
    monkeypatch.setattr(
        "health_inference.algorithms.ppg_active_offline_diabetes_v1.inference.build_diabetes_features",
        lambda prepared: DiabetesFeatureMatrix(
            np.zeros((1, 918), dtype=np.float32),
            ("feature",) * 918,
        ),
    )

    with pytest.raises(InferenceError) as error:
        run_diabetes_inference(_prepared(), _resources(RuntimeError("private runtime details")))

    assert error.value.code == "INFERENCE.EXECUTION_ERROR"
    assert str(error.value) == "推理执行失败"
