"""验证共享失败分类不会泄漏异常私有内容或伪造质量结论。"""

from pathlib import Path

import pytest
from health_inference import (
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.build_output import valid_output
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.failures import is_safe_failure, safe_failure_fields
from health_inference.types import InferenceOutput
from health_inference.utils.ppg_input import load_ppg


@pytest.mark.parametrize("reason", list(InputErrorReason))
def test_input_reasons_discard_exception_private_data(reason):
    """受控输入原因不会将正文或附加诊断写入公开失败。"""
    error = InputError(reason)
    error.safe_message = "PRIVATE-PATH-SENTINEL"
    error.args = ("PRIVATE-BODY-SENTINEL",)
    error.failure_detail = {"raw": "PRIVATE-INPUT-SENTINEL"}
    code, message, detail = safe_failure_fields(error)
    assert code == "INFERENCE.INVALID_INPUT"
    assert detail is None
    assert "PRIVATE-" not in repr((code, message, detail))
    assert is_safe_failure(code, message, None)


@pytest.mark.parametrize("reason", [None, "CSV_FORMAT", "UNKNOWN", object()])
def test_unknown_or_corrupted_reason_uses_generic_safe_input_message(reason):
    """未知原因降级为通用输入失败，不以字符串相等冒充枚举。"""
    error = InputError(InputErrorReason.CSV_FORMAT)
    error.reason = reason
    error.safe_message = "PRIVATE-PATH-SENTINEL"
    assert safe_failure_fields(error) == safe_failure_fields(
        InferenceError("INFERENCE.INVALID_INPUT", "PRIVATE-BODY-SENTINEL")
    )
    assert "PRIVATE-" not in repr(safe_failure_fields(error))


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            "timestampFrame,avgR,avgG,avgB,blockIndex\n0,120,128,136,1\n100,121,128,136,0\n",
            InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,120,128,136\n0,121,128,136\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,120,128,136\n",
            InputErrorReason.INSUFFICIENT_DATA,
        ),
    ],
)
def test_real_parser_errors_keep_classification_without_path(tmp_path, content, reason):
    """真实解析失败保留受控原因解释，文件路径不进入持久化字段。"""
    input_path = tmp_path / "private-input.csv"
    input_path.write_text(content, encoding="utf-8")
    with pytest.raises(InputError) as error:
        load_ppg(input_path)
    assert error.value.reason is reason
    fields = safe_failure_fields(error.value)
    assert fields[0] == "INFERENCE.INVALID_INPUT"
    assert fields[2] is None
    assert str(input_path) not in repr(fields)


def test_non_finite_prediction_stays_a_result_failure():
    """HR/BR 单项非法时整份结果被拒绝，不降级成输入或质量失败。"""
    output = InferenceOutput(
        payload={"hr": 72.0, "br": float("inf"), "quality": {"score": 85.0, "threshold": 70}},
        metrics={"hr": 72.0, "br": float("inf")},
        output_files={"processed_ppg": Path("ppg-processed.csv"), "processed_rsp": Path("rsp-processed.csv")},
    )
    with pytest.raises(InferenceError) as error:
        valid_output(output)
    code, _, detail = safe_failure_fields(error.value)
    assert code == "RESULT.INVALID_OUTPUT"
    assert detail is None


@pytest.mark.parametrize("reason", list(QualityRejectionReason))
def test_quality_rejection_only_persists_whitelisted_fields(reason):
    """合法拒绝保留真实数值，额外异常属性和正文不进入持久化明细。"""
    details = LowScoreDetails(69.999999999, 70) if reason is QualityRejectionReason.LOW_SCORE else None
    error = QualityRejectedError(QualityRejection(reason, details))
    error.args = ("PRIVATE-BODY-SENTINEL",)
    error.safe_message = "PRIVATE-MESSAGE-SENTINEL"
    error.failure_detail = {"path": "PRIVATE-PATH-SENTINEL"}
    code, message, detail = safe_failure_fields(error)
    expected = {"reason": reason.value, "details": None}
    if details is not None:
        expected["details"] = {"score": details.score, "threshold": details.threshold}
    assert detail == {"quality": expected}
    assert code == "INFERENCE.QUALITY_REJECTED"
    assert "PRIVATE-" not in repr((code, message, detail))
    assert is_safe_failure(code, message, expected)
    assert not is_safe_failure(code, message, {**expected, "path": "PRIVATE-PATH"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "60", None, 70])
def test_corrupted_quality_score_cannot_become_a_public_conclusion(value):
    """绕过构造器篡改的评分不能产生质量拒绝或医学解释。"""
    error = QualityRejectedError(QualityRejection(QualityRejectionReason.LOW_SCORE, LowScoreDetails(60, 70)))
    object.__setattr__(error.rejection.details, "score", value)
    code, message, detail = safe_failure_fields(error)
    assert code == "INFERENCE.EXECUTION_ERROR"
    assert detail is None
    assert not is_safe_failure("INFERENCE.QUALITY_REJECTED", message, {"reason": "LOW_SCORE", "details": {"score": value, "threshold": 70}})


@pytest.mark.parametrize("corruption", ["code", "reason", "details", "missing"])
def test_forged_quality_error_falls_back_without_leaking(corruption):
    """伪造质量码、未知原因及损坏对象均安全降级。"""
    if corruption == "code":
        error = InferenceError("INFERENCE.QUALITY_REJECTED", "PRIVATE-BODY-SENTINEL")
    else:
        error = QualityRejectedError(QualityRejection(QualityRejectionReason.LOW_SCORE, LowScoreDetails(60, 70)))
        if corruption == "reason":
            object.__setattr__(error.rejection, "reason", "LOW_SCORE")
        elif corruption == "details":
            object.__setattr__(error.rejection, "details", None)
        else:
            del error.rejection
    code, message, detail = safe_failure_fields(error)
    assert code == "INFERENCE.EXECUTION_ERROR"
    assert detail is None
    assert "PRIVATE-" not in message


def test_final_failure_validation_rejects_forged_summary_and_details():
    """最终汇总不能替换安全摘要或将普通错误包装为质量拒绝。"""
    code, message, _ = safe_failure_fields(InferenceError("RESULT.INVALID_OUTPUT", "private"))
    assert not is_safe_failure(code, "PRIVATE-BODY", None)
    assert not is_safe_failure(code, message, {"reason": "NO_VALID_SIGNAL", "details": None})
    assert not is_safe_failure("INFERENCE.QUALITY_REJECTED", message, None)
