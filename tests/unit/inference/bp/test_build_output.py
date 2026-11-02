"""验证血压 v1 双结果及隔离处理文件契约。"""

import csv
from pathlib import Path

import numpy as np
import pytest
from health_inference import InferenceError, InferenceInput
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.build_output import (
    build_output,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.inference import (
    RawPrediction,
    _predict_scalar,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    ACCInput,
    BPProfileSnapshot,
    LoadedBPInput,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.quality import (
    BPQualityResult,
)
from health_inference.utils.ppg_input import PPGInput


def _loaded_input() -> LoadedBPInput:
    """创建带原始毫秒时间轴的确定性双传感器输入。"""
    return LoadedBPInput(
        ppg=PPGInput(
            timestamps_ms=(1000.0, 1033.0, 1066.0),
            channels=((20.0, 30.0, 40.0),) * 3,
            channel_format="rgb",
            sampling_rate_hz=30.0,
        ),
        acc=ACCInput(
            timestamps_ms=(2000.0, 2010.0, 2020.0, 2030.0),
            channels=((0.0, 0.0, 0.0),) * 4,
            sampling_rate_hz=100.0,
        ),
        ppg_sampling_rate_hz=30.0,
        profile=BPProfileSnapshot(30, 170, 70, 1),
    )


def _prepared(ppg_signal: tuple[float, ...] = (0.123456789, -0.3, 1.23456789)):
    """创建清洗后的 PPG 与长于 AO 输出的 SCG 时间轴。"""
    return PreprocessedBP(
        ppg_timestamps_ms=(1000.0, 1033.0, 1066.0),
        scg_timestamps_ms=(2000.0, 2010.0, 2020.0, 2030.0),
        ppg_signal=ppg_signal,
        scg_signal=(0.1, -0.2, 0.3, -0.4),
        ppg_sampling_rate_hz=30.0,
        scg_sampling_rate_hz=100.0,
        profile=BPProfileSnapshot(30, 170, 70, 1),
        channel_format="rgb",
    )


def _inference_input(output_dir: Path) -> InferenceInput:
    """为结果构建器创建本任务独占输出目录。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    return InferenceInput(
        files={},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )


def _prediction(
    *,
    sbp: object = 250.123456789,
    dbp: object = -12.3456789,
    ao_signal: tuple[float, ...] = (0.3, -0.4, 1.25),
) -> RawPrediction:
    """创建单次推理的原始双模型结果与 AO 波形。"""
    return RawPrediction(
        sbp=sbp,
        dbp=dbp,
        ptt_ms=68.038462,
        pr=79,
        ao_signal=ao_signal,
    )


class _FixedPredictionModel:
    """为模型结果形状测试提供可控 predict 返回值。"""

    def __init__(self, output: object):
        """保存待返回的合成模型输出。"""
        self.output = output

    def predict(self, features):
        """返回指定输出，以覆盖推理结果边界。"""
        return self.output


def test_build_output_preserves_values_and_writes_fixed_csv_contract(tmp_path: Path):
    """验证原始双结果、指标一致、固定文件名及截断后的 SCG CSV。"""
    output_dir = tmp_path / "executions" / "task-1" / "token-a"
    inference_input = _inference_input(output_dir)
    prediction = _prediction()

    output = build_output(
        inference_input,
        _loaded_input(),
        _prepared(),
        BPQualityResult(score=88.65866718458334, threshold=70),
        prediction,
    )

    assert output.payload == {
        "sbp": 250.123456789,
        "dbp": -12.3456789,
        "quality": {"score": 88.65866718458334, "threshold": 70},
    }
    assert output.metrics == {"sbp": 250.123456789, "dbp": -12.3456789}
    assert output.output_files == {
        "processed_ppg": output_dir / "ppg-processed.csv",
        "processed_scg": output_dir / "scg-processed.csv",
    }
    assert output.output_files["processed_ppg"].name == "ppg-processed.csv"
    assert output.output_files["processed_scg"].name == "scg-processed.csv"

    with output.output_files["processed_ppg"].open(encoding="utf-8", newline="") as csv_file:
        ppg_rows = list(csv.DictReader(csv_file))
    with output.output_files["processed_scg"].open(encoding="utf-8", newline="") as csv_file:
        scg_rows = list(csv.DictReader(csv_file))

    assert ppg_rows == [
        {"timestamp": "1000.0", "ppg": "0.123456789"},
        {"timestamp": "1033.0", "ppg": "-0.3"},
        {"timestamp": "1066.0", "ppg": "1.23456789"},
    ]
    assert scg_rows == [
        {
            "timestamp": "2000.0",
            "scgX": "0",
            "scgY": "0",
            "scgZ": "0.3",
        },
        {
            "timestamp": "2010.0",
            "scgX": "0",
            "scgY": "0",
            "scgZ": "-0.4",
        },
        {
            "timestamp": "2020.0",
            "scgX": "0",
            "scgY": "0",
            "scgZ": "1.25",
        },
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("sbp", None),
        ("dbp", float("nan")),
        ("sbp", np.asarray([250.0])),
        ("dbp", float("inf")),
    ),
    ids=("empty_sbp", "nan_dbp", "shaped_sbp", "infinite_dbp"),
)
def test_build_output_rejects_one_invalid_prediction_without_writing_files(
    tmp_path: Path,
    field: str,
    value: object,
):
    """验证任一缺失、形状非法或非有限预测均阻止整组输出。"""
    output_dir = tmp_path / "executions" / "task-2" / "token-b"
    inference_input = _inference_input(output_dir)
    values = {"sbp": 250.123456789, "dbp": -12.3456789}
    values[field] = value

    with pytest.raises(InferenceError) as error:
        build_output(
            inference_input,
            _loaded_input(),
            _prepared(),
            BPQualityResult(score=80.0, threshold=70),
            _prediction(**values),
        )

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert list(output_dir.iterdir()) == []


@pytest.mark.parametrize(
    "model_output",
    (
        [],
        [[120.0]],
        [120.0, 121.0],
        [np.nan],
        [np.inf],
        ["120.0"],
        None,
    ),
    ids=("empty", "matrix", "multiple", "nan", "infinite", "nonnumeric", "none"),
)
def test_predict_scalar_rejects_empty_shaped_or_nonfinite_model_outputs(
    model_output: object,
):
    """验证模型输出必须恰为一个有限数值。"""
    model = _FixedPredictionModel(model_output)

    with pytest.raises(InferenceError) as error:
        _predict_scalar(model, np.zeros((1, 6), dtype=np.float64))

    assert error.value.code == "RESULT.INVALID_OUTPUT"


def test_build_output_keeps_each_task_and_execution_token_in_its_own_directory(
    tmp_path: Path,
):
    """验证不同任务与令牌只写各自固定名文件且不会相互覆盖。"""
    first_dir = tmp_path / "executions" / "task-10" / "token-first"
    second_dir = tmp_path / "executions" / "task-11" / "token-second"
    first = build_output(
        _inference_input(first_dir),
        _loaded_input(),
        _prepared(),
        BPQualityResult(score=80.0, threshold=70),
        _prediction(sbp=130.0, dbp=75.0),
    )
    second = build_output(
        _inference_input(second_dir),
        _loaded_input(),
        _prepared(ppg_signal=(0.9, 0.8, 0.7)),
        BPQualityResult(score=81.0, threshold=70),
        _prediction(sbp=140.0, dbp=85.0),
    )

    assert first.output_files["processed_ppg"] == first_dir / "ppg-processed.csv"
    assert first.output_files["processed_scg"] == first_dir / "scg-processed.csv"
    assert second.output_files["processed_ppg"] == second_dir / "ppg-processed.csv"
    assert second.output_files["processed_scg"] == second_dir / "scg-processed.csv"
    assert (
        first.output_files["processed_ppg"].read_bytes()
        != second.output_files["processed_ppg"].read_bytes()
    )
    assert (
        first.output_files["processed_scg"].read_bytes()
        == second.output_files["processed_scg"].read_bytes()
    )
