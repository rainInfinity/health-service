"""装配并公开正式 PPG 血氧 v1 算法契约。"""

from collections.abc import Callable, Mapping

from health_inference.algorithms.ppg_active_offline_spo2_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.inference import (
    Spo2InferencePrediction,
    run_spo2_inference,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.load_input import (
    load_spo2_ppg,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    PreprocessedSpo2,
    preprocess_ppg,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.quality import (
    Spo2QualityResult,
    Spo2QualityStrategy,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.resources import (
    load_spo2_resources,
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
from health_inference.utils.ppg_input import PPGInput

ALGORITHM_KEY = "ppg.active.offline.spo2.v1"


def _load_input(inference_input: InferenceInput) -> PPGInput:
    """读取并校验当前任务唯一的 PPG 输入文件。"""
    return load_spo2_ppg(inference_input.files["ppg"])


def _build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedSpo2,
    quality: Spo2QualityResult | None,
    prediction: Spo2InferencePrediction,
) -> InferenceOutput:
    """将本次任务阶段数据交给血氧结果构建器。"""
    return build_output(inference_input, loaded, prepared, quality, prediction)


def _validate_parameters(params: Mapping[str, object]) -> None:
    """拒绝任何改变血氧 v1 固定计算规则的参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


def _create_runtime(
    resources: VersionResources,
) -> Callable[[InferenceInput], InferenceOutput]:
    """加载固定模型制品并创建不保存单次任务数据的血氧流水线。"""
    spo2_resources = load_spo2_resources(resources.artifact_paths)

    def predict(prepared: PreprocessedSpo2) -> Spo2InferencePrediction:
        """使用当前绑定版本执行窗口特征、模型预测和固定后处理。"""
        return run_spo2_inference(prepared, spo2_resources)

    return OfflinePipeline[PPGInput, PreprocessedSpo2, Spo2QualityResult, Spo2InferencePrediction](
        load_input=_load_input,
        preprocess=preprocess_ppg,
        quality_strategy=Spo2QualityStrategy(),
        predict=predict,
        build_output=_build_output,
    )


PPG_ACTIVE_OFFLINE_SPO2_V1 = AlgorithmDefinition(
    key=ALGORITHM_KEY,
    is_example=False,
    required_inputs=("ppg",),
    required_artifacts=("spo2", "feature_names", "stable_features"),
    input_extensions={"ppg": frozenset({"csv"})},
    artifact_extensions={
        "spo2": frozenset({"onnx"}),
        "feature_names": frozenset({"json"}),
        "stable_features": frozenset({"json"}),
    },
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=valid_output,
    resource_factory=_create_runtime,
    metric_contracts=(MetricContract("spo2", "number", "percent"),),
)
