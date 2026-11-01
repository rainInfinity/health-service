"""验证血氧 v1 制品清单、稳定索引和 ONNX 签名约束。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.resources import (
    load_spo2_resources,
)
from health_inference.exceptions import InferenceError


def _artifact_paths(
    tmp_path: Path, feature_names: object, stable_features: object
) -> dict[str, Path]:
    """创建隔离 JSON 清单和占位模型路径。"""
    feature_path = tmp_path / "feature_names.json"
    feature_path.write_text(json.dumps(feature_names), encoding="utf-8")
    stable_path = tmp_path / "stable_features.json"
    stable_path.write_text(json.dumps(stable_features), encoding="utf-8")
    model_path = tmp_path / "spo2.onnx"
    model_path.write_bytes(b"test model")
    return {
        "spo2": model_path,
        "feature_names": feature_path,
        "stable_features": stable_path,
    }


def _session(
    input_shape: list[object] | tuple[object, ...] = ("batch", 2),
    output_shape: list[object] | tuple[object, ...] = ("batch", 1),
    input_type: str = "tensor(float)",
    output_type: str = "tensor(float)",
    input_count: int = 1,
    output_count: int = 1,
) -> SimpleNamespace:
    """构造仅含 ONNX 元数据的测试会话。"""
    return SimpleNamespace(
        get_inputs=lambda: [
            SimpleNamespace(name=f"input_{index}", type=input_type, shape=input_shape)
            for index in range(input_count)
        ],
        get_outputs=lambda: [
            SimpleNamespace(name=f"output_{index}", type=output_type, shape=output_shape)
            for index in range(output_count)
        ],
    )


def _install_session(monkeypatch, session: SimpleNamespace) -> None:
    """让资源加载器读取指定的模型元数据。"""

    def create_session(model_path: str, providers: list[str]) -> SimpleNamespace:
        """返回测试会话元数据，不执行 ONNX 推理。"""
        return session

    monkeypatch.setattr("onnxruntime.InferenceSession", create_session)


def test_resources_preserve_manifest_order_and_stable_indices(
    tmp_path: Path, monkeypatch
):
    """验证稳定特征按清单顺序映射回完整特征索引。"""
    paths = _artifact_paths(tmp_path, ["red", "green", "blue"], ["blue", "red"])
    _install_session(monkeypatch, _session(input_shape=("batch", 2)))

    resources = load_spo2_resources(paths)

    assert resources.feature_names == ("red", "green", "blue")
    assert resources.stable_features == ("blue", "red")
    assert resources.stable_indices == (2, 0)
    assert resources.input_name == "input_0"
    assert resources.output_name == "output_0"


@pytest.mark.parametrize("output_shape", [("batch",), ("batch", 1)])
def test_resources_accept_supported_per_window_output_shapes(
    tmp_path: Path, monkeypatch, output_shape
):
    """验证模型可用一维或单列张量返回逐窗口预测。"""
    paths = _artifact_paths(tmp_path, ["red", "green"], ["red", "green"])
    _install_session(monkeypatch, _session(output_shape=output_shape))

    resources = load_spo2_resources(paths)

    assert resources.stable_indices == (0, 1)


@pytest.mark.parametrize(
    ("feature_names", "stable_features"),
    [
        ([], ["red"]),
        (["red"], []),
        (["red", "red"], ["red"]),
        (["red"], ["red", "red"]),
        (["red"], ["missing"]),
        (["  "], ["  "]),
        (["red", 1], ["red"]),
        ({"red": 1}, ["red"]),
    ],
)
def test_resources_reject_invalid_json_manifests(
    tmp_path: Path, monkeypatch, feature_names, stable_features
):
    """验证空、重复、未知及非字符串清单名称受控失败。"""
    paths = _artifact_paths(tmp_path, feature_names, stable_features)
    _install_session(monkeypatch, _session())

    with pytest.raises(InferenceError) as error:
        load_spo2_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"


@pytest.mark.parametrize(
    ("input_shape", "output_shape", "input_type", "output_type", "input_count", "output_count"),
    [
        (("batch", 3), ("batch", 1), "tensor(float)", "tensor(float)", 1, 1),
        ((1, 2), ("batch", 1), "tensor(float)", "tensor(float)", 1, 1),
        (("batch", 2), ("batch", 2), "tensor(float)", "tensor(float)", 1, 1),
        (("batch", 2), ("batch", 1, 1), "tensor(float)", "tensor(float)", 1, 1),
        (("batch", 2), ("batch", 1), "tensor(float16)", "tensor(float)", 1, 1),
        (("batch", 2), ("batch", 1), "tensor(float)", "tensor(int64)", 1, 1),
        (("batch", 2), ("batch", 1), "tensor(float)", "tensor(float)", 2, 1),
        (("batch", 2), ("batch", 1), "tensor(float)", "tensor(float)", 1, 2),
    ],
)
def test_resources_reject_incompatible_model_signatures(
    tmp_path: Path,
    monkeypatch,
    input_shape,
    output_shape,
    input_type,
    output_type,
    input_count,
    output_count,
):
    """验证模型批维、特征宽度、数据类型和输出形状必须匹配。"""
    paths = _artifact_paths(tmp_path, ["red", "green"], ["red", "green"])
    _install_session(
        monkeypatch,
        _session(input_shape, output_shape, input_type, output_type, input_count, output_count),
    )

    with pytest.raises(InferenceError) as error:
        load_spo2_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"


def test_resources_map_onnx_load_failures_to_safe_artifact_errors(
    tmp_path: Path, monkeypatch
):
    """验证 ONNX 加载错误不向调用方泄露底层异常内容。"""
    paths = _artifact_paths(tmp_path, ["red"], ["red"])

    def fail_session(model_path: str, providers: list[str]) -> SimpleNamespace:
        """模拟底层运行时拒绝无效模型文件。"""
        raise RuntimeError("private ONNX parser details")

    monkeypatch.setattr("onnxruntime.InferenceSession", fail_session)
    with pytest.raises(InferenceError) as error:
        load_spo2_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"
    assert str(error.value) == "模型制品格式无效"
    assert "private ONNX parser details" not in str(error.value)
