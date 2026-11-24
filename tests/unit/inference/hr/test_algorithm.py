"""验证独立心率契约、真实波形输出以及与呼吸率计算的隔离。"""

import csv
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_hr_v1.algorithm import (
    ALGORITHM_KEY,
    PPG_ACTIVE_OFFLINE_HR_V1,
    _PIPELINE,
)
from health_inference.algorithms.ppg_active_offline_hr_v1.inference import estimate_hr
from health_inference.exceptions import InferenceError, QualityRejectedError
from health_inference.types import InferenceInput, VersionResources
from health_inference.utils.ppg_input import load_ppg
from health_inference.utils.ppg_preprocessing import preprocess_ppg
from health_inference.utils.ppg_quality import PPGQualityStrategy


def _signal_input(
    tmp_path: Path,
    *,
    sampling_rate_hz: float = 30.0,
    noise_amplitude: float = 0.0,
) -> InferenceInput:
    """写出含 72 BPM 脉搏及呼吸分量的确定性一分钟 RGB 波形。"""
    input_path = tmp_path / "input.csv"
    rng = random.Random(42)
    with input_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(("timestampFrame", "avgR", "avgG", "avgB"))
        for index in range(int(60 * sampling_rate_hz)):
            seconds = index / sampling_rate_hz
            red = (
                120.0
                + 4.0 * math.sin(2.0 * math.pi * 1.2 * seconds)
                + 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
                + noise_amplitude * rng.uniform(-1.0, 1.0)
            )
            writer.writerow((seconds * 1000.0, red, 128.0, 136.0))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return InferenceInput(
        files={"ppg": input_path}, artifacts={}, profile={}, params={}, output_dir=output_dir
    )


def test_definition_exposes_only_formal_fixed_hr_contract():
    """直接导入定义，不依赖外层注册，并检查零制品和空参数合同。"""
    definition = PPG_ACTIVE_OFFLINE_HR_V1
    assert definition.key == ALGORITHM_KEY == "ppg.active.offline.hr.v1"
    assert definition.is_example is False
    assert definition.required_inputs == ("ppg",)
    assert definition.input_extensions == {"ppg": frozenset({"csv"})}
    assert definition.required_artifacts == ()
    assert definition.artifact_extensions == {}
    assert definition.required_profile_fields == ()
    assert tuple(
        (metric.code, metric.value_type, metric.unit) for metric in definition.metric_contracts
    ) == (("hr", "number", "beats_per_minute"),)
    definition.validate_params({})
    with pytest.raises(InferenceError) as error:
        definition.validate_params({"sampling_rate": 30})
    assert error.value.code == "INFERENCE.INVALID_PARAMETERS"


def test_real_signal_preserves_hr_quality_and_only_processed_ppg_csv(tmp_path):
    """真实流水线输出实际质量及无损毫秒时间轴，不生成呼吸率结果或文件。"""
    inference_input = _signal_input(tmp_path)
    loaded = load_ppg(inference_input.files["ppg"])
    prepared = preprocess_ppg(loaded)
    expected_quality = PPGQualityStrategy().evaluate(prepared)

    runtime = PPG_ACTIVE_OFFLINE_HR_V1.create_runtime(VersionResources({}, {}))
    output = runtime.run(inference_input)

    assert set(output.payload) == {"hr", "quality"}
    assert type(output.payload["hr"]) is float
    assert output.payload["hr"] == pytest.approx(72.0, abs=0.2)
    assert output.metrics == {"hr": output.payload["hr"]}
    assert output.payload["quality"] == {
        "score": expected_quality.score,
        "threshold": 70,
    }
    assert output.payload["quality"]["score"] >= 70.0
    output_path = inference_input.output_dir / "ppg-processed.csv"
    assert output.output_files == {"processed_ppg": output_path}
    assert tuple(inference_input.output_dir.iterdir()) == (output_path,)
    assert b"\r\n" in output_path.read_bytes()
    with output_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ["timestamp", "ppg"]
        rows = list(reader)
    assert len(rows) == len(loaded.timestamps_ms)
    for row, timestamp, signal in zip(
        rows, loaded.timestamps_ms, prepared.ppg_signal, strict=True
    ):
        assert float(row["timestamp"]) == timestamp
        assert float(row["ppg"]) == pytest.approx(signal, abs=1e-14)


@pytest.mark.parametrize(
    ("sampling_rate_hz", "expected_hr"),
    [(30.0, 72.0), (29.5, 70.78411130202367)],
)
def test_hr_does_not_call_br_or_read_rsp_signal(
    tmp_path, monkeypatch, sampling_rate_hz, expected_hr
):
    """呼吸波无效且 BR 函数不可用时，仍按原有 int(fs) 规则估计心率。"""
    import neurokit2 as nk

    inference_input = _signal_input(tmp_path, sampling_rate_hz=sampling_rate_hz)
    prepared = preprocess_ppg(load_ppg(inference_input.files["ppg"]))

    def reject_br(*args, **kwargs):
        """一旦意外计算呼吸率，立即使测试失败。"""
        pytest.fail("独立心率不得调用 rsp_rate")

    monkeypatch.setattr(nk, "rsp_rate", reject_br)
    prediction = estimate_hr(replace(prepared, rsp_signal=(math.nan,) * len(prepared.rsp_signal)))

    assert type(prediction) is float
    assert prediction == pytest.approx(expected_hr, abs=0.2)


def test_real_low_quality_signal_is_rejected_before_writing(tmp_path):
    """真实低质量输入沿用公共质量门禁，不写出成功文件。"""
    inference_input = _signal_input(tmp_path, noise_amplitude=30.0)

    with pytest.raises(QualityRejectedError) as error:
        _PIPELINE(inference_input)

    assert error.value.rejection.reason.value == "LOW_SCORE"
    assert tuple(inference_input.output_dir.iterdir()) == ()
