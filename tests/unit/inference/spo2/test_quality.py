"""验证血氧 v1 的实际质量评分、阈值边界和拒绝副作用。"""

from pathlib import Path

import numpy as np
import pytest
from health_inference import InferenceInput, InferenceOutput, QualityRejectedError
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    PreprocessedSpo2,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.quality import (
    QUALITY_THRESHOLD,
    Spo2QualityResult,
    Spo2QualityStrategy,
)
from health_inference.pipeline import OfflinePipeline
from health_inference.quality import LowScoreDetails


def _prepared(sampling_rate_hz: float = 30.75) -> PreprocessedSpo2:
    """创建适合质量策略的确定性任务内波形。"""
    return PreprocessedSpo2(
        feature_rgb=np.zeros((4, 3), dtype=np.float64),
        ppg_signal=np.asarray([-1.0, 0.5, 1.0, -0.5], dtype=np.float64),
        sampling_rate_hz=sampling_rate_hz,
        channel_format="rgb",
    )


def test_quality_below_threshold_rejects_with_actual_unrounded_score(monkeypatch):
    """验证 69.999 以原值拒绝，并沿用旧链整数采样率。"""
    observed_rates: list[int] = []

    def ppg_quality(signal, sampling_rate):
        """为边界测试返回固定的 NeuroKit2 质量值。"""
        observed_rates.append(sampling_rate)
        return np.asarray([0.69999], dtype=np.float64)

    monkeypatch.setattr("neurokit2.ppg_quality", ppg_quality)
    with pytest.raises(QualityRejectedError) as error:
        Spo2QualityStrategy().evaluate(_prepared())

    assert error.value.rejection.details.score == pytest.approx(69.999)
    assert error.value.rejection.details.threshold == QUALITY_THRESHOLD
    assert observed_rates == [30]


def test_quality_equal_to_threshold_continues_with_actual_score(monkeypatch):
    """验证评分等于 70 时允许继续，且不改写实际分数。"""
    monkeypatch.setattr(
        "neurokit2.ppg_quality",
        lambda signal, sampling_rate: np.asarray([0.7], dtype=np.float64),
    )

    result = Spo2QualityStrategy().evaluate(_prepared())

    assert result == Spo2QualityResult(score=70.0, threshold=70)


@pytest.mark.parametrize(
    "scores",
    [
        np.asarray([], dtype=np.float64),
        np.asarray([np.nan], dtype=np.float64),
        np.asarray([np.inf], dtype=np.float64),
    ],
)
def test_invalid_quality_scores_are_execution_errors(monkeypatch, scores):
    """验证空、NaN 和无穷评分不转成质量拒绝或默认通过。"""
    monkeypatch.setattr("neurokit2.ppg_quality", lambda signal, sampling_rate: scores)

    with pytest.raises(RuntimeError, match="未产生有限评分"):
        Spo2QualityStrategy().evaluate(_prepared())


def test_quality_evaluation_exceptions_are_not_reinterpreted(monkeypatch):
    """验证 NeuroKit2 异常原样终止，不伪造质量结论。"""
    def fail_quality(signal, sampling_rate):
        """模拟评分依赖发生意外错误。"""
        raise RuntimeError("quality evaluator failed")

    monkeypatch.setattr("neurokit2.ppg_quality", fail_quality)

    with pytest.raises(RuntimeError, match="quality evaluator failed"):
        Spo2QualityStrategy().evaluate(_prepared())


def test_spo2_rejection_stops_pipeline_before_success_output(
    tmp_path: Path, monkeypatch
):
    """验证实际 SPO2 质量拒绝不会进入预测或创建权威输出。"""
    calls: list[str] = []
    output_dir = tmp_path / "output"

    def load_input(inference_input: InferenceInput) -> str:
        """记录输入阶段并返回测试数据。"""
        calls.append("load")
        return "loaded"

    def preprocess(value: str) -> PreprocessedSpo2:
        """记录预处理阶段并生成有效测试波形。"""
        calls.append("preprocess")
        return _prepared(30.0)

    def ppg_quality(signal, sampling_rate):
        """记录质量阶段并返回低于固定门槛的评分。"""
        calls.append("quality")
        return np.asarray([0.69999], dtype=np.float64)

    def predict(prepared: PreprocessedSpo2) -> str:
        """记录低分时不应执行的模型阶段。"""
        calls.append("predict")
        return "prediction"

    def build_output(
        inference_input: InferenceInput,
        loaded: str,
        prepared: PreprocessedSpo2,
        quality: Spo2QualityResult | None,
        prediction: str,
    ) -> InferenceOutput:
        """创建仅在质量通过后才应出现的权威输出文件。"""
        calls.append("output")
        output_dir.mkdir(parents=True)
        output_path = output_dir / "processed_ppg.csv"
        output_path.write_text("timestampFrame,ppg\n", encoding="utf-8")
        return InferenceOutput(
            payload={"spo2": 90.0},
            metrics={"spo2": 90.0},
            output_files={"processed_ppg": output_path},
        )

    monkeypatch.setattr("neurokit2.ppg_quality", ppg_quality)
    pipeline = OfflinePipeline(
        load_input,
        preprocess,
        predict,
        build_output,
        quality_strategy=Spo2QualityStrategy(),
    )
    inference_input = InferenceInput(
        files={}, artifacts={}, profile={}, params={}, output_dir=output_dir
    )

    with pytest.raises(QualityRejectedError) as error:
        pipeline(inference_input)

    assert error.value.rejection.details == LowScoreDetails(69.999, 70)
    assert calls == ["load", "preprocess", "quality"]
    assert not output_dir.exists()
