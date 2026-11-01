"""验证血氧 v1 颜色转换、负红波形和预处理边界。"""

import math

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    preprocess_ppg,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput
from scipy.signal import butter, filtfilt


def _signal_input(channel_format: str, sample_count: int = 600) -> PPGInput:
    """创建具有固定采样率与脉搏分量的确定性 RGB/YUV 数据。"""
    sampling_rate_hz = 30.0
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    channels = []
    for index in range(sample_count):
        time_seconds = index / sampling_rate_hz
        pulse = 8.0 * math.sin(2.0 * math.pi * 1.2 * time_seconds)
        if channel_format == "rgb":
            channels.append((120.0 + pulse, 130.0 + pulse / 2.0, 140.0 - pulse / 3.0))
        else:
            channels.append((120.0 + pulse, 130.0 + pulse / 2.0, 140.0 - pulse / 3.0))
    return PPGInput(
        timestamps_ms=timestamps_ms,
        channels=tuple(channels),
        channel_format=channel_format,
        sampling_rate_hz=sampling_rate_hz,
    )


def _legacy_yuv_rgb(channels: np.ndarray) -> np.ndarray:
    """按旧 BT.709 矩阵转换 YUV 并归一化至 RGB。"""
    matrix = np.asarray(
        (
            (1.0, 0.0, 1.5748),
            (1.0, -0.1873, -0.4681),
            (1.0, 1.8556, 0.0),
        ),
        dtype=np.float64,
    )
    centered = np.vstack((channels[:, 0], channels[:, 1] - 128.0, channels[:, 2] - 128.0))
    return (matrix @ centered).T / 255.0


@pytest.mark.parametrize("channel_format", ["rgb", "yuv"])
def test_preprocessing_preserves_legacy_color_scale_and_filtered_ppg(channel_format: str):
    """RGB 保留原量级，YUV 按旧矩阵转换，负红波形与旧滤波一致。"""
    data = _signal_input(channel_format)
    original_channels = np.asarray(data.channels, dtype=np.float64)

    result = preprocess_ppg(data)

    expected_rgb = (
        original_channels
        if channel_format == "rgb"
        else _legacy_yuv_rgb(original_channels)
    )
    raw_ppg = (
        -original_channels[:, 0] / 255.0
        if channel_format == "rgb"
        else -expected_rgb[:, 0]
    )
    nyquist_hz = data.sampling_rate_hz / 2.0
    coefficients_b, coefficients_a = butter(
        2,
        (0.75 / nyquist_hz, 4.0 / nyquist_hz),
        btype="band",
    )
    filtered = filtfilt(coefficients_b, coefficients_a, raw_ppg)
    expected_ppg = (filtered - np.mean(filtered)) / np.std(filtered)

    np.testing.assert_allclose(result.feature_rgb, expected_rgb, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(result.ppg_signal, expected_ppg, rtol=1e-12, atol=1e-12)
    assert result.sampling_rate_hz == data.sampling_rate_hz
    assert result.channel_format == channel_format


def test_preprocessing_rejects_non_finite_and_non_executable_data():
    """直接调用预处理时也拒绝无效采样率、通道形状和非有限值。"""
    data = _signal_input("rgb")
    invalid_rate = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=data.channels,
        channel_format="rgb",
        sampling_rate_hz=8.0,
    )
    invalid_value_channels = ((float("nan"), 128.0, 136.0),) + data.channels[1:]
    invalid_values = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=invalid_value_channels,
        channel_format="rgb",
        sampling_rate_hz=data.sampling_rate_hz,
    )
    invalid_shape = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=((120.0, 128.0),) * len(data.timestamps_ms),
        channel_format="rgb",
        sampling_rate_hz=data.sampling_rate_hz,
    )

    with pytest.raises(InputError) as rate_error:
        preprocess_ppg(invalid_rate)
    with pytest.raises(InputError) as value_error:
        preprocess_ppg(invalid_values)
    with pytest.raises(InputError) as shape_error:
        preprocess_ppg(invalid_shape)

    assert rate_error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE
    assert value_error.value.reason is InputErrorReason.NON_FINITE_VALUE
    assert shape_error.value.reason is InputErrorReason.INVALID_CHANNELS


def test_preprocessing_rejects_degenerate_normalization():
    """零方差波形不生成含 NaN 的质量输入。"""
    data = PPGInput(
        timestamps_ms=tuple(index * 1000.0 / 30.0 for index in range(600)),
        channels=((0.0, 128.0, 136.0),) * 600,
        channel_format="rgb",
        sampling_rate_hz=30.0,
    )

    with pytest.raises(InferenceError) as error:
        preprocess_ppg(data)

    assert error.value.code == "INFERENCE.EXECUTION_ERROR"
