"""在 PPG 质量门禁之后执行 AO、特征和双模型预测。"""

from __future__ import annotations

from dataclasses import dataclass

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.ao import (
    reconstruct_ao_signal,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.features import (
    build_feature_vector,
    calculate_pr,
    calculate_ptt_ms,
    find_ao_peaks,
    find_ppg_peaks,
    pair_aos_by_peaks,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.resources import (
    BPResources,
)
from health_inference.exceptions import InferenceError


@dataclass(frozen=True, slots=True)
class RawPrediction:
    """保存双模型原始结果及生成 SCG 输出所需的 AO 波形。"""

    sbp: float  # 收缩压模型原始预测，单位 mmHg。
    dbp: float  # 舒张压模型原始预测，单位 mmHg。
    ptt_ms: float  # 旧 PTT 汇总结果；无配对时为零，IQR 清空时可为 NaN。
    pr: int  # 按旧采样时长公式整数化的脉率。
    ao_signal: tuple[float, ...]  # 输出文件使用的实际 AO 重建波形。


def estimate_bp(prepared: PreprocessedBP, resources: BPResources) -> RawPrediction:
    """按旧版 PTT、PR 和六维特征规则调用当前版本双模型。"""
    import numpy as np

    ao_signal = reconstruct_ao_signal(
        prepared.scg_signal,
        prepared.scg_sampling_rate_hz,
    )
    ppg_peak_indices = find_ppg_peaks(
        prepared.ppg_signal,
        prepared.ppg_sampling_rate_hz,
    )
    ao_peak_indices = find_ao_peaks(
        ao_signal,
        prepared.scg_sampling_rate_hz,
    )
    ppg_times = np.asarray(prepared.ppg_timestamps_ms, dtype=np.int64)[
        ppg_peak_indices
    ]
    scg_timestamps = np.asarray(
        prepared.scg_timestamps_ms[: len(ao_signal)],
        dtype=np.int64,
    )
    ao_times = scg_timestamps[ao_peak_indices]
    paired_ao_times, paired_ppg_times = pair_aos_by_peaks(ao_times, ppg_times)
    ptt_ms = calculate_ptt_ms(paired_ppg_times, paired_ao_times) or 0
    pr = calculate_pr(len(ppg_times), prepared.ppg_timestamps_ms)
    features = build_feature_vector(
        prepared.profile,
        ptt_ms,
        pr,
        resources.normalization,
    )
    sbp = _predict_scalar(resources.sbp_model, features)
    dbp = _predict_scalar(resources.dbp_model, features)
    return RawPrediction(
        sbp=sbp,
        dbp=dbp,
        ptt_ms=float(ptt_ms),
        pr=pr,
        ao_signal=ao_signal,
    )


def _predict_scalar(model: object, features) -> float:
    """执行模型预测，安全映射异常并要求恰好一个有限结果。"""
    import numpy as np

    try:
        prediction = np.asarray(model.predict(features))
    except Exception as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "推理执行失败") from error
    if (
        prediction.shape != (1,)
        or not np.issubdtype(prediction.dtype, np.number)
        or not np.isfinite(prediction[0])
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "推理结果无效")
    return float(prediction[0])
