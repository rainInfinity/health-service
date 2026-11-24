"""验证独立心率原值保留及写文件前的最终结果校验。"""

import math
from dataclasses import replace

import pytest
from health_inference.algorithms.ppg_active_offline_hr_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput
from health_inference.utils.ppg_preprocessing import PreprocessedPPG
from health_inference.utils.ppg_quality import PPGQualityResult


@pytest.fixture
def output_context(tmp_path):
    """构建仅需三行波形的输出阶段上下文，不依赖外层注册。"""
    inference_input = InferenceInput(
        files={}, artifacts={}, profile={}, params={}, output_dir=tmp_path
    )
    loaded = PPGInput(
        timestamps_ms=(0.0, 100.0, 200.0),
        channels=((120.0, 128.0, 136.0),) * 3,
        channel_format="rgb",
        sampling_rate_hz=10.0,
    )
    prepared = PreprocessedPPG(
        timestamps_ms=loaded.timestamps_ms,
        ppg_signal=(0.1, -0.2, 0.3),
        rsp_signal=(0.3, -0.2, 0.1),
        sampling_rate_hz=10.0,
        channel_format="rgb",
    )
    return inference_input, loaded, prepared, PPGQualityResult(score=85.0, threshold=70)


@pytest.mark.parametrize("hr", [-12.3456789012345, 0.0, 312.3456789012345])
def test_build_output_keeps_every_finite_native_hr_without_clamping(output_context, hr):
    """有限原生浮点心率即使超出生理常见范围，也不被限幅或舍入。"""
    output = build_output(*output_context, hr)

    assert output.payload == {"hr": hr, "quality": {"score": 85.0, "threshold": 70}}
    assert output.metrics == {"hr": hr}
    assert output.output_files == {
        "processed_ppg": output_context[0].output_dir / "ppg-processed.csv"
    }


@pytest.mark.parametrize("hr", [math.nan, math.inf, -math.inf, 72, True])
def test_invalid_hr_is_rejected_before_writing_without_fallback(output_context, hr):
    """非有限结果及非原生浮点数直接报错，不回填默认心率或创建文件。"""
    with pytest.raises(InferenceError) as error:
        build_output(*output_context, hr)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
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
def test_invalid_quality_is_rejected_before_writing(output_context, quality):
    """缺失、不足或非法质量不能在写文件后才被拒绝。"""
    inference_input, loaded, prepared, _ = output_context

    with pytest.raises(InferenceError) as error:
        build_output(inference_input, loaded, prepared, quality, 72.0)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert tuple(inference_input.output_dir.iterdir()) == ()


@pytest.mark.parametrize("violation", ["extra_payload", "extra_metric", "metric", "filename", "extra_file"])
def test_validator_rejects_contract_drift(tmp_path, violation):
    """结果校验器拒绝额外 BR、指标不一致以及非固定的文件映射。"""
    output = InferenceOutput(
        payload={"hr": 72.0, "quality": {"score": 85.0, "threshold": 70}},
        metrics={"hr": 72.0},
        output_files={"processed_ppg": tmp_path / "ppg-processed.csv"},
    )
    if violation == "extra_payload":
        output = replace(output, payload={**output.payload, "br": 12.0})
    elif violation == "extra_metric":
        output = replace(output, metrics={"hr": 72.0, "br": 12.0})
    elif violation == "metric":
        output = replace(output, metrics={"hr": 73.0})
    elif violation == "filename":
        output = replace(output, output_files={"processed_ppg": tmp_path / "wrong.csv"})
    else:
        output = replace(
            output,
            output_files={**output.output_files, "processed_rsp": tmp_path / "rsp-processed.csv"},
        )

    with pytest.raises(InferenceError) as error:
        valid_output(output)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
