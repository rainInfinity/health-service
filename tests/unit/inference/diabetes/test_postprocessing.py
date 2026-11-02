"""验证糖尿病 v1 的 ECMAScript 舍入和三分类边界。"""

import math

import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.inference import (
    RawDiabetesPrediction,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.postprocessing import (
    DiabetesPostprocessingStrategy,
    classify_probability,
)
from health_inference.exceptions import InferenceError


@pytest.mark.parametrize(
    ("probability", "risk_level"),
    (
        (0.0, 0),
        (0.2, 0),
        (0.654, 0),
        (math.nextafter(0.655, -math.inf), 0),
        (0.655, 1),
        (0.659, 1),
        (0.66, 1),
        (0.72381, 1),
        (0.754, 1),
        (math.nextafter(0.755, -math.inf), 1),
        (0.755, 2),
        (0.759, 2),
        (0.76, 2),
        (0.9, 2),
        (1.0, 2),
    ),
    ids=(
        "zero",
        "low",
        "below-0.655",
        "adjacent-below-0.655",
        "at-0.655",
        "below-0.66",
        "at-0.66",
        "medium",
        "below-0.76",
        "adjacent-below-0.755",
        "at-0.755",
        "above-0.755",
        "at-0.76",
        "high",
        "one",
    ),
)
def test_classifies_using_binary_float_to_fixed_boundaries(
    probability: float,
    risk_level: int,
):
    """舍入边界前后浮点值与旧客户端 toFixed 分类一致。"""
    assert classify_probability(probability) == risk_level


def test_postprocessing_preserves_the_unrounded_probability():
    """结果分类使用舍入值，但权威概率仍保留模型原始精度。"""
    prediction = DiabetesPostprocessingStrategy().apply(RawDiabetesPrediction(probability=0.655))

    assert prediction.probability == 0.655
    assert prediction.risk_level == 1


@pytest.mark.parametrize(
    "probability",
    (True, 0, 1, -0.01, 1.01, float("nan"), float("inf"), -float("inf")),
    ids=(
        "bool",
        "integer-zero",
        "integer-one",
        "negative",
        "above-one",
        "nan",
        "positive-infinity",
        "negative-infinity",
    ),
)
def test_rejects_non_float_non_finite_and_out_of_range_probabilities(probability: object):
    """分类器只接受 [0,1] 内有限原生 float 概率。"""
    with pytest.raises(InferenceError) as error:
        classify_probability(probability)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
