"""为独立 HR 与 BR 复用同一 PPG 质量评分和固定门禁。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.exceptions import QualityRejectedError
from health_inference.quality import LowScoreDetails, QualityRejection, QualityRejectionReason
from health_inference.utils.ppg_preprocessing import PreprocessedPPG

QUALITY_THRESHOLD = 70


@dataclass(frozen=True, slots=True)
class PPGQualityResult:
    """保存本次实际评分及固定比较阈值。"""

    score: float  # NeuroKit2 PPG 质量分数均值乘以 100，不作舍入。
    threshold: int  # 固定拒绝阈值；分数严格小于此值时拒绝。


class PPGQualityStrategy:
    """评价共享预处理的 PPG 波形，不以呼吸率可估计性代替 PPG 质量。"""

    def evaluate(self, prepared: PreprocessedPPG) -> PPGQualityResult:
        """返回实际质量信息，评分不足时终止当前独立算法。"""
        import neurokit2 as nk
        import numpy as np

        signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
        scores = nk.ppg_quality(signal, sampling_rate=int(prepared.sampling_rate_hz))
        score = float(np.mean(scores) * 100.0)
        return _quality_result_from_score(score)


def _quality_result_from_score(score: float) -> PPGQualityResult:
    """拒绝非法评分或严格低于阈值的实际评分。"""
    if type(score) not in (int, float) or not math.isfinite(score):
        raise RuntimeError("PPG 质量策略未产生有限评分")
    if score < QUALITY_THRESHOLD:
        raise QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(score=score, threshold=QUALITY_THRESHOLD),
            )
        )
    return PPGQualityResult(score=float(score), threshold=QUALITY_THRESHOLD)
