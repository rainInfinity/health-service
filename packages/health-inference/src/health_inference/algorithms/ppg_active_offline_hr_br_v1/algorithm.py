"""装配并公开正式 PPG HR/BR v1 算法契约。"""

from collections.abc import Mapping

from health_inference.algorithms.ppg_active_offline_hr_br_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.inference import (
    RawPrediction,
    estimate_rates,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.load_input import (
    PPGInput,
    load_ppg,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import (
    PreprocessedPPG,
    preprocess_ppg,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.quality import (
    PPGQualityResult,
    PPGQualityStrategy,
)
from health_inference.exceptions import InferenceError
from health_inference.pipeline import OfflinePipeline
from health_inference.types import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
)

ALGORITHM_KEY = "ppg.active.offline.hr_br.v1"


def _load_input(inference_input: InferenceInput) -> PPGInput:
    """读取当前任务的 PPG 单块 CSV。"""
    return load_ppg(inference_input.files["ppg"])


def _build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedPPG,
    quality: PPGQualityResult | None,
    prediction: RawPrediction,
) -> InferenceOutput:
    """在模板阶段边界调用 PPG v1 结果构建器。"""
    return build_output(inference_input, loaded, prepared, quality, prediction)


def _validate_parameters(params: Mapping[str, object]) -> None:
    """拒绝任何改变 v1 固定计算含义的模型参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


_PIPELINE = OfflinePipeline[PPGInput, PreprocessedPPG, PPGQualityResult, RawPrediction](
    load_input=_load_input,
    preprocess=preprocess_ppg,
    quality_strategy=PPGQualityStrategy(),
    predict=estimate_rates,
    build_output=_build_output,
)

PPG_ACTIVE_OFFLINE_HR_BR_V1 = AlgorithmDefinition(
    key=ALGORITHM_KEY,
    is_example=False,
    required_inputs=("ppg",),
    required_artifacts=(),
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=valid_output,
    runner=_PIPELINE,
    metric_contracts=(
        MetricContract("hr", "number", "beats_per_minute"),
        MetricContract("br", "number", "breaths_per_minute"),
    ),
)
