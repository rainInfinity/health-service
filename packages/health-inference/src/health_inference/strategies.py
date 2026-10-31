"""定义离线算法可替换的质量判定与结果后处理策略。"""

from typing import Protocol, TypeVar

PreparedT = TypeVar("PreparedT")
QualityT = TypeVar("QualityT")
PredictionT = TypeVar("PredictionT")


class QualityStrategy(Protocol[PreparedT, QualityT]):
    """声明按预处理数据产生实际质量结论的策略。"""

    def evaluate(self, prepared: PreparedT, /) -> QualityT:
        """计算并返回本次输入的质量结论；拒绝时抛出类型化拒绝异常。"""
        ...


class PostprocessingStrategy(Protocol[PredictionT]):
    """声明对原始预测执行固定版本结果规则的策略。"""

    def apply(self, prediction: PredictionT, /) -> PredictionT:
        """返回经过本版本后处理的预测结果。"""
        ...
