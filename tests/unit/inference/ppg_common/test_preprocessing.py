"""验证独立 HR 与 BR 共用的信号提取、滤波、标准化和处理边界。"""

import math
from typing import Literal

import numpy as np
import pytest
from health_inference.utils.ppg_preprocessing import preprocess_ppg
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput


def _signal_input(
    *,
    sample_count: int = 1800,
    sampling_rate_hz: float = 30.0,
    channel_format: Literal["rgb", "yuv"] = "rgb",
) -> PPGInput:
    """构造具有固定心率与呼吸分量的确定性单块信号。"""
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    channels = []
    for index in range(sample_count):
        time_seconds = index / sampling_rate_hz
        red = (
            120.0
            + 4.0 * math.sin(2.0 * math.pi * 1.2 * time_seconds)
            + 2.0 * math.sin(2.0 * math.pi * 0.2 * time_seconds)
        )
        if channel_format == "rgb":
            channels.append((red, 128.0, 136.0))
        else:
            channels.append((red - 1.5748 * 2.0, 128.0, 130.0))
    return PPGInput(
        timestamps_ms=timestamps_ms,
        channels=tuple(channels),
        channel_format=channel_format,
        sampling_rate_hz=sampling_rate_hz,
    )


def test_rgb_and_bt709_yuv_produce_matching_old_chain_waveforms():
    """等价 RGB/YUV 输入产生与旧链固定数值基线一致的两组波形。"""
    rgb = preprocess_ppg(_signal_input(channel_format="rgb"))
    yuv = preprocess_ppg(_signal_input(channel_format="yuv"))

    assert rgb.channel_format == "rgb"
    assert yuv.channel_format == "yuv"
    assert rgb.sampling_rate_hz == yuv.sampling_rate_hz == 30.0
    assert np.asarray(rgb.ppg_signal) == pytest.approx(yuv.ppg_signal, abs=1e-10)
    assert np.asarray(rgb.rsp_signal) == pytest.approx(yuv.rsp_signal, abs=1e-10)
    assert [rgb.ppg_signal[index] for index in (0, 1, 15, 100, 700, 900, 1799)] == pytest.approx(
        [
            -0.09888018222799277,
            -0.43823627270288346,
            0.8477858005440223,
            0.0015202283494084045,
            0.0015135623464172597,
            -9.145959479162958e-06,
            0.3937846779724898,
        ],
        abs=1e-10,
    )
    assert [rgb.rsp_signal[index] for index in (0, 1, 15, 100, 700, 900, 1799)] == pytest.approx(
        [
            0.6380008789317666,
            0.5792624078280739,
            -0.26421082197725865,
            1.2671269418350466,
            1.249632968054734,
            0.0073366367665464215,
            0.1332530566216646,
        ],
        abs=1e-10,
    )


def test_rejects_signal_that_cannot_meet_filter_preconditions():
    """低于 Nyquist 前提或短于 filtfilt 填充长度时返回受控输入错误。"""
    with pytest.raises(InputError) as sampling_error:
        preprocess_ppg(_signal_input(sampling_rate_hz=8.0))
    assert sampling_error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE

    with pytest.raises(InputError) as length_error:
        preprocess_ppg(_signal_input(sample_count=15))
    assert length_error.value.reason is InputErrorReason.INSUFFICIENT_DATA


def test_preprocessing_does_not_require_an_extra_minimum_capture_duration():
    """刚超过实际滤波填充长度的数据可处理，不另设最短采集秒数。"""
    result = preprocess_ppg(_signal_input(sample_count=16))

    assert len(result.ppg_signal) == len(result.rsp_signal) == 16
    assert all(math.isfinite(value) for value in result.ppg_signal)
    assert all(math.isfinite(value) for value in result.rsp_signal)
