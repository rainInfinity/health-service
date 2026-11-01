"""校验血氧 v1 特征清单并加载匹配的 ONNX 会话。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from health_inference.exceptions import InferenceError

_REQUIRED_ARTIFACTS = frozenset({"spo2", "feature_names", "stable_features"})


@dataclass(frozen=True, slots=True)
class Spo2Resources:
    """保存一个发布版本可复用的模型会话和特征索引。"""

    session: object  # 通过元数据校验的 ONNX Runtime 会话。
    input_name: str  # 接收稳定特征矩阵的输入节点名。
    output_name: str  # 返回每窗口预测的输出节点名。
    feature_names: tuple[str, ...]  # 完整特征清单的原始顺序。
    stable_features: tuple[str, ...]  # 稳定特征清单的原始顺序。
    stable_indices: tuple[int, ...]  # 稳定特征在完整清单中的位置。


def _invalid_artifact() -> InferenceError:
    """创建不泄露路径或制品内容的统一资源错误。"""
    return InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品格式无效")


def _load_names(path: Path) -> tuple[str, ...]:
    """只从 UTF-8 JSON 字符串列表读取有序特征名称。"""
    if not isinstance(path, Path) or not path.is_file():
        raise _invalid_artifact()
    try:
        with path.open("r", encoding="utf-8") as manifest_file:
            names = json.load(manifest_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _invalid_artifact() from error

    if (
        not isinstance(names, list)
        or not names
        or any(type(name) is not str or not name.strip() for name in names)
        or len(names) != len(set(names))
    ):
        raise _invalid_artifact()
    return tuple(names)


def _is_dynamic_dimension(dimension: object) -> bool:
    """判断 ONNX 维度是否可由本次窗口批量动态决定。"""
    return dimension is None or (isinstance(dimension, str) and bool(dimension))


def _validate_session(
    session: object, stable_feature_count: int
) -> tuple[str, str]:
    """要求模型只有一个 float32 矩阵输入和每窗一个标量输出。"""
    try:
        inputs = session.get_inputs()
        outputs = session.get_outputs()
    except Exception as error:
        raise _invalid_artifact() from error

    if len(inputs) != 1 or len(outputs) != 1:
        raise _invalid_artifact()
    model_input = inputs[0]
    model_output = outputs[0]
    input_shape = model_input.shape
    output_shape = model_output.shape
    if (
        model_input.type != "tensor(float)"
        or not isinstance(input_shape, (list, tuple))
        or len(input_shape) != 2
        or not _is_dynamic_dimension(input_shape[0])
        or type(input_shape[1]) is not int
        or input_shape[1] != stable_feature_count
        or model_output.type != "tensor(float)"
        or not isinstance(output_shape, (list, tuple))
        or len(output_shape) not in (1, 2)
        or not _is_dynamic_dimension(output_shape[0])
        or (len(output_shape) == 2 and output_shape[1] != 1)
        or not model_input.name
        or not model_output.name
    ):
        raise _invalid_artifact()
    return model_input.name, model_output.name


def load_spo2_resources(artifact_paths: Mapping[str, Path]) -> Spo2Resources:
    """校验三件套清单、建立稳定索引并加载可批量推理的 ONNX 会话。"""
    if set(artifact_paths) != _REQUIRED_ARTIFACTS:
        raise _invalid_artifact()

    feature_names = _load_names(artifact_paths["feature_names"])
    stable_features = _load_names(artifact_paths["stable_features"])
    full_indices = {name: index for index, name in enumerate(feature_names)}
    if any(name not in full_indices for name in stable_features):
        raise _invalid_artifact()

    model_path = artifact_paths["spo2"]
    if not isinstance(model_path, Path) or not model_path.is_file():
        raise _invalid_artifact()
    try:
        import onnxruntime as ort
    except ImportError as error:
        raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型推理依赖不可用") from error
    try:
        session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
    except Exception as error:
        raise _invalid_artifact() from error

    input_name, output_name = _validate_session(session, len(stable_features))
    return Spo2Resources(
        session=session,
        input_name=input_name,
        output_name=output_name,
        feature_names=feature_names,
        stable_features=stable_features,
        stable_indices=tuple(full_indices[name] for name in stable_features),
    )
