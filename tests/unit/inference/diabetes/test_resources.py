"""验证糖尿病 v1 对固定 ONNX 输入和按名输出的资源契约。"""

from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.resources import (
    load_diabetes_resources,
)
from health_inference.exceptions import InferenceError


class _Node:
    """提供可控 ONNX 节点元数据。"""

    def __init__(self, name: str, type_name: str, shape: list[object]):
        """保存节点名、张量类型和静态/动态形状。"""
        self.name = name
        self.type = type_name
        self.shape = shape


class _Session:
    """按指定输出顺序公开固定的节点元数据。"""

    def __init__(self, outputs: list[_Node], inputs: list[_Node] | None = None):
        """保存待暴露的输入与输出节点。"""
        self._inputs = inputs or [_Node("input", "tensor(float)", [None, 918])]
        self._outputs = outputs

    def get_inputs(self) -> list[_Node]:
        """返回模型输入节点。"""
        return self._inputs

    def get_outputs(self) -> list[_Node]:
        """返回模型输出节点。"""
        return self._outputs


def _valid_session() -> _Session:
    """创建符合唯一 918 维 float 与二分类输出契约的会话。"""
    return _Session(
        [
            _Node("probabilities", "tensor(float)", [None, 2]),
            _Node("label", "tensor(int64)", [None]),
        ]
    )


def test_loads_outputs_by_name_when_onnx_lists_them_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """节点返回顺序不影响 label 与 probabilities 的绑定。"""
    model_path = tmp_path / "diabetes.onnx"
    model_path.write_bytes(b"model")
    session = _valid_session()
    monkeypatch.setattr("onnxruntime.InferenceSession", lambda *args, **kwargs: session)

    resources = load_diabetes_resources({"diabetes": model_path})

    assert resources.session is session
    assert resources.input_name == "input"
    assert resources.label_output_name == "label"
    assert resources.probabilities_output_name == "probabilities"


@pytest.mark.parametrize(
    "session",
    (
        _Session(
            [
                _Node("label", "tensor(int64)", [None]),
                _Node("probabilities", "tensor(float)", [None, 3]),
            ]
        ),
        _Session(
            [_Node("label", "tensor(int64)", [None]), _Node("other", "tensor(float)", [None, 2])]
        ),
        _Session(
            [
                _Node("label", "tensor(float)", [None]),
                _Node("probabilities", "tensor(float)", [None, 2]),
            ]
        ),
        _Session(
            [
                _Node("label", "tensor(int64)", [None]),
                _Node("probabilities", "tensor(float)", [None, 2]),
            ],
            inputs=[_Node("input", "tensor(float)", [None, 917])],
        ),
    ),
    ids=("wrong_probability_width", "wrong_output_name", "wrong_label_type", "wrong_input_width"),
)
def test_rejects_incompatible_model_node_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: _Session,
):
    """概率宽度、节点名称、标签类型和输入列数错误均拒绝加载。"""
    model_path = tmp_path / "invalid.onnx"
    model_path.write_bytes(b"model")
    monkeypatch.setattr("onnxruntime.InferenceSession", lambda *args, **kwargs: session)

    with pytest.raises(InferenceError) as error:
        load_diabetes_resources({"diabetes": model_path})

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"
