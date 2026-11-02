"""按旧版顺序 VMD 与双条件规则重建 AO 信号。"""

from __future__ import annotations


def reconstruct_ao_signal(
    scg_signal: tuple[float, ...],
    sampling_rate_hz: float,
) -> tuple[float, ...]:
    """执行顺序 VMD，并按旧版频率与波形因子条件重建 AO。"""
    import numpy as np
    from vmdpy import VMD

    signal = np.asarray(scg_signal, dtype=np.float64)
    modes = _sequential_vmd(signal, VMD, np)
    if modes.shape[0] == 0:
        return tuple(float(value) for value in np.zeros_like(signal))

    average_frequencies = tuple(
        _calculate_average_frequency(mode, sampling_rate_hz) for mode in modes
    )
    waveform_factors = tuple(_calculate_waveform_factor(mode, np) for mode in modes)
    average_waveform_factor = float(np.mean(waveform_factors))
    frequency_indices = [
        index
        for index, frequency in enumerate(average_frequencies)
        if 8.0 <= frequency <= 24.0
    ]
    waveform_indices = [
        index
        for index, factor in enumerate(waveform_factors)
        if factor > average_waveform_factor
    ]
    selected_indices = list(set(frequency_indices) & set(waveform_indices))
    if not selected_indices:
        selected_indices = frequency_indices
    if not selected_indices:
        reconstructed = np.zeros_like(signal)
    else:
        reconstructed = np.sum([modes[index] for index in selected_indices], axis=0)
    return tuple(float(value) for value in reconstructed)


def _sequential_vmd(signal, vmd, numpy):
    """逐次以 K=1 分解残差，直到其能量比例达到旧停止阈值。"""
    original_energy = numpy.sum(signal**2) + 1e-12
    residual = signal.astype(float).copy()
    modes: list[object] = []

    while True:
        components, _, _ = vmd(residual, 2000, 0.0, 1, 0, 1, 1e-7)
        mode = components[0]
        if len(mode) != len(residual):
            minimum_length = min(len(mode), len(residual))
            mode = mode[:minimum_length]
            residual = residual[:minimum_length]
        modes.append(mode)
        residual = residual - mode
        if numpy.sum(residual**2) / original_energy <= 0.01:
            break
    return numpy.array(modes) if modes else numpy.zeros((0, residual.shape[0]))


def _calculate_average_frequency(mode, sampling_rate_hz: float) -> float:
    """用旧版 Welch 功率谱密度计算单个模态的加权平均频率。"""
    import numpy as np
    from scipy.signal import welch

    frequencies, power = welch(
        mode,
        fs=sampling_rate_hz,
        nperseg=min(len(mode) // 4, 256),
    )
    total_power = np.sum(power)
    if total_power > 0:
        return float(np.sum(frequencies * power) / total_power)
    return 0.0


def _calculate_waveform_factor(mode, numpy) -> float:
    """计算旧版 RMS 与绝对均值之比，零绝对均值回退为零。"""
    rms = numpy.sqrt(numpy.mean(mode**2))
    mean_absolute = numpy.mean(numpy.abs(mode))
    if mean_absolute == 0:
        return 0.0
    return float(rms / mean_absolute)
