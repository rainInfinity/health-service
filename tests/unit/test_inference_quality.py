"""验证质量拒绝契约的原因、明细及数值边界。"""

import math
import os
import subprocess
import sys

import pytest
from health_inference import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)
from health_inference.quality import quality_rejection_from_mapping


def test_quality_rejection_accepts_scored_and_unscored_reasons():
    """验证低分拒绝保留实际比较值且无信号原因不要求评分。"""
    low_score = QualityRejection(
        QualityRejectionReason.LOW_SCORE,
        LowScoreDetails(40, 70),
    )
    no_signal = QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None)

    assert low_score.details == LowScoreDetails(40, 70)
    assert no_signal.details is None


def test_low_score_preserves_unrounded_values_near_threshold():
    """验证接近阈值的分数不会被展示精度改写。"""
    details = LowScoreDetails(69.999, 70)

    assert details.score == 69.999
    assert details.threshold == 70


@pytest.mark.parametrize("score", [70, 71], ids=("equal", "above"))
def test_low_score_rejects_score_not_below_threshold(score: int):
    """验证分数等于或高于阈值时不能构造低分拒绝。"""
    with pytest.raises(ValueError):
        LowScoreDetails(score, 70)


@pytest.mark.parametrize(
    ("score", "threshold"),
    [
        (True, 70),
        (40, False),
        ("40", 70),
        (40, "70"),
        (math.nan, 70),
        (40, math.nan),
        (math.inf, 70),
        (40, math.inf),
        (-math.inf, 70),
        (40, -math.inf),
    ],
    ids=(
        "boolean-score",
        "boolean-threshold",
        "string-score",
        "string-threshold",
        "nan-score",
        "nan-threshold",
        "positive-infinity-score",
        "positive-infinity-threshold",
        "negative-infinity-score",
        "negative-infinity-threshold",
    ),
)
def test_low_score_rejects_invalid_numeric_values(score: object, threshold: object):
    """验证评分和阈值只接受有限的原生数值。"""
    with pytest.raises(ValueError):
        LowScoreDetails(score, threshold)


def test_quality_rejection_rejects_unknown_or_mismatched_reason_details():
    """验证未知原因及原因和明细不匹配的组合不能成立。"""
    with pytest.raises(TypeError):
        QualityRejection("UNKNOWN", None)
    with pytest.raises(TypeError):
        QualityRejection(QualityRejectionReason.LOW_SCORE, None)
    with pytest.raises(ValueError):
        QualityRejection(
            QualityRejectionReason.NO_VALID_SIGNAL,
            LowScoreDetails(40, 70),
        )


def test_quality_rejection_from_mapping_only_accepts_valid_whitelisted_values():
    """验证历史私有 JSON 可投影合法字段并拒绝损坏结构。"""
    rejection = quality_rejection_from_mapping(
        {
            "reason": "LOW_SCORE",
            "details": {"score": 40, "threshold": 70, "private": "ignored"},
            "private": "/not/public",
        }
    )

    assert rejection == QualityRejection(
        QualityRejectionReason.LOW_SCORE,
        LowScoreDetails(40, 70),
    )
    assert quality_rejection_from_mapping(
        {"reason": "NO_VALID_SIGNAL", "details": None}
    ) == QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None)
    assert (
        quality_rejection_from_mapping({"reason": "NO_VALID_SIGNAL", "details": {"score": 0}})
        is None
    )
    assert (
        quality_rejection_from_mapping(
            {"reason": "LOW_SCORE", "details": {"score": True, "threshold": 70}}
        )
        is None
    )


def test_both_rejections_stop_the_real_algorithm_entry_in_an_independent_process(
    tmp_path,
):
    """用独立进程验证两类拒绝传播且预测步骤不会继续执行。"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = """
from pathlib import Path
from health_inference import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
    VersionResources,
)

for reason in QualityRejectionReason:
    rejection = QualityRejection(
        reason,
        LowScoreDetails(40, 70) if reason is QualityRejectionReason.LOW_SCORE else None,
    )
    def executor(_):
        raise QualityRejectedError(rejection)

    algorithm = AlgorithmDefinition(
        key="isolated-test",
        is_example=False,
        required_inputs=(),
        required_artifacts=(),
        input_extensions={},
        artifact_extensions={},
        required_profile_fields=(),
        parameter_validator=lambda _: None,
        result_validator=lambda _: None,
        resource_factory=lambda _: executor,
        metric_contracts=(),
    )
    runtime = algorithm.create_runtime(VersionResources({}, {}))
    try:
        runtime.run(InferenceInput({}, {}, {}, {}, Path(".")))
    except QualityRejectedError as error:
        assert error.code == "INFERENCE.QUALITY_REJECTED"
        assert error.rejection == rejection
    else:
        raise AssertionError("质量拒绝未传播")
print("quality rejection process ok")
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "quality rejection process ok"
