"""装配并公开正式 PPG/ACC 血压 v1 算法契约。"""

from collections.abc import Callable, Mapping

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.constants import (
    ALGORITHM_KEY,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.inference import (
    RawPrediction,
    estimate_bp,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    LoadedBPInput,
    load_bp_input,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
    preprocess_bp,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.quality import (
    BPQualityResult,
    BPQualityStrategy,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.resources import (
    load_bp_resources,
)
from health_inference.exceptions import InferenceError
from health_inference.pipeline import OfflinePipeline
from health_inference.types import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
    VersionResources,
)


def _load_input(inference_input: InferenceInput) -> LoadedBPInput:
    """读取当前任务的双传感器输入和冻结资料。"""
    return load_bp_input(inference_input)


def _validate_parameters(params: Mapping[str, object]) -> None:
    """拒绝改变血压 v1 固定解释的所有非空版本参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


def _create_runtime(
    resources: VersionResources,
) -> Callable[[InferenceInput], InferenceOutput]:
    """加载当前绑定版本制品并装配无任务状态的离线流水线。"""
    bp_resources = load_bp_resources(resources.artifact_paths)

    def predict(prepared: PreprocessedBP) -> RawPrediction:
        """在质量门禁后使用固定版本模型计算血压。"""
        return estimate_bp(prepared, bp_resources)

    def make_output(
        inference_input: InferenceInput,
        loaded: LoadedBPInput,
        prepared: PreprocessedBP,
        quality: BPQualityResult | None,
        prediction: RawPrediction,
    ) -> InferenceOutput:
        """将本次流水线数据交给血压结果构建器。"""
        return build_output(inference_input, loaded, prepared, quality, prediction)

    return OfflinePipeline[LoadedBPInput, PreprocessedBP, BPQualityResult, RawPrediction](
        load_input=_load_input,
        preprocess=preprocess_bp,
        quality_strategy=BPQualityStrategy(),
        predict=predict,
        build_output=make_output,
    )


PPG_ACC_ACTIVE_OFFLINE_BP_V1 = AlgorithmDefinition(
    key=ALGORITHM_KEY,
    is_example=False,
    required_inputs=("ppg", "acc"),
    required_artifacts=("sbp", "dbp", "normalize"),
    input_extensions={"ppg": frozenset({"csv"}), "acc": frozenset({"csv"})},
    artifact_extensions={
        "sbp": frozenset({"joblib"}),
        "dbp": frozenset({"joblib"}),
        "normalize": frozenset({"csv"}),
    },
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=valid_output,
    resource_factory=_create_runtime,
    metric_contracts=(
        MetricContract("sbp", "number", "mmHg"),
        MetricContract("dbp", "number", "mmHg"),
    ),
)
