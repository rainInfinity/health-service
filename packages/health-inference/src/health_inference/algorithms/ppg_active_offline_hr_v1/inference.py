"""仅用 PPG 峰值及速率均值估计独立心率。"""

from health_inference.utils.ppg_preprocessing import PreprocessedPPG


def estimate_hr(prepared: PreprocessedPPG) -> float:
    """沿用 int(fs) 采样率解释，不计算或读取呼吸率。"""
    import neurokit2 as nk
    import numpy as np

    sampling_rate = int(prepared.sampling_rate_hz)
    signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
    peaks = nk.ppg_findpeaks(signal, sampling_rate=sampling_rate)
    return float(np.mean(nk.ppg_rate(peaks, sampling_rate=sampling_rate)))
