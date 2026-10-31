"""按固定阶段顺序运行离线算法，不在实例上保存单次任务数据。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .strategies import PostprocessingStrategy, QualityStrategy
from .types import InferenceInput, InferenceOutput

LoadedT = TypeVar("LoadedT")
PreparedT = TypeVar("PreparedT")
QualityT = TypeVar("QualityT")
PredictionT = TypeVar("PredictionT")


@dataclass(frozen=True, slots=True)
class OfflinePipeline(Generic[LoadedT, PreparedT, QualityT, PredictionT]):
    """编排输入、预处理、质量、推理、后处理及结果输出阶段。"""

    load_input: Callable[[InferenceInput], LoadedT]  # 读取并校验一次任务输入。
    preprocess: Callable[[LoadedT], PreparedT]  # 将输入转换为后续计算数据。
    predict: Callable[[PreparedT], PredictionT]  # 从预处理数据产生原始预测。
    build_output: Callable[
        [InferenceInput, LoadedT, PreparedT, QualityT | None, PredictionT], InferenceOutput
    ]  # 使用本次阶段数据组织结果和输出文件。
    quality_strategy: QualityStrategy[PreparedT, QualityT] | None = None  # 未配置表示未执行质检。
    postprocessing_strategy: PostprocessingStrategy[PredictionT] | None = (
        None  # 固定版本后处理规则。
    )

    def __call__(self, inference_input: InferenceInput) -> InferenceOutput:
        """按契约顺序运行阶段，并将所有任务数据限制在本次调用内。"""
        loaded = self.load_input(inference_input)
        prepared = self.preprocess(loaded)

        quality_result = None
        if self.quality_strategy is not None:
            quality_result = self.quality_strategy.evaluate(prepared)
            if quality_result is None:
                raise RuntimeError("质量策略未返回实际质量结论")

        prediction = self.predict(prepared)
        if self.postprocessing_strategy is not None:
            prediction = self.postprocessing_strategy.apply(prediction)

        return self.build_output(
            inference_input,
            loaded,
            prepared,
            quality_result,
            prediction,
        )
