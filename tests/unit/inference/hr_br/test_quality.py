"""验证 HR/BR v1 实际评分、固定阈值和非法质量结果。"""

import math

import pytest
from health_inference import QualityRejectedError
from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import (
    preprocess_ppg,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.quality import (
    QUALITY_THRESHOLD,
    PPGQualityResult,
    PPGQualityStrategy,
    _quality_result_from_score,
)
from health_inference.quality import LowScoreDetails
from health_inference.utils.ppg_input import PPGInput


def _prepared_signal():
    """生成可由真实质量评价函数处理的确定性单块波形。"""
    import math as math_module

    sampling_rate_hz = 30.0
    sample_count = 1800
    timestamps_ms = tuple(index * 1000.0 / sampling_rate_hz for index in range(sample_count))
    channels = tuple(
        (
            120.0
            + 4.0 * math_module.sin(2.0 * math_module.pi * 1.2 * index / sampling_rate_hz)
            + 2.0 * math_module.sin(2.0 * math_module.pi * 0.2 * index / sampling_rate_hz),
            128.0,
            136.0,
        )
        for index in range(sample_count)
    )
    return preprocess_ppg(PPGInput(timestamps_ms, channels, "rgb", sampling_rate_hz))


def test_quality_strategy_uses_actual_neurokit_score():
    """实际质量策略返回真实 PPG 分数和固定阈值，不补造评分。"""
    result = PPGQualityStrategy().evaluate(_prepared_signal())

    assert isinstance(result, PPGQualityResult)
    assert math.isfinite(result.score)
    assert result.score >= QUALITY_THRESHOLD
    assert result.threshold == 70


@pytest.mark.parametrize("score", [69.999, 0.0])
def test_quality_score_below_threshold_is_rejected_with_actual_values(score: float):
    """低于阈值的评分以 LOW_SCORE 保留原分数及阈值。"""
    with pytest.raises(QualityRejectedError) as error:
        _quality_result_from_score(score)

    assert error.value.rejection.details == LowScoreDetails(score, 70)


@pytest.mark.parametrize("score", [70.0, 70.001])
def test_quality_score_at_or_above_threshold_continues(score: float):
    """分数等于或高于阈值时保留精确值并允许继续。"""
    result = _quality_result_from_score(score)

    assert result.score == score
    assert result.threshold == 70


@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf, True, "70"])
def test_invalid_quality_score_is_not_reinterpreted_as_pass_or_low_score(score: object):
    """非法评分作为执行错误终止，不降为低分拒绝或默认通过。"""
    with pytest.raises(RuntimeError, match="未产生有限评分"):
        _quality_result_from_score(score)
