"""执行血压 v1 固定颜色转换、带通滤波和总体标准化。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    LoadedBPInput,
)
from health_inference.exceptions import InputError, InputErrorReason


@dataclass(frozen=True, slots=True)
class PreprocessedBP:
    """保存本次调用的原始时间轴、清洗波形、采样率和资料快照。"""

    ppg_timestamps_ms: tuple[float, ...]  # PPG 原始毫秒时间轴，不重采样或归零。
    scg_timestamps_ms: tuple[float, ...]  # ACC 原始毫秒时间轴，不对齐 PPG 起点。
    ppg_signal: tuple[float, ...]  # 旧颜色转换、0.75–4 Hz 滤波并 z-score 的 PPG。
    scg_signal: tuple[float, ...]  # 负 accZ 经旧 SCG 带通滤波并 z-score 的波形。
    ppg_sampling_rate_hz: float  # 旧 calc_fs 两位小数采样率。
    scg_sampling_rate_hz: float  # 旧 calc_fs 两位小数采样率。
    profile: object  # 本次任务提交时冻结的资料快照。
    channel_format: str  # 原始 PPG 通道类型，供结果解释和行为验证使用。


def preprocess_bp(data: LoadedBPInput) -> PreprocessedBP:
    """按旧版公式清洗 PPG 与负 accZ，不增加 SCG 质量门禁。"""
    import numpy as np
    from scipy.signal import butter, filtfilt

    if data.ppg.channel_format == "rgb":
        raw_ppg = -np.asarray(
            [channels[0] for channels in data.ppg.channels], dtype=np.float64
        ) / 255.0
    else:
        y_channel = np.asarray(
            [channels[0] for channels in data.ppg.channels], dtype=np.float64
        )
        u_channel = np.asarray(
            [channels[1] for channels in data.ppg.channels], dtype=np.float64
        )
        v_channel = np.asarray(
            [channels[2] for channels in data.ppg.channels], dtype=np.float64
        )
        matrix = np.asarray(
            (
                (1.0, 0.0, 1.5748),
                (1.0, -0.1873, -0.4681),
                (1.0, 1.8556, 0.0),
            ),
            dtype=np.float64,
        )
        yuv = np.asarray((y_channel, u_channel - 128.0, v_channel - 128.0))
        raw_ppg = -(matrix @ yuv)[0] / 255.0

    raw_scg = -np.asarray([channels[2] for channels in data.acc.channels], dtype=np.float64)
    ppg_signal = _filter_and_standardize(
        raw_ppg,
        data.ppg_sampling_rate_hz,
        lowcut_hz=0.75,
        highcut_hz=4.0,
        btype="band",
        butter=butter,
        filtfilt=filtfilt,
        numpy=np,
    )
    scg_highcut_hz = min(25.0, float(math.floor(data.acc.sampling_rate_hz / 2.0)))
    scg_signal = _filter_and_standardize(
        raw_scg,
        data.acc.sampling_rate_hz,
        lowcut_hz=1.0,
        highcut_hz=scg_highcut_hz,
        btype="bandpass",
        butter=butter,
        filtfilt=filtfilt,
        numpy=np,
    )
    return PreprocessedBP(
        ppg_timestamps_ms=data.ppg.timestamps_ms,
        scg_timestamps_ms=data.acc.timestamps_ms,
        ppg_signal=ppg_signal,
        scg_signal=scg_signal,
        ppg_sampling_rate_hz=data.ppg_sampling_rate_hz,
        scg_sampling_rate_hz=data.acc.sampling_rate_hz,
        profile=data.profile,
        channel_format=data.ppg.channel_format,
    )


def _filter_and_standardize(
    signal,
    sampling_rate_hz: float,
    *,
    lowcut_hz: float,
    highcut_hz: float,
    btype: str,
    butter,
    filtfilt,
    numpy,
) -> tuple[float, ...]:
    """执行旧版二阶 Butterworth 带通和总体标准差 z-score。"""
    nyquist_hz = sampling_rate_hz / 2.0
    if (
        not math.isfinite(sampling_rate_hz)
        or sampling_rate_hz <= 0
        or lowcut_hz >= highcut_hz
        or highcut_hz >= nyquist_hz
    ):
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    b, a = butter(
        2,
        (lowcut_hz / nyquist_hz, highcut_hz / nyquist_hz),
        btype=btype,
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
