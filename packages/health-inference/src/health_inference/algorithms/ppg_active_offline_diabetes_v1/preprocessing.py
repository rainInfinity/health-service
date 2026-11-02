"""按糖尿病旧版规则生成负红通道滤波与标准化波形。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    PPG_FILTER_HIGH_HZ,
    PPG_FILTER_LOW_HZ,
    PPG_FILTER_ORDER,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PreprocessedDiabetes:
    """保存本次 PPG 的原始时间轴、清洗波形和采样信息。"""

    timestamps_ms: tuple[float, ...]  # 与输入逐帧对应的原始毫秒时间戳。
    ppg_signal: NDArray[np.float64]  # 负红通道经旧带通滤波和总体标准差 Z-score。
    sampling_rate_hz: float  # 沿用旧实现的两位小数采样率。
    channel_format: str  # 本次输入实际提供的 RGB 或 YUV 通道组。


def preprocess_ppg(data: PPGInput) -> PreprocessedDiabetes:
    """执行旧颜色转换、二阶带通与总体标准差标准化。"""
    import numpy as np
    from scipy.signal import butter, filtfilt

    sample_count = len(data.timestamps_ms)
    if sample_count != len(data.channels):
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    sampling_rate_hz = data.sampling_rate_hz
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 2.0 * PPG_FILTER_HIGH_HZ:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    channels = np.asarray(data.channels, dtype=np.float64)
    if channels.shape != (sample_count, 3):
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    if not np.isfinite(channels).all():
        raise InputError(InputErrorReason.NON_FINITE_VALUE)

    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        if data.channel_format == "rgb":
            raw_ppg = -channels[:, 0] / 255.0
        elif data.channel_format == "yuv":
            yuv_to_rgb = np.asarray(
                (
                    (1.0, 0.0, 1.5748),
                    (1.0, -0.1873, -0.4681),
                    (1.0, 1.8556, 0.0),
                ),
                dtype=np.float64,
            )
            centered_yuv = np.vstack(
                (
                    channels[:, 0],
                    channels[:, 1] - 128.0,
                    channels[:, 2] - 128.0,
                )
            )
            raw_ppg = -(yuv_to_rgb @ centered_yuv)[0] / 255.0
        else:
            raise InputError(InputErrorReason.INVALID_CHANNELS)

    if not np.isfinite(raw_ppg).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")

    nyquist_hz = sampling_rate_hz / 2.0
    coefficients_b, coefficients_a = butter(
        PPG_FILTER_ORDER,
        (PPG_FILTER_LOW_HZ / nyquist_hz, PPG_FILTER_HIGH_HZ / nyquist_hz),
        btype="band",
    )
    try:
        filtered_ppg = filtfilt(coefficients_b, coefficients_a, raw_ppg)
    except ValueError as error:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA) from error

    standard_deviation = float(np.std(filtered_ppg))
    if not math.isfinite(standard_deviation) or standard_deviation <= 0.0:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")
    ppg_signal = (filtered_ppg - np.mean(filtered_ppg)) / standard_deviation
    if not np.isfinite(ppg_signal).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")

    return PreprocessedDiabetes(
        timestamps_ms=data.timestamps_ms,
        ppg_signal=ppg_signal,
        sampling_rate_hz=sampling_rate_hz,
        channel_format=data.channel_format,
    )
