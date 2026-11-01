"""验证正式 HR/BR 输出、执行隔离和失败时不生成结果文件。"""

import csv
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest
from health_inference import (
    PPG_ACTIVE_OFFLINE_HR_BR_V1,
    InferenceInput,
    QualityRejectedError,
    VersionResources,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.algorithm import _PIPELINE
from health_inference.algorithms.ppg_active_offline_hr_br_v1.inference import RawPrediction
from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import (
    preprocess_ppg,
)
from health_inference.exceptions import InferenceError
from health_inference.utils.ppg_input import load_ppg


def _write_signal_csv(
    path: Path,
    *,
    heart_rate_hz: float = 1.2,
    noise_amplitude: float = 0.0,
) -> tuple[float, ...]:
    """写入确定性 30 Hz RGB 单块信号并返回其原始毫秒时间轴。"""
    sampling_rate_hz = 30.0
    sample_count = 1800
    rng = random.Random(42)
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(("timestampFrame", "avgR", "avgG", "avgB"))
        for index, timestamp_ms in enumerate(timestamps_ms):
            seconds = index / sampling_rate_hz
            red = (
                120.0
                + 4.0 * math.sin(2.0 * math.pi * heart_rate_hz * seconds)
                + 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
                + noise_amplitude * rng.uniform(-1.0, 1.0)
            )
            writer.writerow((timestamp_ms, red, 128.0, 136.0))
    return timestamps_ms


def _inference_input(input_path: Path, output_dir: Path) -> InferenceInput:
    """创建不依赖健康资料和制品的正式算法输入。"""
    output_dir.mkdir()
    return InferenceInput(
        files={"ppg": input_path},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )


def test_success_payload_metrics_and_processed_csvs_match_input(tmp_path):
    """成功载荷包含实际质量，指标和值文件与同次处理一致。"""
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "execution-one"
    timestamps_ms = _write_signal_csv(input_path)
    inference_input = _inference_input(input_path, output_dir)

    runtime = PPG_ACTIVE_OFFLINE_HR_BR_V1.create_runtime(VersionResources({}, {}))
    result = runtime.run(inference_input)

    assert set(result.payload) == {"hr", "br", "quality"}
    assert math.isfinite(result.payload["hr"])
    assert math.isfinite(result.payload["br"])
    assert result.payload["quality"]["threshold"] == 70
    assert result.payload["quality"]["score"] >= 70.0
    assert result.metrics == {"hr": result.payload["hr"], "br": result.payload["br"]}
    assert "output_files" not in result.payload
    assert str(tmp_path) not in repr(result.payload)
    assert set(result.output_files) == {"processed_ppg", "processed_rsp"}

    prepared = preprocess_ppg(load_ppg(input_path))
    for logical_name, signal_name, expected_signal, filename in (
        ("processed_ppg", "ppg", prepared.ppg_signal, "ppg-processed.csv"),
        ("processed_rsp", "rsp", prepared.rsp_signal, "rsp-processed.csv"),
    ):
        output_path = result.output_files[logical_name]
        assert output_path.parent == output_dir
        assert output_path.name == filename
        with output_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            assert reader.fieldnames == ["timestamp", signal_name]
            rows = list(reader)
        assert len(rows) == len(timestamps_ms)
        for row, timestamp_ms, expected_value in zip(
            rows, timestamps_ms, expected_signal, strict=True
        ):
            assert float(row["timestamp"]) == timestamp_ms
            assert float(row[signal_name]) == pytest.approx(expected_value, abs=1e-14)


def test_separate_executions_write_to_distinct_directories(tmp_path):
    """相邻任务输出路径互异且文件内容反映各自输入。"""
    input_one = tmp_path / "input-one.csv"
    input_two = tmp_path / "input-two.csv"
    _write_signal_csv(input_one, heart_rate_hz=1.2)
    _write_signal_csv(input_two, heart_rate_hz=1.5)

    runtime = PPG_ACTIVE_OFFLINE_HR_BR_V1.create_runtime(VersionResources({}, {}))
    result_one = runtime.run(_inference_input(input_one, tmp_path / "execution-one"))
    result_two = runtime.run(_inference_input(input_two, tmp_path / "execution-two"))

    ppg_one = result_one.output_files["processed_ppg"]
    ppg_two = result_two.output_files["processed_ppg"]
    assert ppg_one.parent != ppg_two.parent
    assert ppg_one.read_bytes() != ppg_two.read_bytes()
    assert result_one.metrics["hr"] != result_two.metrics["hr"]


def test_quality_rejection_creates_no_success_file_references(tmp_path):
    """实际低质量信号在输出前被拒绝且不留下处理文件。"""
    input_path = tmp_path / "low-quality.csv"
    _write_signal_csv(input_path, noise_amplitude=30.0)
    output_dir = tmp_path / "execution-low-quality"
    inference_input = _inference_input(input_path, output_dir)

    with pytest.raises(QualityRejectedError) as error:
        runtime = PPG_ACTIVE_OFFLINE_HR_BR_V1.create_runtime(VersionResources({}, {}))
        runtime.run(inference_input)

    assert error.value.rejection.reason.value == "LOW_SCORE"
    assert tuple(output_dir.iterdir()) == ()


def test_non_finite_prediction_creates_no_success_file_references(tmp_path):
    """结果错误在 CSV 写出前终止，不留下成功文件或引用。"""
    input_path = tmp_path / "input.csv"
    _write_signal_csv(input_path)
    output_dir = tmp_path / "execution-invalid-result"
    inference_input = _inference_input(input_path, output_dir)
    invalid_pipeline = replace(
        _PIPELINE,
        predict=lambda _prepared: RawPrediction(hr=72.0, br=math.inf),
    )

    with pytest.raises(InferenceError) as error:
        invalid_pipeline(inference_input)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert tuple(output_dir.iterdir()) == ()
