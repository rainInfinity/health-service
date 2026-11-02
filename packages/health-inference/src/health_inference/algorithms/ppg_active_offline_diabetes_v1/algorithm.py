"""装配并公开正式 PPG 糖尿病风险 v1 算法契约。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from health_inference.algorithms.ppg_active_offline_diabetes_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    ALGORITHM_KEY,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.inference import (
    RawDiabetesPrediction,
    run_diabetes_inference,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.load_input import (
    load_diabetes_ppg,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.postprocessing import (
    DiabetesPostprocessingStrategy,
    DiabetesPrediction,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
    preprocess_ppg,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.quality import (
    DiabetesQualityResult,
    DiabetesQualityStrategy,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.resources import (
    load_diabetes_resources,
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


def _load_input(inference_input: InferenceInput) -> PPGInput:
    """读取并校验当前任务唯一 PPG 输入。"""
    return load_diabetes_ppg(inference_input.files["ppg"])


def _build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedDiabetes,
    quality: DiabetesQualityResult | None,
    prediction: DiabetesPrediction,
) -> InferenceOutput:
    """将本次任务各阶段数据交给糖尿病结果构建器。"""
    return build_output(inference_input, loaded, prepared, quality, prediction)


def _validate_parameters(params: Mapping[str, object]) -> None:
    """拒绝任何改变糖尿病风险 v1 固定计算规则的参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


def _create_runtime(
    resources: VersionResources,
) -> Callable[[InferenceInput], InferenceOutput]:
    """加载版本绑定模型并创建不保存单任务数据的流水线。"""
    diabetes_resources = load_diabetes_resources(resources.artifact_paths)

    def predict(prepared: PreprocessedDiabetes) -> RawDiabetesPrediction:
        """在质量门禁后执行特征提取、ONNX 推理和固定三分类。"""
        return run_diabetes_inference(prepared, diabetes_resources)

    return OfflinePipeline(
        load_input=_load_input,
        preprocess=preprocess_ppg,
        quality_strategy=DiabetesQualityStrategy(),
        predict=predict,
        postprocessing_strategy=DiabetesPostprocessingStrategy(),
        build_output=_build_output,
    )


PPG_ACTIVE_OFFLINE_DIABETES_V1 = AlgorithmDefinition(
    key=ALGORITHM_KEY,
    is_example=False,
    required_inputs=("ppg",),
    required_artifacts=("diabetes",),
    input_extensions={"ppg": frozenset({"csv"})},
    artifact_extensions={"diabetes": frozenset({"onnx"})},
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=valid_output,
    resource_factory=_create_runtime,
    metric_contracts=(MetricContract("diabetes_probability", "number", "ratio"),),
)
