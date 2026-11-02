"""验证糖尿病 v1 的真实质量评分边界与低分拒绝详情。"""

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.quality import (
    DiabetesQualityStrategy,
)
from health_inference.exceptions import QualityRejectedError
from health_inference.pipeline import OfflinePipeline
from health_inference.quality import QualityRejectionReason
from health_inference.types import InferenceInput, InferenceOutput


def _prepared() -> PreprocessedDiabetes:
    """创建仅供质量策略接收的有限 30 Hz 波形。"""
    return PreprocessedDiabetes(
        timestamps_ms=tuple(float(index) for index in range(450)),
        ppg_signal=np.linspace(-1.0, 1.0, 450),
        sampling_rate_hz=30.0,
        channel_format="rgb",
    )


@pytest.mark.parametrize(("score", "accepted"), ((70.0, True), (69.999, False)))
def test_quality_uses_strict_seventy_point_threshold(
    monkeypatch: pytest.MonkeyPatch,
    score: float,
    accepted: bool,
):
    """分数等于 70 时放行，低于 70 时携带未舍入分数拒绝。"""
    monkeypatch.setattr(
        "neurokit2.ppg_quality",
        lambda signal, sampling_rate: np.full(len(signal), score / 100.0),
    )

    if accepted:
        result = DiabetesQualityStrategy().evaluate(_prepared())
        assert result.score == score
        assert result.threshold == 70
    else:
        with pytest.raises(QualityRejectedError) as error:
            DiabetesQualityStrategy().evaluate(_prepared())
        assert error.value.rejection.reason is QualityRejectionReason.LOW_SCORE
        assert error.value.rejection.details.score == pytest.approx(score)
        assert error.value.rejection.details.threshold == 70


def test_quality_rejects_non_finite_neurokit_scores(monkeypatch: pytest.MonkeyPatch):
    """NeuroKit2 未返回有限质量值时不得继续调用模型。"""
    monkeypatch.setattr(
        "neurokit2.ppg_quality",
        lambda signal, sampling_rate: np.full(len(signal), np.nan),
    )

    with pytest.raises(RuntimeError, match="有限评分"):
        DiabetesQualityStrategy().evaluate(_prepared())


def test_low_quality_stops_prediction_and_success_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """低分门禁终止推理阶段，不创建成功文件或指标投影。"""
    monkeypatch.setattr(
        "neurokit2.ppg_quality",
        lambda signal, sampling_rate: np.full(len(signal), 0.69999),
    )
    output_path = tmp_path / "ppg-processed.csv"
    predictions: list[object] = []
    metrics: list[object] = []

    def predict(prepared):
        """记录是否错误进入模型阶段。"""
        predictions.append(prepared)
        return object()

    def build_output(inference_input, loaded, prepared, quality, prediction):
        """记录成功输出阶段的副作用。"""
        output_path.write_text("timestampFrame,ppg\n", encoding="utf-8")
        metrics.append({"diabetes_probability": 0.5})
        return InferenceOutput(
            payload={},
            metrics={"diabetes_probability": 0.5},
            output_files={"processed_ppg": output_path},
        )

    pipeline = OfflinePipeline(
        load_input=lambda inference_input: inference_input,
        preprocess=lambda _loaded: _prepared(),
        quality_strategy=DiabetesQualityStrategy(),
        predict=predict,
        build_output=build_output,
    )
    inference_input = InferenceInput(
        files={},
        artifacts={},
        profile={},
        params={},
        output_dir=tmp_path,
    )

    with pytest.raises(QualityRejectedError):
        pipeline(inference_input)

    assert predictions == []
    assert metrics == []
    assert not output_path.exists()
