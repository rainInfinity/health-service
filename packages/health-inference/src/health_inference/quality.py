"""定义与 Django 无关的质量拒绝原因、明细及校验。"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class QualityRejectionReason(StrEnum):
    """列出可由计算算法报告的受控质量拒绝原因。"""

    LOW_SCORE = "LOW_SCORE"  # 实际质量分数低于本次算法阈值。
    NO_VALID_SIGNAL = "NO_VALID_SIGNAL"  # 算法明确确认不存在可用信号。


@dataclass(frozen=True, slots=True)
class LowScoreDetails:
    """保存一次低分拒绝实际比较的原始数值。"""

    score: int | float  # 本次计算实际得到的质量分数。
    threshold: int | float  # 本次计算实际使用的拒绝阈值。

    def __post_init__(self) -> None:
        """确保评分为有限数值且严格低于阈值。"""
        if not _is_finite_number(self.score) or not _is_finite_number(self.threshold):
            raise ValueError("质量分数与阈值必须是有限数值")
        if self.score >= self.threshold:
            raise ValueError("低分拒绝要求分数严格低于阈值")


@dataclass(frozen=True, slots=True)
class QualityRejection:
    """将受控拒绝原因与该原因允许的明细绑定。"""

    reason: QualityRejectionReason  # 本次质量拒绝的稳定原因码。
    details: LowScoreDetails | None  # 低分原因的数值；无可用信号时为空。

    def __post_init__(self) -> None:
        """拒绝未知原因及原因与明细不匹配的组合。"""
        if not isinstance(self.reason, QualityRejectionReason):
            raise TypeError("未知质量拒绝原因")
        if self.reason is QualityRejectionReason.LOW_SCORE:
            if not isinstance(self.details, LowScoreDetails):
                raise TypeError("低分拒绝必须提供评分明细")
            LowScoreDetails(self.details.score, self.details.threshold)
        elif self.details is not None:
            raise ValueError("无可用信号拒绝不接受评分明细")


def quality_rejection_from_mapping(value: object) -> QualityRejection | None:
    """从私有 JSON 明细中重建合法质量拒绝并丢弃未声明字段。"""
    if not isinstance(value, Mapping):
        return None
    reason = value.get("reason")
    details = value.get("details")
    try:
        if reason == QualityRejectionReason.LOW_SCORE.value:
            if not isinstance(details, Mapping):
                return None
            return QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(
                    score=details.get("score"),
                    threshold=details.get("threshold"),
                ),
            )
        if reason == QualityRejectionReason.NO_VALID_SIGNAL.value:
            if details is not None:
                return None
            return QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None)
    except (TypeError, ValueError):
        return None
    return None


def _is_finite_number(value: object) -> bool:
    """只接受有限的原生整数或浮点数，并排除布尔值。"""
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)
