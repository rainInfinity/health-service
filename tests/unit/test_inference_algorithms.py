"""验证受控算法注册和 PPG 示例输入诊断行为。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from health_inference import get_algorithm, register_algorithm
from health_inference.algorithms.hr_br.template import HR_BR_TEMPLATE
from health_inference.exceptions import InferenceError, UnknownAlgorithmError
from health_inference.types import InferenceInput


def _run_example(tmp_path: Path, content: str, params: dict[str, object] | None = None):
    """将给定 CSV 交给受控示例执行器。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ppg_path = tmp_path / "ppg.csv"
    ppg_path.write_text(content, encoding="utf-8")
    definition = get_algorithm("hr_br.example")
    inference_input = InferenceInput(
        files={"ppg": ppg_path},
        artifacts={},
        profile={},
        params=params or {},
        output_dir=tmp_path / "output",
    )
    return definition.run(inference_input)


def test_example_uses_actual_rgb_and_yuv_samples(tmp_path: Path):
    """验证不同 RGB/YUV 文件产生各自真实的样本数和时间诊断。"""
    rgb = _run_example(
        tmp_path / "rgb",
        "timestampFrame,avgR,avgG,avgB\n"
        "100,100,120,130\n"
        "300,110,121,132\n"
        "600,115,122,135\n",
    )
    yuv = _run_example(
        tmp_path / "yuv",
        "timestampFrame,avgY,avgU,avgV\n"
        "0,120,128,128\n"
        "500,121,129,127\n"
        "1500,122,130,126\n"
        "2000,123,131,125\n",
    )

    assert rgb.payload == {
        "example": True,
        "sample_count": 3,
        "duration_seconds": 0.5,
        "sampling_rate_hz": 4.0,
    }
    assert yuv.payload == {
        "example": True,
        "sample_count": 4,
        "duration_seconds": 2.0,
        "sampling_rate_hz": 1.5,
    }
    assert set(rgb.metrics) == {
        "example.ppg.sample_count",
        "example.ppg.duration_seconds",
        "example.ppg.sampling_rate_hz",
    }
    assert "hr" not in rgb.payload and "br" not in rgb.payload
    assert rgb.payload != yuv.payload


@pytest.mark.parametrize(
    "content",
    [
        "timestampFrame,avgR,avgG\n0,1,2\n100,2,3\n",
        "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n",
        "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,nan,2,3\n",
        "timestampFrame,avgR,avgG,avgB\n100,1,2,3\n100,2,3,4\n",
        "timestampFrame,avgR,avgG,avgB\n200,1,2,3\n100,2,3,4\n",
    ],
)
def test_example_rejects_invalid_ppg_csv(tmp_path: Path, content: str):
    """验证缺列、样本不足、非有限通道值及非递增时间戳安全失败。"""
    with pytest.raises(InferenceError) as error:
        _run_example(tmp_path, content)

    assert error.value.code == "INFERENCE.INVALID_INPUT"
    assert str(tmp_path) not in error.value.safe_message


def test_unimplemented_formal_algorithm_is_not_registered():
    """验证正式 HR/BR 模板不可执行，未知标识不能查到执行器。"""
    assert HR_BR_TEMPLATE.key == "hr_br"
    assert HR_BR_TEMPLATE.executable is False
    assert HR_BR_TEMPLATE.required_inputs == ("ppg",)
    assert HR_BR_TEMPLATE.required_artifacts == ()
    assert dict(HR_BR_TEMPLATE.result_units) == {
        "hr": "beats_per_minute",
        "br": "breaths_per_minute",
    }
    with pytest.raises(UnknownAlgorithmError):
        get_algorithm("hr_br")


def test_example_parameters_cannot_change_its_identity(tmp_path: Path):
    """验证客户端参数不能把示例伪装为正式预测。"""
    with pytest.raises(InferenceError) as error:
        _run_example(
            tmp_path,
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,2,3,4\n",
            {"isExample": False},
        )

    assert error.value.code == "INFERENCE.INVALID_PARAMETERS"


def test_registry_rejects_duplicate_builtin_algorithm():
    """验证同一算法定义不能通过重复注册改变现有契约。"""
    with pytest.raises(ValueError, match="算法标识已注册"):
        register_algorithm(get_algorithm("hr_br.example"))


def test_health_inference_imports_without_django_initialization():
    """验证纯计算包在未导入 Django 的独立进程中可用。"""
    source_root = Path(__file__).resolve().parents[2] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source_root)}
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import health_inference; assert 'django' not in sys.modules",
        ],
        cwd=source_root.parent,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
