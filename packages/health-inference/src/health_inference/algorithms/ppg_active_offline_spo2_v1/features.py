"""提取血氧 v1 的九通道窗口特征与 pyPPG 生物标志物。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from health_inference.algorithms.ppg_active_offline_spo2_v1.constants import (
    APPLY_DIFF,
    WINDOW_SIZE,
    WINDOW_STEP,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


_BIOMARKER_NAMES = (
    "Tpi",
    "Tpp",
    "Tdp",
    "IPR",
    "IPA",
    "AGI",
    "AGImod",
    "RIp1",
    "RIp2",
    "AI",
    "AUCsys",
    "Tdw10",
    "Tdw25",
    "Tdw33",
    "Tdw50",
    "Tdw66",
    "Tpw10",
    "Tpw25",
    "Tpw33",
    "Tpw50",
    "Tpw66",
    "Tpw75",
    "Tpw90",
    "Tsw10",
    "Tsw25",
    "Tsw33",
    "Tsw50",
    "Tsw66",
    "Tsw75",
    "Tsw90",
    "Tsys",
    "deltaT",
    "Tdw75",
    "Tdw90",
)
_BASIC_FEATURE_NAMES = (
    "mean",
    "std",
    "skewness",
    "kurtosis",
    "rms",
    "max",
    "min",
    "peak_to_peak",
    "dominant_freq",
    "spectral_energy",
    "spectral_entropy",
    "peak_count",
    "peak_mean",
    "peak_std",
    "peak_interval_mean",
    "peak_interval_std",
)


def _validate_feature_values(features: Mapping[str, object]) -> None:
    """拒绝非数值或非有限的特征，避免把坏值送入模型。"""
    for value in features.values():
        try:
            finite = math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征结果无效")


def extract_ac_dc(
    signal: NDArray[np.float64], sampling_rate_hz: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """按旧链的中值基线与局部分位幅值规则提取 AC/DC。"""
    import numpy as np
    from scipy.ndimage import median_filter, uniform_filter1d
    from scipy.signal import find_peaks

    values = np.asarray(signal, dtype=np.float64)
    if values.ndim != 1 or not len(values):
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    if not np.isfinite(values).all():
        raise InputError(InputErrorReason.NON_FINITE_VALUE)
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0.0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    dc_window = int(sampling_rate_hz * 2)
    if dc_window % 2 == 0:
        dc_window += 1
    dc_component = median_filter(values, size=dc_window)
    ac_raw = values - dc_component

    max_period = int(sampling_rate_hz * 60 / 40)
    min_period = int(sampling_rate_hz * 60 / 180)
    smoothed_ac = uniform_filter1d(ac_raw, size=max(int(sampling_rate_hz / 10), 1))
    peaks, _ = find_peaks(
        smoothed_ac,
        distance=min_period,
        prominence=np.std(ac_raw) * 0.5,
        height=np.mean(smoothed_ac) + np.std(smoothed_ac) * 0.3,
    )
    if len(peaks) >= 2:
        typical_interval = int(np.median(np.diff(peaks)))
        window_size = min(max(typical_interval, min_period), max_period)
    else:
        window_size = int(sampling_rate_hz * 60 / 75)
    if window_size % 2 == 0:
        window_size += 1

    ac_component = np.zeros_like(ac_raw)
    half_window = window_size // 2
    for index in range(len(ac_raw)):
        start = max(0, index - half_window)
        end = min(len(ac_raw), index + half_window + 1)
        window = ac_raw[start:end]
        if len(window) >= 2:
            q95 = np.percentile(window, 97.5)
            q5 = np.percentile(window, 2.5)
            ac_component[index] = (q95 - q5) * 0.5
    return ac_component, dc_component


def build_9ch_matrix(
    feature_rgb: NDArray[np.float64], sampling_rate_hz: float
) -> NDArray[np.float64]:
    """按旧通道顺序组合原始 RGB、AC 与 DC 信号。"""
    import numpy as np

    rgb = np.asarray(feature_rgb, dtype=np.float64)
    if rgb.ndim != 2 or rgb.shape[1] != 3 or not len(rgb):
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    if not np.isfinite(rgb).all():
        raise InputError(InputErrorReason.NON_FINITE_VALUE)
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0.0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    red_ac, red_dc = extract_ac_dc(rgb[:, 0], sampling_rate_hz)
    green_ac, green_dc = extract_ac_dc(rgb[:, 1], sampling_rate_hz)
    blue_ac, blue_dc = extract_ac_dc(rgb[:, 2], sampling_rate_hz)
    matrix = np.column_stack(
        (
            rgb[:, 0],
            rgb[:, 1],
            rgb[:, 2],
            red_ac,
            red_dc,
            green_ac,
            green_dc,
            blue_ac,
            blue_dc,
        )
    )
    if not np.isfinite(matrix).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "九通道特征无效")
    return matrix


def _basic_ppg_features(
    signal: NDArray[np.float64], sampling_rate_hz: float, prefix: str
) -> dict[str, float]:
    """提取旧链使用的时域、频域与峰值基础特征。"""
    import numpy as np
    from scipy.signal import find_peaks, welch
    from scipy.stats import kurtosis, skew

    values = np.asarray(signal, dtype=np.float32)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "基础 PPG 特征输入无效")
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0.0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    if not len(values):
        return {f"{prefix}{name}": 0.0 for name in _BASIC_FEATURE_NAMES}

    standard_deviation = float(np.std(values))
    near_constant = standard_deviation < 1e-6
    mean_value = float(np.mean(values))
    maximum = float(np.max(values))
    minimum = float(np.min(values))
    features = {
        f"{prefix}mean": mean_value,
        f"{prefix}std": standard_deviation,
        f"{prefix}skewness": (
            float(skew(values)) if len(values) > 2 and not near_constant else 0.0
        ),
        f"{prefix}kurtosis": (
            float(kurtosis(values)) if len(values) > 3 and not near_constant else 0.0
        ),
        f"{prefix}rms": float(np.sqrt(np.mean(values**2))),
        f"{prefix}max": maximum,
        f"{prefix}min": minimum,
        f"{prefix}peak_to_peak": maximum - minimum,
    }

    if len(values) >= 10 and not near_constant:
        nperseg = min(1024, 2 ** int(np.log2(len(values))))
        nperseg = max(nperseg, 32)
        frequencies, power = welch(
            values,
            fs=sampling_rate_hz,
            nperseg=nperseg,
            noverlap=nperseg // 2,
        )
        mask = (frequencies >= 0.5) & (frequencies <= 8.0)
        valid_frequencies = frequencies[mask]
        valid_power = power[mask]
        if not np.isfinite(valid_power).all():
            raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 频域特征无效")
        total_power = float(np.sum(valid_power))
        if not len(valid_power) or total_power <= 0.0:
            dominant_frequency = spectral_energy = spectral_entropy = 0.0
        else:
            frequency_step = (
                float(valid_frequencies[1] - valid_frequencies[0])
                if len(valid_frequencies) > 1
                else 1.0
            )
            normalized_power = valid_power / total_power
            dominant_frequency = float(valid_frequencies[np.argmax(valid_power)])
            spectral_energy = total_power * frequency_step
            spectral_entropy = float(
                -np.sum(normalized_power * np.log2(normalized_power + 1e-10))
            )
    else:
        dominant_frequency = spectral_energy = spectral_entropy = 0.0
    features.update(
        {
            f"{prefix}dominant_freq": dominant_frequency,
            f"{prefix}spectral_energy": spectral_energy,
            f"{prefix}spectral_entropy": spectral_entropy,
        }
    )

    peaks, _ = find_peaks(values, distance=max(int(sampling_rate_hz * 0.5), 1))
    features[f"{prefix}peak_count"] = float(len(peaks))
    if len(peaks):
        peak_values = values[peaks]
        features[f"{prefix}peak_mean"] = float(np.mean(peak_values))
        features[f"{prefix}peak_std"] = float(np.std(peak_values))
        if len(peaks) > 1:
            intervals = np.diff(peaks) / sampling_rate_hz
            features[f"{prefix}peak_interval_mean"] = float(np.mean(intervals))
            features[f"{prefix}peak_interval_std"] = float(np.std(intervals))
        else:
            features[f"{prefix}peak_interval_mean"] = 0.0
            features[f"{prefix}peak_interval_std"] = 0.0
    else:
        for name in (
            "peak_mean",
            "peak_std",
            "peak_interval_mean",
            "peak_interval_std",
        ):
            features[f"{prefix}{name}"] = 0.0

    _validate_feature_values(features)
    return features


def _extract_pyppg_features(
    signal: NDArray[np.float64], sampling_rate_hz: float, prefix: str
) -> dict[str, float] | None:
    """计算非退化信号的 pyPPG 生物标志物，空特征点返回 None。"""
    import numpy as np
    import pandas as pd
    from dotmap import DotMap
    from scipy.signal import resample

    from health_inference.libs.pyPPG import PPG, Fiducials
    from health_inference.libs.pyPPG import biomarkers as BM
    from health_inference.libs.pyPPG import fiducials as FP
    from health_inference.libs.pyPPG import preproc as PP

    desired_sampling_rate_hz = 125.0
    processed = np.asarray(signal, dtype=np.float64).copy()
    minimum = float(np.min(processed))
    if minimum < 0.0:
        processed = processed - minimum + 1e-6
    sample_count = int(len(processed) * desired_sampling_rate_hz / sampling_rate_hz)
    if sample_count < desired_sampling_rate_hz * 5:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "pyPPG 输入时长无效")
    resampled = resample(processed, sample_count)

    settings = DotMap()
    settings.v = resampled
    settings.fs = desired_sampling_rate_hz
    settings.start_sig = 0
    settings.end_sig = len(resampled)
    settings.filtering = True
    settings.fL = 0.5
    settings.fH = 12.0
    settings.order = 4
    settings.sm_wins = {"ppg": 50, "vpg": 10, "apg": 10, "jpg": 10}
    settings.name = "ppg_signal"
    correction = pd.DataFrame()
    correction.loc[0, ["on", "dn", "dp", "v", "w", "f"]] = True
    settings.correction = correction

    preprocessor = PP.Preprocess(
        fL=settings.fL,
        fH=settings.fH,
        order=settings.order,
        sm_wins=settings.sm_wins,
    )
    processed_signals = preprocessor.get_signals(s=settings)
    if processed_signals is None or len(processed_signals) != 4:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "pyPPG 预处理结果无效")
    settings.ppg, settings.vpg, settings.apg, settings.jpg = processed_signals

    ppg = PPG(settings)
    fiducial_extractor = FP.FpCollection(s=ppg)
    fiducials = fiducial_extractor.get_fiducials(s=ppg)
    if fiducials is None or fiducials.empty:
        return None

    fiducial_points = Fiducials(fp=fiducials)
    biomarker_extractor = BM.BmCollection(s=ppg, fp=fiducial_points)
    biomarker_result = biomarker_extractor.get_biomarkers()
    if not isinstance(biomarker_result, tuple) or len(biomarker_result) != 3:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "pyPPG 生物标志物结果无效")

    biomarker_stats = biomarker_result[2]
    if not isinstance(biomarker_stats, Mapping):
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "pyPPG 生物标志物结果无效")
    features: dict[str, float] = {}
    for section, dataframe in biomarker_stats.items():
        if not isinstance(dataframe, pd.DataFrame):
            raise InferenceError("INFERENCE.EXECUTION_ERROR", "pyPPG 统计结果无效")
        for biomarker in _BIOMARKER_NAMES:
            if biomarker not in dataframe.columns:
                continue
            for statistic in ("mean", "std", "median"):
                if statistic not in dataframe.index:
                    continue
                features[f"{prefix}ppg_{section}_{biomarker}_{statistic}"] = float(
                    dataframe.loc[statistic, biomarker]
                )
    _validate_feature_values(features)
    return features


def extract_ppg_features(
    signal: NDArray[np.float64], sampling_rate_hz: float, prefix: str
) -> dict[str, float]:
    """优先沿旧 pyPPG 路径提取生物标志物，明确退化时使用基础特征。"""
    import numpy as np

    values = np.asarray(signal, dtype=np.float64)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 特征输入无效")
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0.0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    reversed_signal = values[::-1]
    if (
        len(reversed_signal) < int(15.0 * sampling_rate_hz)
        or float(np.std(reversed_signal)) < 1e-6
    ):
        return _basic_ppg_features(reversed_signal, sampling_rate_hz, prefix)

    biomarker_features = _extract_pyppg_features(
        reversed_signal, sampling_rate_hz, prefix
    )
    if biomarker_features is None:
        return _basic_ppg_features(reversed_signal, sampling_rate_hz, prefix)

    features = dict(biomarker_features)
    features.update(_basic_ppg_features(reversed_signal, sampling_rate_hz, prefix))
    _validate_feature_values(features)
    return features


def _ratio(ac1: float, dc1: float, ac2: float, dc2: float) -> float:
    """计算旧链红绿、蓝绿或红蓝的 AC/DC 比值。"""
    if ac1 == 0.0 or dc1 == 0.0 or ac2 == 0.0 or dc2 == 0.0:
        return 0.0
    return (ac1 / dc1) / (ac2 / dc2)


def extract_window_features(
    subject_data: NDArray[np.float64], sampling_rate_hz: float
) -> tuple[list[dict[str, float]], NDArray[np.int64]]:
    """按固定窗口和步长提取三色特征、AC/DC 均值及比值。"""
    import numpy as np

    matrix = np.asarray(subject_data, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != 9:
        raise InputError(InputErrorReason.INVALID_CHANNELS)
    if not np.isfinite(matrix).all():
        raise InputError(InputErrorReason.NON_FINITE_VALUE)
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0.0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    if len(matrix) < WINDOW_SIZE:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)

    feature_dicts: list[dict[str, float]] = []
    starts: list[int] = []
    window_count = ((len(matrix) - WINDOW_SIZE) // WINDOW_STEP) + 1
    for window_index in range(window_count):
        start = window_index * WINDOW_STEP
        window = matrix[start : start + WINDOW_SIZE]
        final_window = (
            np.diff(window, axis=0, prepend=window[0:1]) if APPLY_DIFF else window
        )

        features: dict[str, float] = {}
        for channel_index, prefix in enumerate(("r_", "g_", "b_")):
            features.update(
                extract_ppg_features(window[:, channel_index], sampling_rate_hz, prefix)
            )

        features.update(
            {
                "r_ac_mean": float(np.mean(final_window[:, 3])),
                "r_dc_mean": float(np.mean(final_window[:, 4])),
                "g_ac_mean": float(np.mean(final_window[:, 5])),
                "g_dc_mean": float(np.mean(final_window[:, 6])),
                "b_ac_mean": float(np.mean(final_window[:, 7])),
                "b_dc_mean": float(np.mean(final_window[:, 8])),
            }
        )
        features["r_g_ratio"] = _ratio(
            features["r_ac_mean"],
            features["r_dc_mean"],
            features["g_ac_mean"],
            features["g_dc_mean"],
        )
        features["b_g_ratio"] = _ratio(
            features["b_ac_mean"],
            features["b_dc_mean"],
            features["g_ac_mean"],
            features["g_dc_mean"],
        )
        features["r_b_ratio"] = _ratio(
            features["r_ac_mean"],
            features["r_dc_mean"],
            features["b_ac_mean"],
            features["b_dc_mean"],
        )
        _validate_feature_values(features)
        feature_dicts.append(features)
        starts.append(start)

    return feature_dicts, np.asarray(starts, dtype=np.int64)


def dicts_to_matrix(
    feature_dicts: list[Mapping[str, object]], feature_names: list[str]
) -> NDArray[np.float32]:
    """按给定清单稳定构建 float32 矩阵，仅对缺失特征填零。"""
    import numpy as np

    matrix = np.zeros((len(feature_dicts), len(feature_names)), dtype=np.float64)
    for row_index, feature_dict in enumerate(feature_dicts):
        for column_index, name in enumerate(feature_names):
            if name not in feature_dict:
                continue
            try:
                value = float(feature_dict[name])
            except (TypeError, ValueError, OverflowError) as exc:
                raise InferenceError(
                    "INFERENCE.EXECUTION_ERROR", "血氧特征结果无效"
                ) from exc
            if not math.isfinite(value):
                raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征结果无效")
            matrix[row_index, column_index] = value

    with np.errstate(over="ignore", invalid="ignore"):
        float32_matrix = matrix.astype(np.float32)
    if not np.isfinite(float32_matrix).all():
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "血氧特征矩阵无效")
    return float32_matrix
