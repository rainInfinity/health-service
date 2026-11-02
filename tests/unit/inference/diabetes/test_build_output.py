"""验证糖尿病 v1 结果、指标及处理后 PPG 文件契约。"""

import csv
from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.build_output import (
    build_output,
    valid_output,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.postprocessing import (
    DiabetesPrediction,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.quality import (
    DiabetesQualityResult,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput


def _input(output_dir: Path) -> InferenceInput:
    """创建当前任务独占输出目录和固定资源映射。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    return InferenceInput(
        files={},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )


def _loaded() -> PPGInput:
    """创建具有原始毫秒时间轴的 RGB 输入。"""
    return PPGInput(
        timestamps_ms=(1000.0, 1033.0, 1066.0),
        channels=((120.0, 128.0, 136.0),) * 3,
        channel_format="rgb",
        sampling_rate_hz=30.0,
    )


def _prepared() -> PreprocessedDiabetes:
    """创建与原始时间轴逐帧对应的清洗 PPG。"""
    return PreprocessedDiabetes(
        timestamps_ms=(1000.0, 1033.0, 1066.0),
        ppg_signal=(0.125, -0.25, 0.5),
        sampling_rate_hz=30.0,
        channel_format="rgb",
    )


def test_builds_probability_metric_and_processed_ppg_csv(tmp_path: Path):
    """成功结果保留原始概率、分类、质量及固定 CSV 列名和行数。"""
    output_dir = tmp_path / "executions" / "task-a" / "token-a"
    output = build_output(
        _input(output_dir),
        _loaded(),
        _prepared(),
        DiabetesQualityResult(score=70.0, threshold=70),
        DiabetesPrediction(probability=0.655, risk_level=1),
    )

    assert output.payload == {
        "prob": 0.655,
        "risk_level": 1,
        "quality": {"score": 70.0, "threshold": 70},
    }
    assert output.metrics == {"diabetes_probability": 0.655}
    assert output.output_files == {"processed_ppg": output_dir / "ppg-processed.csv"}
    with output.output_files["processed_ppg"].open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ["timestampFrame", "ppg"]
        assert list(reader) == [
            {"timestampFrame": "1000.0", "ppg": "0.125"},
            {"timestampFrame": "1033.0", "ppg": "-0.25"},
            {"timestampFrame": "1066.0", "ppg": "0.5"},
        ]


def test_rejects_inconsistent_risk_level_before_creating_a_file(tmp_path: Path):
    """概率与分类不一致时结果无效且不落盘。"""
    output_dir = tmp_path / "executions" / "task-b" / "token-b"
    output_dir.mkdir(parents=True)
    invalid = InferenceOutput(
        payload={
            "prob": 0.655,
            "risk_level": 0,
            "quality": {"score": 70.0, "threshold": 70},
        },
        metrics={"diabetes_probability": 0.655},
        output_files={"processed_ppg": output_dir / "ppg-processed.csv"},
    )

    with pytest.raises(InferenceError) as error:
        valid_output(invalid)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert list(output_dir.iterdir()) == []
