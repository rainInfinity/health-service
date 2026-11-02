"""验证血压 v1 的实际 PPG 质量评分、固定门槛和门禁顺序。"""

from pathlib import Path

import numpy as np
import pytest
from health_inference import InferenceInput, InferenceOutput, QualityRejectedError
from health_inference.algorithms.ppg_acc_active_offline_bp_v1 import (
    algorithm as bp_algorithm,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.inference import (
    RawPrediction,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.quality import (
    BPQualityResult,
    BPQualityStrategy,
)
from health_inference.quality import LowScoreDetails
from health_inference.types import VersionResources


def _prepared(sampling_rate_hz: float = 30.75) -> PreprocessedBP:
    """创建用于质量策略测试的确定性清洗波形。"""
    return PreprocessedBP(
        ppg_timestamps_ms=(1000.0, 1032.5, 1065.0, 1097.5),
        scg_timestamps_ms=(1996.0, 2000.76, 2005.52, 2010.28),
        ppg_signal=(-1.0, 0.5, 1.0, -0.5),
        scg_signal=(0.2, -0.3, 0.7, -0.6),
        ppg_sampling_rate_hz=sampling_rate_hz,
        scg_sampling_rate_hz=210.0,
        profile={},
        channel_format="rgb",
    )


def test_quality_below_threshold_rejects_with_actual_score(monkeypatch):
    """验证 NeuroKit2 评分按旧整数采样率计算并保留未舍入低分。"""
    prepared = _prepared()
    observed_rates: list[int] = []

    def ppg_quality(signal, sampling_rate):
        """记录真实输入波形和采样率后返回阈值下的测试分数。"""
        np.testing.assert_array_equal(signal, np.asarray(prepared.ppg_signal))
        observed_rates.append(sampling_rate)
        return np.asarray([0.69999], dtype=np.float64)

    monkeypatch.setattr("neurokit2.ppg_quality", ppg_quality)

    with pytest.raises(QualityRejectedError) as error:
        BPQualityStrategy().evaluate(prepared)

    assert error.value.rejection.details.score == pytest.approx(69.999)
    assert error.value.rejection.details.threshold == QUALITY_THRESHOLD
    assert observed_rates == [30]


def test_quality_equal_to_threshold_continues_with_actual_score(monkeypatch):
    """验证质量分等于 70 时继续且不修改实际分数。"""
    monkeypatch.setattr(
        "neurokit2.ppg_quality",
        lambda signal, sampling_rate: np.asarray([0.7], dtype=np.float64),
    )

    result = BPQualityStrategy().evaluate(_prepared())

    assert result == BPQualityResult(score=70.0, threshold=QUALITY_THRESHOLD)


@pytest.mark.parametrize(
    "scores",
    [
        np.asarray([], dtype=np.float64),
        np.asarray([np.nan], dtype=np.float64),
        np.asarray([np.inf], dtype=np.float64),
    ],
    ids=("empty", "nan", "infinite"),
)
def test_invalid_quality_scores_are_not_quality_rejections(monkeypatch, scores):
    """验证空、NaN 和无穷评分失败而不伪造通过或质量拒绝。"""
    monkeypatch.setattr("neurokit2.ppg_quality", lambda signal, sampling_rate: scores)

    with pytest.raises(RuntimeError, match="未产生有限评分"):
        BPQualityStrategy().evaluate(_prepared())


def test_quality_evaluator_exception_is_not_reinterpreted(monkeypatch):
    """验证 NeuroKit2 意外异常原样终止，不映射为低分或默认通过。"""
    def fail_quality(signal, sampling_rate):
        """模拟质量依赖故障。"""
        raise RuntimeError("quality evaluator failed")

    monkeypatch.setattr("neurokit2.ppg_quality", fail_quality)

    with pytest.raises(RuntimeError, match="quality evaluator failed"):
        BPQualityStrategy().evaluate(_prepared())


def test_quality_rejection_stops_bp_runtime_before_ao_and_output(
    tmp_path: Path,
    monkeypatch,
):
    """验证正式血压运行时低分后不重建 AO、预测或写成功输出。"""
    calls: list[str] = []
    prepared = _prepared(30.0)
    output_dir = tmp_path / "execution"
    artifact_paths = {
        "sbp": tmp_path / "sbp.joblib",
        "dbp": tmp_path / "dbp.joblib",
        "normalize": tmp_path / "normalize.csv",
    }

    def load_input(inference_input: InferenceInput) -> object:
        """记录固定版本输入阶段。"""
        calls.append("load")
        return object()

    def preprocess(loaded: object) -> PreprocessedBP:
        """记录预处理阶段并提供已清洗 PPG。"""
        calls.append("preprocess")
        return prepared

    def predict(value: PreprocessedBP) -> RawPrediction:
        """记录不应在低分时执行的 AO 与双模型阶段。"""
        calls.append("predict")
        raise AssertionError("低分后不得执行 AO 重建或血压预测")

    def build_output(
        inference_input: InferenceInput,
        loaded: object,
        value: PreprocessedBP,
        quality: BPQualityResult | None,
        prediction: RawPrediction,
    ) -> InferenceOutput:
        """记录不应在低分时创建的权威输出。"""
        calls.append("output")
        raise AssertionError("低分后不得生成成功输出")

    def score_ppg(signal, sampling_rate):
        """记录质量门禁阶段并返回 69.999 分。"""
        calls.append("quality")
        return np.asarray([0.69999], dtype=np.float64)

    monkeypatch.setattr(bp_algorithm, "_load_input", load_input)
    monkeypatch.setattr(bp_algorithm, "preprocess_bp", preprocess)
    monkeypatch.setattr(bp_algorithm, "estimate_bp", predict)
    monkeypatch.setattr(bp_algorithm, "build_output", build_output)
    monkeypatch.setattr(bp_algorithm, "load_bp_resources", lambda paths: object())
    monkeypatch.setattr("neurokit2.ppg_quality", score_ppg)

    runtime = bp_algorithm.PPG_ACC_ACTIVE_OFFLINE_BP_V1.create_runtime(
        VersionResources(params={}, artifact_paths=artifact_paths)
    )
    inference_input = InferenceInput(
        files={"ppg": tmp_path / "ppg.csv", "acc": tmp_path / "acc.csv"},
        artifacts=artifact_paths,
        profile={},
        params={},
        output_dir=output_dir,
    )

    with pytest.raises(QualityRejectedError) as error:
        runtime.run(inference_input)

    assert error.value.rejection.details == LowScoreDetails(69.999, QUALITY_THRESHOLD)
    assert calls == ["load", "preprocess", "quality"]
    assert not output_dir.exists()
