"""验证独立 BR 的正式契约、真实合成信号及唯一呼吸波形输出。"""

import csv
import math
from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_br_v1.algorithm import (
    ALGORITHM_KEY,
    PPG_ACTIVE_OFFLINE_BR_V1,
    _PIPELINE,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, VersionResources
from health_inference.utils.ppg_input import load_ppg
from health_inference.utils.ppg_preprocessing import preprocess_ppg


def test_formal_definition_has_only_br_and_accepts_only_empty_parameters():
    """直接导入的正式定义只声明 BR，并保留固定无制品、无资料契约。"""
    definition = PPG_ACTIVE_OFFLINE_BR_V1
    assert definition.key == ALGORITHM_KEY == "ppg.active.offline.br.v1"
    assert definition.is_example is False
    assert definition.required_inputs == ("ppg",)
    assert definition.input_extensions == {"ppg": frozenset({"csv"})}
    assert definition.required_artifacts == ()
    assert definition.artifact_extensions == {}
    assert definition.required_profile_fields == ()
    assert tuple(
        (contract.code, contract.value_type, contract.unit)
        for contract in definition.metric_contracts
    ) == (("br", "number", "breaths_per_minute"),)
    assert definition.create_runtime(VersionResources({}, {})).executor is _PIPELINE
    definition.validate_params({})

    with pytest.raises(InferenceError) as error:
        definition.validate_params({"sampling_rate": 30})

    assert error.value.code == "INFERENCE.INVALID_PARAMETERS"


@pytest.mark.parametrize(
    ("sampling_rate_hz", "expected_br"),
    [(30.0, 12.03265584092234), (29.5, 11.817181891662122)],
)
def test_real_signal_preserves_br_estimate_and_writes_only_rsp_csv(
    tmp_path: Path, sampling_rate_hz: float, expected_br: float
):
    """实际运行共享前置及呼吸估计，保留旧链 int(fs) 解释和 CSV 合同。"""
    input_path = tmp_path / "input.csv"
    sample_count = int(60 * sampling_rate_hz)
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    with input_path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(("timestampFrame", "avgR", "avgG", "avgB"))
        for index, timestamp_ms in enumerate(timestamps_ms):
            seconds = index / sampling_rate_hz
            red = (
                120.0
                + 4.0 * math.sin(2.0 * math.pi * 1.2 * seconds)
                + 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
            )
            writer.writerow((timestamp_ms, red, 128.0, 136.0))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    inference_input = InferenceInput(
        files={"ppg": input_path},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )

    runtime = PPG_ACTIVE_OFFLINE_BR_V1.create_runtime(VersionResources({}, {}))
    result = runtime.run(inference_input)

    assert set(result.payload) == {"br", "quality"}
    assert result.payload["br"] == pytest.approx(expected_br, abs=0.05)
    assert result.payload["quality"]["threshold"] == 70
    assert result.payload["quality"]["score"] >= 70.0
    assert result.metrics == {"br": result.payload["br"]}
    output_path = output_dir / "rsp-processed.csv"
    assert result.output_files == {"processed_rsp": output_path}
    assert tuple(output_dir.iterdir()) == (output_path,)
    prepared = preprocess_ppg(load_ppg(input_path))
    with output_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ["timestamp", "rsp"]
        rows = list(reader)
    assert len(rows) == sample_count
    for row, timestamp_ms, expected_signal in zip(
        rows, timestamps_ms, prepared.rsp_signal, strict=True
    ):
        assert float(row["timestamp"]) == timestamp_ms
        assert float(row["rsp"]) == pytest.approx(expected_signal, abs=1e-14)
    assert output_path.read_bytes().count(b"\r\n") == sample_count + 1
