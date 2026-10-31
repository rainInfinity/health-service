"""验证算法注册边界和纯计算包导入。"""

import os
import subprocess
import sys
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
