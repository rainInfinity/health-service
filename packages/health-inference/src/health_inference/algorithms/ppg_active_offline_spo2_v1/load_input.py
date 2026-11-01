"""校验血氧 v1 支持的单块 RGB/YUV PPG 输入。"""

from pathlib import Path

from health_inference.algorithms.ppg_active_offline_spo2_v1.constants import (
    PPG_FILTER_HIGH_HZ,
    WINDOW_SIZE,
)
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput, load_ppg


def load_spo2_ppg(path: Path) -> PPGInput:
    """读取单块 PPG，并拒绝短于固定窗口或无法通过滤波器的采样率。"""
    data = load_ppg(path)
    if len(data.timestamps_ms) < WINDOW_SIZE:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)
    if data.sampling_rate_hz <= 2.0 * PPG_FILTER_HIGH_HZ:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    return data
