"""按旧版 NeuroKit2 分数执行糖尿病风险 v1 质量门禁。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.exceptions import QualityRejectedError
from health_inference.quality import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)


@dataclass(frozen=True, slots=True)
class DiabetesQualityResult:
    """保存本次实际质量分数与固定比较阈值。"""

    score: float  # NeuroKit2 评分均值乘以 100，不作舍入。
    threshold: int  # 固定低分阈值；分数严格小于此值时拒绝。


class DiabetesQualityStrategy:
    """调用 NeuroKit2 评价清洗波形并在特征计算前拒绝低分输入。"""

    def evaluate(self, prepared: PreprocessedDiabetes) -> DiabetesQualityResult:
        """返回实际评分；非法评分和低分均不会继续执行特征或模型。"""
        import neurokit2 as nk
        import numpy as np

        signal = np.asarray(prepared.ppg_signal, dtype=np.float64)
        if signal.ndim != 1 or not len(signal) or not np.isfinite(signal).all():
            raise RuntimeError("PPG 质量输入无效")
        scores = np.asarray(
            nk.ppg_quality(signal, sampling_rate=int(prepared.sampling_rate_hz)),
            dtype=np.float64,
        )
        if not scores.size or not np.isfinite(scores).all():
            raise RuntimeError("PPG 质量策略未产生有限评分")
        score = float(np.mean(scores) * 100.0)
        if not math.isfinite(score):
            raise RuntimeError("PPG 质量策略未产生有限评分")
        if score < QUALITY_THRESHOLD:
            raise QualityRejectedError(
                QualityRejection(
                    QualityRejectionReason.LOW_SCORE,
                    LowScoreDetails(score=score, threshold=QUALITY_THRESHOLD),
                )
            )
        return DiabetesQualityResult(score=score, threshold=QUALITY_THRESHOLD)
