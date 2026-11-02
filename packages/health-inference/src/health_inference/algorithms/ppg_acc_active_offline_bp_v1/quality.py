"""计算旧版 NeuroKit2 PPG 质量分数并执行固定 70 分门禁。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.exceptions import QualityRejectedError
from health_inference.quality import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)


@dataclass(frozen=True, slots=True)
class BPQualityResult:
    """保存本次实际 PPG 评分和固定拒绝阈值。"""

    score: float  # NeuroKit2 分数均值乘以 100，不作舍入。
    threshold: int  # 固定门槛；仅严格低于此值时拒绝。


class BPQualityStrategy:
    """使用清洗后 PPG 计算真实分数并在低分时停止流水线。"""

    def evaluate(self, prepared: PreprocessedBP) -> BPQualityResult:
        """返回实际质量结论；非法评分失败而不伪造门禁结果。"""
        import neurokit2 as nk
        import numpy as np

        signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
        if signal.ndim != 1 or not signal.size or not np.isfinite(signal).all():
            raise RuntimeError("PPG 质量输入无效")
        scores = np.asarray(
            nk.ppg_quality(
                signal,
                sampling_rate=int(prepared.ppg_sampling_rate_hz),
            ),
            dtype=np.float64,
        )
        if not scores.size:
            raise RuntimeError("PPG 质量策略未产生有限评分")
        score = float(np.mean(scores) * 100.0)
        return _quality_result_from_score(score)


def _quality_result_from_score(score: object) -> BPQualityResult:
    """拒绝非法评分或实际值低于固定阈值的情况。"""
    if type(score) not in (int, float) or not math.isfinite(score):
        raise RuntimeError("PPG 质量策略未产生有限评分")
    if score < QUALITY_THRESHOLD:
        raise QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(score=float(score), threshold=QUALITY_THRESHOLD),
            )
        )
    return BPQualityResult(score=float(score), threshold=QUALITY_THRESHOLD)
