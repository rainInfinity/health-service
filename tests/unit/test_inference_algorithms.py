"""验证算法注册边界和纯计算包导入。"""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from health_inference import (
    UnknownAlgorithmError,
    get_algorithm,
    register_algorithm,
)


def test_retired_example_key_is_not_registered():
    """已移除的诊断标识不会被解析为可执行算法。"""
    with pytest.raises(UnknownAlgorithmError):
        get_algorithm("hr_br.example")




def test_registry_rejects_duplicate_builtin_algorithm():
    """验证同一算法定义不能通过重复注册改变现有契约。"""
    with pytest.raises(ValueError):
        register_algorithm(get_algorithm("ppg.active.offline.hr_br.v1"))



def test_registered_algorithms_expose_immutable_file_extensions():
    """正式算法的后缀映射与逻辑名一致且不可修改。"""
    hr_br = get_algorithm("ppg.active.offline.hr_br.v1")
    spo2 = get_algorithm("ppg.active.offline.spo2.v1")

    assert dict(hr_br.input_extensions) == {"ppg": frozenset({"csv"})}
    assert dict(hr_br.artifact_extensions) == {}
    assert dict(spo2.input_extensions) == {"ppg": frozenset({"csv"})}
    assert dict(spo2.artifact_extensions) == {
        "spo2": frozenset({"onnx"}),
        "feature_names": frozenset({"json"}),
        "stable_features": frozenset({"json"}),
    }
    with pytest.raises(TypeError):
        hr_br.input_extensions["ppg"] = frozenset({"json"})


@pytest.mark.parametrize(
    ("base_key", "field", "extensions"),
    [
        ("ppg.active.offline.hr_br.v1", "input_extensions", {}),
        (
            "ppg.active.offline.hr_br.v1",
            "input_extensions",
            {"other": frozenset({"csv"})},
        ),
        (
            "ppg.active.offline.spo2.v1",
            "artifact_extensions",
            {
                "spo2": frozenset({"onnx"}),
                "feature_names": frozenset({"json"}),
            },
        ),
        (
            "ppg.active.offline.spo2.v1",
            "artifact_extensions",
            {
                "spo2": frozenset({"onnx"}),
                "feature_names": frozenset({"json"}),
                "stable_features": frozenset({"json"}),
                "extra": frozenset({"json"}),
            },
        ),
        (
            "ppg.active.offline.hr_br.v1",
            "input_extensions",
            {"ppg": frozenset({".csv"})},
        ),
    ],
)
def test_registry_rejects_invalid_file_extension_contracts(base_key, field, extensions):
    """注册器拒绝缺失、额外逻辑名和非法后缀声明。"""
    definition = replace(
        get_algorithm(base_key),
        key="test.invalid.file-format-contract",
        **{field: extensions},
    )

    with pytest.raises(ValueError, match="文件格式契约"):
        register_algorithm(definition)


def test_health_inference_imports_without_django_initialization(tmp_path: Path):
    """验证已安装的纯计算包可在仓库外独立导入。"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import health_inference; assert 'django' not in sys.modules",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
