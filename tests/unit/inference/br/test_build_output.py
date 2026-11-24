"""验证独立 BR 限幅、区间精度、非法结果拒绝及写文件前校验。"""

import math
from dataclasses import replace
from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_br_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput
from health_inference.utils.ppg_preprocessing import PreprocessedPPG
from health_inference.utils.ppg_quality import PPGQualityResult


@pytest.fixture
def output_context(tmp_path: Path):
    """创建输出构建器使用的固定单块波形和空输出目录。"""
    timestamps_ms = (0.0, 100.0, 200.0)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return (
        InferenceInput(
            files={"ppg": tmp_path / "input.csv"},
            artifacts={},
            profile={},
            params={},
            output_dir=output_dir,
        ),
        PPGInput(
            timestamps_ms=timestamps_ms,
            channels=((120.0, 128.0, 136.0),) * 3,
            channel_format="rgb",
            sampling_rate_hz=10.0,
        ),
        PreprocessedPPG(
            timestamps_ms=timestamps_ms,
            ppg_signal=(0.1, -0.2, 0.3),
            rsp_signal=(0.3, -0.2, 0.1),
            sampling_rate_hz=10.0,
            channel_format="rgb",
        ),
        PPGQualityResult(score=85.0, threshold=70),
    )


@pytest.mark.parametrize(
    ("prediction", "expected"),
    [
        (-4.125, 8.0),
        (7.999, 8.0),
        (8.0, 8.0),
        (12.3456789012345, 12.3456789012345),
        (20.0, 20.0),
        (20.001, 20.0),
    ],
)
def test_build_output_clamps_bounds_without_rounding_in_range(
    output_context, prediction: float, expected: float
):
    """上下界截断但不舍入区间内的原始 BR，且只写出呼吸波形。"""
    output = build_output(*output_context, prediction)

    assert output.payload == {
        "br": expected,
        "quality": {"score": 85.0, "threshold": 70},
    }
    assert output.metrics == {"br": expected}
    output_path = output_context[0].output_dir / "rsp-processed.csv"
    assert output.output_files == {"processed_rsp": output_path}
    assert output_path.is_file()


@pytest.mark.parametrize("prediction", [math.nan, math.inf, -math.inf, 12, True])
def test_build_output_rejects_invalid_raw_prediction_before_writing(
    output_context, prediction: object
):
    """非有限值和非原生浮点值不可被限幅转换为看似有效的呼吸率。"""
    with pytest.raises(InferenceError) as error:
        build_output(*output_context, prediction)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert error.value.safe_message == "推理结果无效"
    assert tuple(output_context[0].output_dir.iterdir()) == ()


@pytest.mark.parametrize(
    "quality",
    [
        None,
        PPGQualityResult(score=69.999, threshold=70),
        PPGQualityResult(score=math.nan, threshold=70),
        PPGQualityResult(score=85.0, threshold=69),
    ],
)
def test_build_output_rejects_invalid_quality_before_writing(output_context, quality):
    """无质量结论、非法评分或门禁不符时不写出成功文件。"""
    inference_input, loaded, prepared, _ = output_context
    with pytest.raises(InferenceError) as error:
        build_output(inference_input, loaded, prepared, quality, 12.0)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert tuple(inference_input.output_dir.iterdir()) == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"payload": {"br": 12.0, "hr": 72.0, "quality": {"score": 85.0, "threshold": 70}}},
        {"metrics": {"br": 12.1}},
        {"metrics": {"br": 12.0, "hr": 72.0}},
        {"metrics": {"br": 12}},
        {"output_files": {"processed_rsp": Path("other.csv")}},
        {"output_files": {"processed_rsp": "rsp-processed.csv"}},
        {"output_files": {"processed_ppg": Path("ppg-processed.csv")}},
    ],
)
def test_valid_output_rejects_inconsistent_metrics_or_output_contract(changes):
    """额外 HR、指标不一致、类型错误和非固定呼吸文件均被拒绝。"""
    output = InferenceOutput(
        payload={"br": 12.0, "quality": {"score": 85.0, "threshold": 70}},
        metrics={"br": 12.0},
        output_files={"processed_rsp": Path("rsp-processed.csv")},
    )

    with pytest.raises(InferenceError) as error:
        valid_output(replace(output, **changes))

    assert error.value.code == "RESULT.INVALID_OUTPUT"


@pytest.mark.parametrize("br", [7.999, 20.001, math.nan, math.inf, -math.inf])
def test_valid_output_rejects_invalid_final_br(br: float):
    """最终校验拒绝超界或非有限 BR，即使载荷与指标相同。"""
    output = InferenceOutput(
        payload={"br": br, "quality": {"score": 85.0, "threshold": 70}},
        metrics={"br": br},
        output_files={"processed_rsp": Path("rsp-processed.csv")},
    )

    with pytest.raises(InferenceError) as error:
        valid_output(output)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
