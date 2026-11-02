"""验证糖尿病 v1 的负红通道、YUV 转换与旧滤波行为。"""

import math

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    preprocess_ppg,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput
from scipy.signal import butter, filtfilt


def _signal_input(channel_format: str, sample_count: int = 600) -> PPGInput:
    """创建固定 30 Hz、含脉搏与漂移分量的 RGB/YUV 数据。"""
    sampling_rate_hz = 30.0
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    channels = []
    for index in range(sample_count):
        seconds = index / sampling_rate_hz
        pulse = 8.0 * math.sin(2.0 * math.pi * 1.2 * seconds)
        drift = 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
        value = 120.0 + pulse + drift
        channels.append(
            (value, 128.0 + pulse / 2.0, 136.0 - pulse / 3.0)
            if channel_format == "rgb"
            else (value, 128.0 + pulse / 2.0, 128.0 - pulse / 3.0)
        )
    return PPGInput(
        timestamps_ms=timestamps_ms,
        channels=tuple(channels),
        channel_format=channel_format,
        sampling_rate_hz=sampling_rate_hz,
    )


def _legacy_yuv_red(channels: np.ndarray) -> np.ndarray:
    """按旧 BT.709 矩阵转换 YUV 并提取归一化红通道。"""
    matrix = np.asarray(
        (
            (1.0, 0.0, 1.5748),
            (1.0, -0.1873, -0.4681),
            (1.0, 1.8556, 0.0),
        ),
        dtype=np.float64,
    )
    centered = np.vstack((channels[:, 0], channels[:, 1] - 128.0, channels[:, 2] - 128.0))
    return (matrix @ centered)[0] / 255.0


@pytest.mark.parametrize("channel_format", ("rgb", "yuv"))
def test_preprocessing_matches_legacy_filter_and_population_zscore(channel_format: str):
    """验证两种颜色格式产生与旧链一致的二阶带通和总体 Z-score。"""
    data = _signal_input(channel_format)
    channels = np.asarray(data.channels, dtype=np.float64)
    raw_ppg = -channels[:, 0] / 255.0 if channel_format == "rgb" else -_legacy_yuv_red(channels)
    coefficients_b, coefficients_a = butter(
        2,
        (0.75 / 15.0, 4.0 / 15.0),
        btype="band",
    )
    filtered = filtfilt(coefficients_b, coefficients_a, raw_ppg)
    expected = (filtered - np.mean(filtered)) / np.std(filtered)

    prepared = preprocess_ppg(data)

    np.testing.assert_allclose(prepared.ppg_signal, expected, rtol=1e-12, atol=1e-12)
    assert prepared.timestamps_ms == data.timestamps_ms
    assert prepared.sampling_rate_hz == 30.0
    assert prepared.channel_format == channel_format


def test_preprocessing_rejects_invalid_rate_channels_and_degenerate_waveform():
    """预处理入口再次验证通道契约和零方差标准化边界。"""
    data = _signal_input("rgb")
    invalid_rate = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=data.channels,
        channel_format="rgb",
        sampling_rate_hz=8.0,
    )
    invalid_channels = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=((120.0, 128.0),) * len(data.timestamps_ms),
        channel_format="rgb",
        sampling_rate_hz=30.0,
    )
    constant = PPGInput(
        timestamps_ms=data.timestamps_ms,
        channels=((0.0, 0.0, 0.0),) * len(data.timestamps_ms),
        channel_format="rgb",
        sampling_rate_hz=30.0,
    )

    with pytest.raises(InputError) as rate_error:
        preprocess_ppg(invalid_rate)
    with pytest.raises(InputError) as channel_error:
        preprocess_ppg(invalid_channels)
    with pytest.raises(InferenceError) as constant_error:
        preprocess_ppg(constant)

    assert rate_error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE
    assert channel_error.value.reason is InputErrorReason.INVALID_CHANNELS
    assert constant_error.value.code == "INFERENCE.EXECUTION_ERROR"
