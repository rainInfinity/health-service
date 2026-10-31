"""使用旧链 NeuroKit2 方法从预处理波形估计 HR 与 BR。"""

from dataclasses import dataclass

from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import (
    PreprocessedPPG,
)


@dataclass(frozen=True, slots=True)
class RawPrediction:
    """保存后处理前的原始心率和呼吸率估计。"""

    hr: float  # NeuroKit2 峰检测与速率函数产生的原始心率，单位 BPM。
    br: float  # NeuroKit2 呼吸速率函数产生的原始呼吸率，单位 BPM。


def estimate_rates(prepared: PreprocessedPPG) -> RawPrediction:
    """按旧链采样率参数解释计算峰值速率并分别取均值。"""
    import neurokit2 as nk
    import numpy as np

    sampling_rate = int(prepared.sampling_rate_hz)
    ppg_signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
    rsp_signal = np.asarray(prepared.rsp_signal, dtype=np.float64)
    peaks = nk.ppg_findpeaks(ppg_signal, sampling_rate=sampling_rate)
    hr = float(np.mean(nk.ppg_rate(peaks, sampling_rate=sampling_rate)))
    br = float(np.mean(nk.rsp_rate(rsp_signal, sampling_rate=sampling_rate)))
    return RawPrediction(hr=hr, br=br)
