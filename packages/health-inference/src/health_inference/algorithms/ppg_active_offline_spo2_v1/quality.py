"""按 NeuroKit2 实际 PPG 质量分数执行血氧 v1 固定门禁。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_active_offline_spo2_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    PreprocessedSpo2,
)
from health_inference.exceptions import QualityRejectedError
from health_inference.quality import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)


@dataclass(frozen=True, slots=True)
class Spo2QualityResult:
    """保存实际质量分数及固定比较阈值。"""

    score: float  # NeuroKit2 质量分数均值乘以 100，不作舍入。
    threshold: int  # 固定拒绝阈值；分数严格小于此值时拒绝。


class Spo2QualityStrategy:
    """调用 NeuroKit2 评价预处理 PPG 并在特征计算前执行门禁。"""

    def evaluate(self, prepared: PreprocessedSpo2) -> Spo2QualityResult:
        """返回实际质量分数；低分或非法分数终止本次流水线。"""
        import neurokit2 as nk
        import numpy as np

        signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
        if signal.ndim != 1 or not len(signal) or not np.isfinite(signal).all():
            raise RuntimeError("PPG 质量输入无效")
        scores = np.asarray(
            nk.ppg_quality(signal, sampling_rate=int(prepared.sampling_rate_hz)),
            dtype=np.float64,
        )
        if not scores.size:
            raise RuntimeError("PPG 质量策略未产生有限评分")
        score = float(np.mean(scores) * 100.0)
        return _quality_result_from_score(score)


def _quality_result_from_score(score: object) -> Spo2QualityResult:
    """拒绝非法评分或严格低于 70 的实际评分。"""
    if type(score) not in (int, float) or not math.isfinite(score):
        raise RuntimeError("PPG 质量策略未产生有限评分")
    if score < QUALITY_THRESHOLD:
        raise QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(score=score, threshold=QUALITY_THRESHOLD),
            )
        )
    return Spo2QualityResult(score=float(score), threshold=QUALITY_THRESHOLD)
