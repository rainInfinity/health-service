"""读取并校验糖尿病风险 v1 的单块 PPG 输入。"""

from __future__ import annotations

import math
from pathlib import Path

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    PPG_FILTER_HIGH_HZ,
    PYPPG_MINIMUM_DURATION_SECONDS,
)
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput, load_ppg


def load_diabetes_ppg(path: Path) -> PPGInput:
    """复用共享 CSV 校验并检查滤波和 pyPPG 的执行边界。"""
    loaded = load_ppg(path)
    sample_count = len(loaded.timestamps_ms)
    sampling_rate_hz = loaded.sampling_rate_hz
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 2.0 * PPG_FILTER_HIGH_HZ:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    if sample_count < math.ceil(sampling_rate_hz * PYPPG_MINIMUM_DURATION_SECONDS):
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)
    return loaded
