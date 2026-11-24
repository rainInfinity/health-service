"""沿用 NeuroKit2 呼吸速率方法，仅估计独立 BR。"""

from health_inference.utils.ppg_preprocessing import PreprocessedPPG


def estimate_br(prepared: PreprocessedPPG) -> float:
    """按旧链的整数采样率计算呼吸速率并返回原始均值。"""
    import neurokit2 as nk
    import numpy as np

    rsp_signal = np.asarray(prepared.rsp_signal, dtype=np.float64)
    return float(
        np.mean(nk.rsp_rate(rsp_signal, sampling_rate=int(prepared.sampling_rate_hz)))
    )
