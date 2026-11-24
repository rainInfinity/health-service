"""复用心率和呼吸率的负红通道提取、带通滤波及标准化规则。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from health_inference.exceptions import InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PreprocessedPPG:
    """保存两项独立估计采用的同源预处理波形。"""

    timestamps_ms: tuple[float, ...]  # 输入文件中的原始毫秒时间戳。
    ppg_signal: tuple[float, ...]  # 0.75–4 Hz 带通并标准化后的负红脉搏波。
    rsp_signal: tuple[float, ...]  # 0.1–0.5 Hz 带通并标准化后的呼吸波。
    sampling_rate_hz: float  # 按输入时间轴计算并保留两位小数的采样率。
    channel_format: str  # 原始完整通道组，不改变通道解释。


def preprocess_ppg(data: PPGInput) -> PreprocessedPPG:
    """沿用原有数值规则，为独立 HR 和 BR 提供相同的预处理结果。"""
    import numpy as np
    from scipy.signal import butter, filtfilt

    if data.channel_format == "rgb":
        raw_signal = (
            -np.asarray([channels[0] for channels in data.channels], dtype=np.float64) / 255.0
        )
    else:
        y_channel = np.asarray([channels[0] for channels in data.channels], dtype=np.float64)
        v_channel = np.asarray([channels[2] for channels in data.channels], dtype=np.float64)
        raw_signal = -(y_channel + 1.5748 * (v_channel - 128.0)) / 255.0

    ppg_signal = _filter_and_standardize(
        raw_signal,
        data.sampling_rate_hz,
        lowcut_hz=0.75,
        highcut_hz=4.0,
        butter=butter,
        filtfilt=filtfilt,
        numpy=np,
    )
    rsp_signal = _filter_and_standardize(
        raw_signal,
        data.sampling_rate_hz,
        lowcut_hz=0.1,
        highcut_hz=0.5,
        butter=butter,
        filtfilt=filtfilt,
        numpy=np,
    )
    return PreprocessedPPG(
        timestamps_ms=data.timestamps_ms,
        ppg_signal=ppg_signal,
        rsp_signal=rsp_signal,
        sampling_rate_hz=data.sampling_rate_hz,
        channel_format=data.channel_format,
    )


def _filter_and_standardize(
    signal: NDArray[np.float64],
    sampling_rate_hz: float,
    *,
    lowcut_hz: float,
    highcut_hz: float,
    butter,
    filtfilt,
    numpy,
) -> tuple[float, ...]:
    """执行原有二阶 Butterworth 带通和总体标准差 Z-score。"""
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    nyquist_hz = sampling_rate_hz / 2.0
    if highcut_hz >= nyquist_hz:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    b, a = butter(
        2,
        (lowcut_hz / nyquist_hz, highcut_hz / nyquist_hz),
        btype="band",
    )
    pad_length = 3 * max(len(a), len(b))
    if signal.size <= pad_length:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)

    filtered = filtfilt(b, a, signal)
    if not numpy.all(numpy.isfinite(filtered)):
        raise RuntimeError("滤波输出包含非有限值")
    mean = float(numpy.mean(filtered))
    standard_deviation = float(numpy.std(filtered))
    if standard_deviation == 0:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)
    if not math.isfinite(mean) or not math.isfinite(standard_deviation):
        raise RuntimeError("滤波输出不能标准化")

    normalized = (filtered - mean) / standard_deviation
    if not numpy.all(numpy.isfinite(normalized)):
        raise RuntimeError("标准化输出包含非有限值")
    return tuple(float(value) for value in normalized)
