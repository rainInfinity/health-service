"""验证输出构建、BR 上限、非有限拒绝和最终结果校验。"""

import csv
import math
from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_hr_br_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.inference import RawPrediction
from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import PreprocessedPPG
from health_inference.algorithms.ppg_active_offline_hr_br_v1.quality import PPGQualityResult
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput


def _output_context(
    tmp_path: Path,
) -> tuple[InferenceInput, PPGInput, PreprocessedPPG, PPGQualityResult]:
    """创建结果构建器需要的固定单块数据。"""
    timestamps_ms = (0.0, 100.0, 200.0)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    inference_input = InferenceInput(
        files={"ppg": tmp_path / "input.csv"},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )
    loaded = PPGInput(
        timestamps_ms=timestamps_ms,
        channels=((120.0, 128.0, 136.0),) * 3,
        channel_format="rgb",
        sampling_rate_hz=10.0,
    )
    prepared = PreprocessedPPG(
        timestamps_ms=timestamps_ms,
        ppg_signal=(0.1, -0.2, 0.3),
        rsp_signal=(0.3, -0.2, 0.1),
        sampling_rate_hz=10.0,
        channel_format="rgb",
    )
    return (
        inference_input,
        loaded,
        prepared,
        PPGQualityResult(score=85.0, threshold=70),
    )


@pytest.mark.parametrize(
    ("raw_br", "expected_br"),
    [(-4.125, -4.125), (18.0, 18.0), (21.75, 18.0)],
)
def test_build_output_preserves_finite_hr_and_only_caps_br(
    tmp_path: Path,
    raw_br: float,
    expected_br: float,
):
    """保留有限 HR 与低 BR，只将超上限 BR 截为 18。"""
    inference_input, loaded, prepared, quality = _output_context(tmp_path)
    output = build_output(
        inference_input,
        loaded,
        prepared,
        quality,
        RawPrediction(hr=-123.456789, br=raw_br),
    )

    assert output.payload["hr"] == -123.456789
    assert output.payload["br"] == expected_br
    assert output.metrics == {"hr": -123.456789, "br": expected_br}
    assert output.output_files["processed_ppg"].is_file()
    assert output.output_files["processed_rsp"].is_file()
    with output.output_files["processed_ppg"].open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [float(row["timestamp"]) for row in rows] == [0.0, 100.0, 200.0]
    assert [float(row["ppg"]) for row in rows] == pytest.approx((0.1, -0.2, 0.3))


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("hr", math.nan),
        ("hr", math.inf),
        ("hr", -math.inf),
        ("br", math.nan),
        ("br", math.inf),
        ("br", -math.inf),
    ],
)
def test_build_output_rejects_non_finite_raw_values_before_writing(
    tmp_path: Path,
    field: str,
    invalid_value: float,
):
    """任一原始 HR/BR 非有限时拒绝结果且不留下文件。"""
    inference_input, loaded, prepared, quality = _output_context(tmp_path)
    values = {"hr": 72.25, "br": 25.0}
    values[field] = invalid_value

    with pytest.raises(InferenceError) as error:
        build_output(
            inference_input,
            loaded,
            prepared,
            quality,
            RawPrediction(**values),
        )

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert error.value.safe_message == "推理结果无效"
    assert tuple(inference_input.output_dir.iterdir()) == ()


def test_valid_output_rejects_metrics_that_disagree_with_payload(tmp_path: Path):
    """最终结果校验器拒绝与载荷不一致的投影指标。"""
    output = InferenceOutput(
        payload={"hr": 72.0, "br": 18.0, "quality": {"score": 85.0, "threshold": 70}},
        metrics={"hr": 72.0, "br": 17.99},
        output_files={
            "processed_ppg": tmp_path / "ppg-processed.csv",
            "processed_rsp": tmp_path / "rsp-processed.csv",
        },
    )

    with pytest.raises(InferenceError) as error:
        valid_output(output)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
