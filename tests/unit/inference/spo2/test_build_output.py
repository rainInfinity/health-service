"""验证血氧完整结果与处理后 PPG 文件保持同一任务的一致性。"""

import csv

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.inference import (
    Spo2InferencePrediction,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.postprocessing import (
    Spo2Prediction,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    PreprocessedSpo2,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.quality import (
    Spo2QualityResult,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput


def test_build_output_projects_final_spo2_and_writes_task_local_processed_ppg(
    tmp_path,
):
    """验证原始窗口值、百分数投影、窗口起点和 CSV 时间轴逐样本对应。"""
    output_dir = tmp_path / "task" / "execution-token"
    output_dir.mkdir(parents=True)
    timestamps_ms = tuple(float(index * 20) for index in range(525))
    processed_signal = np.linspace(-1.0, 1.0, len(timestamps_ms), dtype=np.float64)
    loaded = PPGInput(
        timestamps_ms=timestamps_ms,
        channels=tuple((10.0, 20.0, 30.0) for _ in timestamps_ms),
        channel_format="rgb",
        sampling_rate_hz=50.0,
    )
    prepared = PreprocessedSpo2(
        feature_rgb=np.zeros((len(timestamps_ms), 3), dtype=np.float64),
        ppg_signal=processed_signal,
        sampling_rate_hz=50.0,
        channel_format="rgb",
    )
    prediction = Spo2InferencePrediction(
        prediction=Spo2Prediction(window_predictions=(88.0, 103.0), spo2=100.0),
        window_starts=(0, 75),
    )
    inference_input = InferenceInput(
        files={"ppg": tmp_path / "input.csv"},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )

    output = build_output(
        inference_input,
        loaded,
        prepared,
        Spo2QualityResult(score=82.5, threshold=70),
        prediction,
    )

    assert output.payload == {
        "spo2": 100.0,
        "quality": {"score": 82.5, "threshold": 70},
        "window_predictions": [88.0, 103.0],
        "window_starts": [0, 75],
    }
    assert output.metrics == {"spo2": output.payload["spo2"]}
    assert str(tmp_path) not in repr(output.payload)
    assert set(output.output_files) == {"processed_ppg"}
    output_path = output.output_files["processed_ppg"]
    assert output_path.parent == output_dir
    assert output_path.name == "ppg-processed.csv"
    assert tuple(output_dir.iterdir()) == (output_path,)

    with output_path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)

    assert reader.fieldnames == ["timestampFrame", "ppg"]
    assert [float(row["timestampFrame"]) for row in rows] == list(timestamps_ms)
    assert [float(row["ppg"]) for row in rows] == pytest.approx(processed_signal)


def test_valid_output_rejects_a_projection_that_differs_from_window_predictions(
    tmp_path,
):
    """验证完整血氧值不能与窗口最大值或指标投影不一致。"""
    output = InferenceOutput(
        payload={
            "spo2": 96.0,
            "quality": {"score": 80.0, "threshold": 70},
            "window_predictions": [91.0, 95.0],
            "window_starts": [0, 75],
        },
        metrics={"spo2": 95.0},
        output_files={"processed_ppg": tmp_path / "ppg-processed.csv"},
    )

    with pytest.raises(InferenceError) as invalid_output:
        valid_output(output)

    assert invalid_output.value.code == "RESULT.INVALID_OUTPUT"
