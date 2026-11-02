"""校验糖尿病风险 v1 固定 ONNX 输入与类别概率输出。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import FEATURE_COUNT
from health_inference.exceptions import InferenceError

_REQUIRED_ARTIFACTS = frozenset({"diabetes"})


@dataclass(frozen=True, slots=True)
class DiabetesResources:
    """保存固定版本可复用的 ONNX 会话和已校验节点名。"""

    session: object  # CPUExecutionProvider 上的 ONNX Runtime 会话。
    input_name: str  # 唯一 918 维 float32 输入节点。
    label_output_name: str  # 用于校验类别映射的整数标签节点。
    probabilities_output_name: str  # 类别顺序固定为 0、1 的概率矩阵节点。


def _invalid_artifact() -> InferenceError:
    """创建不泄露模型路径或内容的统一制品错误。"""
    return InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品格式无效")


def _is_dynamic_dimension(value: object) -> bool:
    """判断 ONNX 批次维度是否可由任务动态决定。"""
    return value is None or (isinstance(value, str) and bool(value))


def _validate_session(session: object) -> tuple[str, str, str]:
    """按名称和类型校验唯一特征输入、标签及二分类概率输出。"""
    try:
        inputs = session.get_inputs()
        outputs = session.get_outputs()
    except Exception as error:
        raise _invalid_artifact() from error

    if len(inputs) != 1 or len(outputs) != 2:
        raise _invalid_artifact()
    model_input = inputs[0]
    outputs_by_name = {output.name: output for output in outputs}
    if set(outputs_by_name) != {"label", "probabilities"}:
        raise _invalid_artifact()
    label = outputs_by_name["label"]
    probabilities = outputs_by_name["probabilities"]
    input_shape = model_input.shape
    label_shape = label.shape
    probabilities_shape = probabilities.shape
    if (
        not model_input.name
        or model_input.type != "tensor(float)"
        or not isinstance(input_shape, (list, tuple))
        or len(input_shape) != 2
        or not _is_dynamic_dimension(input_shape[0])
        or type(input_shape[1]) is not int
        or input_shape[1] != FEATURE_COUNT
        or label.type != "tensor(int64)"
        or not isinstance(label_shape, (list, tuple))
        or len(label_shape) != 1
        or not _is_dynamic_dimension(label_shape[0])
        or probabilities.type != "tensor(float)"
        or not isinstance(probabilities_shape, (list, tuple))
        or len(probabilities_shape) != 2
        or not _is_dynamic_dimension(probabilities_shape[0])
        or type(probabilities_shape[1]) is not int
        or probabilities_shape[1] != 2
    ):
        raise _invalid_artifact()
    return model_input.name, label.name, probabilities.name


def load_diabetes_resources(artifact_paths: Mapping[str, Path]) -> DiabetesResources:
    """加载唯一指定模型制品并按其节点名绑定推理输出。"""
    if set(artifact_paths) != _REQUIRED_ARTIFACTS:
        raise _invalid_artifact()
    model_path = artifact_paths["diabetes"]
    if not isinstance(model_path, Path) or not model_path.is_file():
        raise _invalid_artifact()
    try:
        import onnxruntime as ort
    except ImportError as error:
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型推理依赖不可用") from error
    try:
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        input_name, label_output_name, probabilities_output_name = _validate_session(session)
    except InferenceError:
        raise
    except Exception as error:
        raise _invalid_artifact() from error
    return DiabetesResources(
        session=session,
        input_name=input_name,
        label_output_name=label_output_name,
        probabilities_output_name=probabilities_output_name,
    )
