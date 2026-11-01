"""验证血氧 v1 的稳定特征筛选和任务内归一化。"""

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.inference import (
    _standardize_task_matrix,
    prepare_model_input,
)
from health_inference.exceptions import InferenceError


def test_model_input_matches_legacy_stable_order_and_minmax_matrix():
    """验证稳定列顺序及旧链 StandardScaler 后的逐列归一化结果。"""
    full_matrix = np.asarray(
        [[0.0, 9.0, 4.0], [1.0, 8.0, 7.0], [4.0, 7.0, 9.0], [9.0, 6.0, 16.0]],
        dtype=np.float32,
    )

    model_input = prepare_model_input(full_matrix, (2, 0))

    expected = np.asarray(
        [[0.0, 0.0], [0.25, 1.0 / 9.0], [5.0 / 12.0, 4.0 / 9.0], [1.0, 1.0]],
        dtype=np.float32,
    )
    assert model_input.dtype == np.float32
    np.testing.assert_allclose(model_input, expected, atol=1e-7, rtol=1e-7)


def test_single_window_and_constant_columns_normalize_to_zero():
    """验证单窗口及常量列按旧公式归一化为零。"""
    single_window = prepare_model_input(
        np.asarray([[7.0, 2.0, 11.0]], dtype=np.float32), (2, 0)
    )
    constant_column = prepare_model_input(
        np.asarray([[5.0, 8.0], [7.0, 8.0], [9.0, 8.0]], dtype=np.float32),
        (1, 0),
    )

    np.testing.assert_array_equal(single_window, np.zeros((1, 2), dtype=np.float32))
    np.testing.assert_array_equal(constant_column[:, 0], np.zeros(3, dtype=np.float32))
    np.testing.assert_allclose(constant_column[:, 1], [0.0, 0.5, 1.0], atol=1e-7)


def test_standardization_fits_each_task_without_reusing_statistics():
    """验证先处理不同任务后，当前矩阵仍按自身均值和方差标准化。"""
    first_task = np.asarray([[-2.0, 7.0], [0.0, 7.0], [2.0, 7.0]], dtype=np.float32)
    second_task = np.asarray([[4.0, -3.0], [5.0, -3.0], [9.0, -3.0]], dtype=np.float32)

    _standardize_task_matrix(first_task)
    standardized = _standardize_task_matrix(second_task)

    np.testing.assert_allclose(np.mean(standardized, axis=0), [0.0, 0.0], atol=1e-6)
    np.testing.assert_allclose(np.std(standardized, axis=0), [1.0, 0.0], atol=1e-6)


def test_model_input_rejects_invalid_matrix_and_stable_indices():
    """验证非有限矩阵和错误稳定索引不会进入模型。"""
    valid_matrix = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    with pytest.raises(InferenceError) as non_finite:
        prepare_model_input(np.asarray([[1.0, np.nan]], dtype=np.float32), (0,))
    assert non_finite.value.code == "INFERENCE.EXECUTION_ERROR"

    for indices in ((), (0, 0), (0, 2)):
        with pytest.raises(InferenceError) as invalid_indices:
            prepare_model_input(valid_matrix, indices)
        assert invalid_indices.value.code == "INFERENCE.INVALID_ARTIFACT"
