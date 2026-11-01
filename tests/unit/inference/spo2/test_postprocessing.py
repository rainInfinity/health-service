"""验证血氧窗口预测的结构校验、保留与聚合限幅。"""

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.postprocessing import (
    postprocess_predictions,
)
from health_inference.exceptions import InferenceError


@pytest.mark.parametrize(
    "raw_predictions",
    [
        np.asarray([95.5, 91.0], dtype=np.float32),
        np.asarray([[95.5], [91.0]], dtype=np.float32),
    ],
)
def test_postprocessing_preserves_window_predictions_and_uses_maximum(raw_predictions):
    """验证一维与单列输出均保留原预测，并以最大值作为结果。"""
    result = postprocess_predictions(raw_predictions, expected_window_count=2)

    assert result.window_predictions == (95.5, 91.0)
    assert result.spo2 == 95.5


@pytest.mark.parametrize(
    ("raw_predictions", "expected_window_count"),
    [
        (np.asarray([], dtype=np.float32), 1),
        (np.asarray([[90.0, 91.0]], dtype=np.float32), 1),
        (np.asarray([[[90.0]]], dtype=np.float32), 1),
        (np.asarray([90.0], dtype=np.float32), 2),
    ],
)
def test_postprocessing_rejects_empty_malformed_or_mismatched_predictions(
    raw_predictions, expected_window_count
):
    """验证空预测、错误形状和窗口数量不符均映射为无效输出。"""
    with pytest.raises(InferenceError) as invalid_output:
        postprocess_predictions(raw_predictions, expected_window_count)

    assert invalid_output.value.code == "RESULT.INVALID_OUTPUT"


@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf])
def test_postprocessing_rejects_non_finite_values_before_clipping(invalid_value):
    """验证即使其他预测超过上限，非有限值也不会被限幅掩盖。"""
    predictions = np.asarray([105.0, invalid_value], dtype=np.float32)

    with pytest.raises(InferenceError) as invalid_output:
        postprocess_predictions(predictions, expected_window_count=2)

    assert invalid_output.value.code == "RESULT.INVALID_OUTPUT"


@pytest.mark.parametrize(
    ("raw_predictions", "expected_spo2"),
    [
        ([92.0, 88.0], 92.0),
        ([87.0, 89.0], 90.0),
        ([102.0, 99.0], 100.0),
    ],
)
def test_postprocessing_clips_the_maximum_without_changing_raw_predictions(
    raw_predictions, expected_spo2
):
    """验证最大值聚合后限幅到 [90, 100]，窗口原值保持不变。"""
    values = np.asarray(raw_predictions, dtype=np.float32)

    result = postprocess_predictions(values, expected_window_count=2)

    assert result.window_predictions == tuple(float(value) for value in values)
    assert result.spo2 == expected_spo2


def test_postprocessing_rejects_invalid_expected_window_count():
    """验证预期窗口数必须是正整数。"""
    for expected_window_count in (0, -1, True):
        with pytest.raises(InferenceError) as invalid_output:
            postprocess_predictions(np.asarray([95.0], dtype=np.float32), expected_window_count)

        assert invalid_output.value.code == "RESULT.INVALID_OUTPUT"
