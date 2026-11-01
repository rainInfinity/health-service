"""按旧血氧链转换颜色、提取负红 PPG 并执行带通标准化。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from health_inference.algorithms.ppg_active_offline_spo2_v1.constants import (
    PPG_FILTER_HIGH_HZ,
    PPG_FILTER_LOW_HZ,
    WINDOW_SIZE,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PreprocessedSpo2:
    """保存任务内颜色特征输入、标准化 PPG 和采样率。"""

    feature_rgb: NDArray[np.float64]  # [样本数, 3]；RGB 保留原量级，YUV 转为归一化 RGB。
    ppg_signal: NDArray[np.float64]  # BT.709 负红通道经带通和总体标准差 Z-score。
    sampling_rate_hz: float  # 输入时间轴均值采样率，沿用两位小数规则。
    channel_format: str  # 保留输入的 RGB 或 YUV 来源。


def preprocess_ppg(data: PPGInput) -> PreprocessedSpo2:
    """保留训练相关颜色量级并生成质量评价使用的处理后负红波形。"""
    import numpy as np
    from scipy.signal import butter, filtfilt

    sample_count = len(data.timestamps_ms)
    if sample_count < WINDOW_SIZE or len(data.channels) != sample_count:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)
    sampling_rate_hz = data.sampling_rate_hz
    if (
        not math.isfinite(sampling_rate_hz)
        or sampling_rate_hz <= 2.0 * PPG_FILTER_HIGH_HZ
    ):
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    channels = np.asarray(data.channels, dtype=np.float64)
    if channels.shape != (sample_count, 3):
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    if not np.isfinite(channels).all():
        raise InputError(InputErrorReason.NON_FINITE_VALUE)

    if data.channel_format == "rgb":
        feature_rgb = channels
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
            (channels[:, 0], channels[:, 1] - 128.0, channels[:, 2] - 128.0)
        )
        with np.errstate(over="ignore", invalid="ignore"):
            feature_rgb = (yuv_to_rgb @ centered_yuv).T / 255.0
        raw_ppg = -feature_rgb[:, 0]
    else:
        raise InputError(InputErrorReason.INVALID_CHANNELS)

    if not np.isfinite(feature_rgb).all() or not np.isfinite(raw_ppg).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")

    nyquist_hz = sampling_rate_hz / 2.0
    coefficients_b, coefficients_a = butter(
        2,
        (PPG_FILTER_LOW_HZ / nyquist_hz, PPG_FILTER_HIGH_HZ / nyquist_hz),
        btype="band",
    )
    filtered_ppg = filtfilt(coefficients_b, coefficients_a, raw_ppg)
    standard_deviation = float(np.std(filtered_ppg))
    if not math.isfinite(standard_deviation) or standard_deviation <= 0.0:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")
    ppg_signal = (filtered_ppg - np.mean(filtered_ppg)) / standard_deviation
    if not np.isfinite(ppg_signal).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 预处理结果无效")

    return PreprocessedSpo2(
        feature_rgb=feature_rgb,
        ppg_signal=ppg_signal,
        sampling_rate_hz=sampling_rate_hz,
        channel_format=data.channel_format,
    )
