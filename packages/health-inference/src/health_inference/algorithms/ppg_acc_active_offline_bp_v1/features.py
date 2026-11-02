"""实现旧版峰值配对、PTT/PR 计算及六维模型特征。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    BPProfileSnapshot,
)
from health_inference.exceptions import InferenceError


def find_ppg_peaks(
    cleaned_ppg: Sequence[float],
    sampling_rate_hz: float,
    *,
    max_hr_bpm: int = 120,
    min_prominence: float = 0.6,
):
    """按旧最大心率距离和 prominence 检测负清洗 PPG 的峰值索引。"""
    import numpy as np
    from scipy.signal import find_peaks

    signal = np.asarray(cleaned_ppg, dtype=np.float64)
    minimum_distance = int((60.0 / max_hr_bpm) * sampling_rate_hz)
    peaks, _ = find_peaks(
        -signal,
        distance=minimum_distance,
        prominence=min_prominence,
    )
    return peaks


def find_ao_peaks(
    ao_signal: Sequence[float],
    sampling_rate_hz: float,
    *,
    max_hr_bpm: int = 120,
    search_window_ms: int = 400,
):
    """在旧 Hilbert 包络中心附近回溯选择最显著局部 AO 峰。"""
    import neurokit2 as nk
    import numpy as np
    from scipy.signal import find_peaks, hilbert

    signal = np.asarray(ao_signal, dtype=np.float64)
    minimum_distance = int((60.0 / max_hr_bpm) * sampling_rate_hz)
    if signal.size < minimum_distance * 2:
        return np.array([], dtype=np.int64)

    envelope = np.abs(hilbert(signal))
    smoothed_envelope = nk.signal_filter(
        envelope,
        sampling_rate=sampling_rate_hz,
        lowcut=0.5,
        highcut=3.0,
        method="butterworth",
        order=4,
    )
    centers, _ = find_peaks(
        smoothed_envelope,
        distance=minimum_distance,
        height=np.mean(smoothed_envelope),
    )
    search_window_samples = int(search_window_ms * sampling_rate_hz / 1000.0)
    ao_indices = []
    for center in centers:
        start = max(0, int(center) - search_window_samples)
        end = min(signal.size, int(center) + search_window_samples)
        window = signal[start:end]
        if window.size == 0:
            continue
        local_peaks, _ = find_peaks(
            window,
            prominence=0.4,
            distance=minimum_distance,
        )
        if local_peaks.size:
            best_peak = local_peaks[np.argmax(window[local_peaks])]
            ao_indices.append(start + int(best_peak))
    return np.asarray(ao_indices, dtype=np.int64)


def pair_aos_by_peaks(
    ao_times_ms: Sequence[int],
    ppg_peak_times_ms: Sequence[int],
    *,
    window_ms: tuple[int, int] = (50, 500),
):
    """为每个 PPG 峰选择峰前含边界窗口内最近的 AO 时间戳。"""
    import numpy as np

    ao_times = np.asarray(ao_times_ms, dtype=np.int64)
    peak_times = np.asarray(ppg_peak_times_ms, dtype=np.int64)
    minimum_ms, maximum_ms = window_ms
    paired_aos = []
    paired_peaks = []
    for peak_time in peak_times:
        window_start = peak_time - maximum_ms
        window_end = peak_time - minimum_ms
        candidates = ao_times[
            (ao_times >= window_start) & (ao_times <= window_end)
        ]
        if candidates.size:
            closest_ao = candidates[np.argmin(np.abs(candidates - peak_time))]
            paired_aos.append(closest_ao)
            paired_peaks.append(peak_time)
    return np.asarray(paired_aos, dtype=np.int64), np.asarray(
        paired_peaks, dtype=np.int64
    )


def calculate_ptt_ms(
    paired_peak_times_ms: Sequence[int],
    paired_ao_times_ms: Sequence[int],
) -> float | None:
    """执行旧版严格 IQR 筛选、绝对均值和六位小数规则。"""
    import numpy as np

    if len(paired_peak_times_ms) == 0 or len(paired_ao_times_ms) == 0:
        return None
    ptt_values = np.asarray(paired_peak_times_ms) - np.asarray(paired_ao_times_ms)
    first_quartile = np.percentile(ptt_values, 25)
    third_quartile = np.percentile(ptt_values, 75)
    interquartile_range = third_quartile - first_quartile
    lower_bound = first_quartile - 1.5 * interquartile_range
    upper_bound = third_quartile + 1.5 * interquartile_range
    filtered = ptt_values[(ptt_values > lower_bound) & (ptt_values < upper_bound)]
    mean_ptt = np.mean(np.abs(filtered))
    return round(float(mean_ptt), 6)


def calculate_pr(
    peak_count: int,
    ppg_timestamps_ms: Sequence[float],
) -> int:
    """按 PPG 向上取整秒时长计算并整数化旧版脉率。"""
    duration_seconds = math.ceil(
        (ppg_timestamps_ms[-1] - ppg_timestamps_ms[0]) / 1000.0
    )
    return int(peak_count / (duration_seconds / 60.0))


def build_feature_vector(
    profile: BPProfileSnapshot,
    ptt_ms: float | int,
    pr: int,
    normalization: Mapping[str, Mapping[str, float]],
):
    """按旧顺序将 PTT、年龄、PR、BMI、身高米和原始性别组成六维向量。"""
    import numpy as np

    try:
        age = float(profile.age_years) if profile.age_years is not None else 0.0
        height_cm = float(profile.height_cm) if profile.height_cm is not None else 0.0
        weight_kg = float(profile.weight_kg) if profile.weight_kg is not None else 0.0
    except (TypeError, ValueError, OverflowError) as error:
        raise InferenceError("INFERENCE.INVALID_PROFILE", "提交资料快照无效") from error
    if not all(math.isfinite(value) for value in (age, height_cm, weight_kg)):
        raise InferenceError("INFERENCE.INVALID_PROFILE", "提交资料快照无效")

    ptt_seconds = float(ptt_ms) / 1000.0 if ptt_ms != 0 else 0.0
    height_m = height_cm / 100.0 if height_cm != 0 else 0.0
    bmi = weight_kg / (height_m**2) if height_m != 0 else 0.0
    if not math.isfinite(bmi):
        raise InferenceError("INFERENCE.INVALID_PROFILE", "提交资料快照无效")

    normalized = []
    for name, value in (
        ("X1", ptt_seconds),
        ("age", age),
        ("pr", float(pr)),
        ("bmi", bmi),
        ("height", height_m),
    ):
        parameters = normalization[name]
        mean = parameters["miu"]
        standard_deviation = parameters["segma"]
        # 兼容旧 z_score 中零均值被 `or` 当成缺省值的行为。
        center = mean or value
        normalized.append((np.asarray([value], dtype=np.float64) - center) / standard_deviation)

    sex = np.asarray([profile.gender])
    return np.column_stack((*normalized, sex))
