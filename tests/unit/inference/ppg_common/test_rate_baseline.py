"""验证 HR/RSP 速率估计沿用实际输入与旧链采样率规则。"""

import math

import pytest
from health_inference.algorithms.ppg_active_offline_hr_v1.inference import estimate_hr
from health_inference.algorithms.ppg_active_offline_br_v1.inference import estimate_br
from health_inference.utils.ppg_preprocessing import preprocess_ppg
from health_inference.utils.ppg_input import PPGInput


def _estimate(heart_rate_hz: float, sampling_rate_hz: float):
    """计算指定心率频率和采样率的确定性合成信号。"""
    sample_count = int(60 * sampling_rate_hz)
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    channels = tuple(
        (
            120.0
            + 4.0 * math.sin(2.0 * math.pi * heart_rate_hz * index / sampling_rate_hz)
            + 2.0 * math.sin(2.0 * math.pi * 0.2 * index / sampling_rate_hz),
            128.0,
            136.0,
        )
        for index in range(sample_count)
    )
    prepared = preprocess_ppg(PPGInput(
        timestamps_ms=timestamps_ms,
        channels=channels,
        channel_format="rgb",
        sampling_rate_hz=sampling_rate_hz,
    ))
    return estimate_hr(prepared), estimate_br(prepared)


def test_rate_estimates_follow_two_distinct_heart_components_and_old_baseline():
    """两种确定性心率信号产生各自估计并与旧链结果相符。"""
    slower = _estimate(1.2, 30.0)
    faster = _estimate(1.5, 30.0)

    assert slower[0] == pytest.approx(72.0, abs=0.2)
    assert faster[0] == pytest.approx(90.0, abs=0.2)
    assert slower[1] == pytest.approx(12.03265584092234, abs=0.05)
    assert faster[1] == pytest.approx(12.034994390836363, abs=0.05)
    assert slower[0] != faster[0]


def test_rate_functions_keep_legacy_integer_sampling_rate_interpretation():
    """非整数采样率仍按旧链传给 NeuroKit2 的 int(fs) 解释。"""
    result = _estimate(1.2, 29.5)

    assert result[0] == pytest.approx(70.78411130202367, abs=0.05)
    assert result[1] == pytest.approx(11.817181891662122, abs=0.05)
