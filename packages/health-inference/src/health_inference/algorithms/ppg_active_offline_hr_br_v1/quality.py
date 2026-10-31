"""以 NeuroKit2 实际 PPG 质量分数执行 v1 固定门禁。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import (
    PreprocessedPPG,
)
from health_inference.exceptions import QualityRejectedError
from health_inference.quality import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)

QUALITY_THRESHOLD = 70


@dataclass(frozen=True, slots=True)
class PPGQualityResult:
    """保存本次实际评分及用于比较的固定阈值。"""

    score: float  # NeuroKit2 PPG 质量分数均值乘以 100，不作舍入。
    threshold: int  # 固定拒绝阈值；分数严格小于此值时拒绝。


class PPGQualityStrategy:
    """调用 NeuroKit2 评价预处理 PPG 并执行固定拒绝规则。"""

    def evaluate(self, prepared: PreprocessedPPG) -> PPGQualityResult:
        """返回实际质量信息；低分终止本次流水线。"""
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
