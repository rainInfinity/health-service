"""验证离线模板阶段顺序、质量门禁及任务数据隔离。"""

from pathlib import Path

import pytest
from health_inference import (
    InferenceInput,
    InferenceOutput,
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
)
from health_inference.pipeline import OfflinePipeline


def _make_input(path: Path, content: str, output_dir: Path) -> InferenceInput:
    """为流水线行为测试写入隔离输入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return InferenceInput(
        files={"ppg": path},
        artifacts={},
        profile={},
        params={},
        output_dir=output_dir,
    )


class _RejectQuality:
    """提供可重复的低分质量拒绝。"""

    def __init__(self, calls: list[str]) -> None:
        """保存测试阶段记录器。"""
        self.calls = calls

    def evaluate(self, prepared: str) -> str:
        """记录质量阶段后拒绝当前输入。"""
        self.calls.append("quality")
        raise QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(score=69.999, threshold=70),
            )
        )


class _AddTen:
    """将原始预测按测试规则加十。"""

    def apply(self, prediction: int) -> int:
        """返回模拟固定后处理后的预测。"""
        return prediction + 10


def test_quality_rejection_stops_prediction_and_output(tmp_path: Path):
    """质量拒绝后不调用预测或输出，也不创建成功文件。"""
    calls: list[str] = []

    def load_input(inference_input: InferenceInput) -> str:
        """读取本次任务的输入并记录阶段。"""
        calls.append("load")
        return inference_input.files["ppg"].read_text(encoding="utf-8")

    def preprocess(value: str) -> str:
        """原样形成测试用预处理数据。"""
        calls.append("preprocess")
        return value

    def predict(value: str) -> str:
        """记录不应执行的预测阶段。"""
        calls.append("predict")
        return value

    def build_output(
        inference_input: InferenceInput,
        loaded: str,
        prepared: str,
        quality: str | None,
        prediction: str,
    ) -> InferenceOutput:
        """记录不应执行的成功输出阶段。"""
        calls.append("output")
        return InferenceOutput({"prediction": prediction}, {}, {})

    pipeline = OfflinePipeline(
        load_input,
        preprocess,
        predict,
        build_output,
        quality_strategy=_RejectQuality(calls),
    )

    with pytest.raises(QualityRejectedError) as error:
        pipeline(_make_input(tmp_path / "input.csv", "signal", tmp_path / "output"))

    assert error.value.rejection.details == LowScoreDetails(69.999, 70)
    assert calls == ["load", "preprocess", "quality"]
    assert not (tmp_path / "output").exists()


def test_skipped_quality_has_no_pass_conclusion(tmp_path: Path):
    """未配置质量策略时输出收到空质量结论而非默认通过。"""
    pipeline = OfflinePipeline(
        load_input=lambda inference_input: inference_input.files["ppg"].read_text(encoding="utf-8"),
        preprocess=lambda value: value,
        predict=lambda value: value,
        build_output=lambda inference_input, loaded, prepared, quality, prediction: InferenceOutput(
            {"quality": quality, "prediction": prediction}, {}, {}
        ),
    )

    result = pipeline(_make_input(tmp_path / "input.csv", "signal", tmp_path / "output"))

    assert result.payload == {"quality": None, "prediction": "signal"}
    assert "quality_passed" not in result.payload


def test_postprocessed_prediction_is_used_for_success_output(tmp_path: Path):
    """完整成功结果使用后处理值而不是原始预测值。"""
    pipeline = OfflinePipeline(
        load_input=lambda inference_input: int(
            inference_input.files["ppg"].read_text(encoding="utf-8")
        ),
        preprocess=lambda value: value * 2,
        predict=lambda value: value + 3,
        build_output=lambda inference_input, loaded, prepared, quality, prediction: InferenceOutput(
            {"hr": prediction}, {"hr": prediction}, {}
        ),
        postprocessing_strategy=_AddTen(),
    )

    result = pipeline(_make_input(tmp_path / "input.csv", "4", tmp_path / "output"))

    assert result.payload == {"hr": 21}
    assert result.metrics == {"hr": 21}


def test_shared_pipeline_keeps_adjacent_task_data_separate(tmp_path: Path):
    """同一模板连续处理两个文件时，各自结果只使用对应输入。"""
    pipeline = OfflinePipeline(
        load_input=lambda inference_input: inference_input.files["ppg"].read_text(encoding="utf-8"),
        preprocess=lambda value: int(value),
        predict=lambda value: value * 2,
        build_output=lambda inference_input, loaded, prepared, quality, prediction: InferenceOutput(
            {"sample": prediction}, {"sample": prediction}, {}
        ),
    )

    first = pipeline(_make_input(tmp_path / "first.csv", "4", tmp_path / "first-output"))
    second = pipeline(_make_input(tmp_path / "second.csv", "9", tmp_path / "second-output"))

    assert first.payload == {"sample": 8}
    assert second.payload == {"sample": 18}
    assert first.output_files == second.output_files == {}
