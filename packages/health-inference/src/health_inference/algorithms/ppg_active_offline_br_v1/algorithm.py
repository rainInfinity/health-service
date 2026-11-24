"""装配并公开正式独立 PPG BR v1 算法契约。"""

from collections.abc import Callable, Mapping

from health_inference.algorithms.ppg_active_offline_br_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_br_v1.inference import estimate_br
from health_inference.exceptions import InferenceError
from health_inference.pipeline import OfflinePipeline
from health_inference.types import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
    VersionResources,
)
from health_inference.utils.ppg_input import PPGInput, load_ppg
from health_inference.utils.ppg_preprocessing import PreprocessedPPG, preprocess_ppg
from health_inference.utils.ppg_quality import PPGQualityResult, PPGQualityStrategy

ALGORITHM_KEY = "ppg.active.offline.br.v1"


def _load_input(inference_input: InferenceInput) -> PPGInput:
    """读取当前任务的 PPG 单块 CSV。"""
    return load_ppg(inference_input.files["ppg"])


def _validate_parameters(params: Mapping[str, object]) -> None:
    """拒绝任何改变 v1 固定计算含义的模型参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


_PIPELINE = OfflinePipeline[PPGInput, PreprocessedPPG, PPGQualityResult, float](
    load_input=_load_input,
    preprocess=preprocess_ppg,
    quality_strategy=PPGQualityStrategy(),
    predict=estimate_br,
    build_output=build_output,
)


def _create_runtime(
    _: VersionResources,
) -> Callable[[InferenceInput], InferenceOutput]:
    """为无模型制品的独立 BR 版本复用无状态流水线。"""
    return _PIPELINE


PPG_ACTIVE_OFFLINE_BR_V1 = AlgorithmDefinition(
    key=ALGORITHM_KEY,
    is_example=False,
    required_inputs=("ppg",),
    required_artifacts=(),
    input_extensions={"ppg": frozenset({"csv"})},
    artifact_extensions={},
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=valid_output,
    resource_factory=_create_runtime,
    metric_contracts=(MetricContract("br", "number", "breaths_per_minute"),),
)
