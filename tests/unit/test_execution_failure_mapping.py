"""验证受控输入失败文案的白名单映射与安全降级。"""

from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_hr_br_v1.build_output import valid_output
from health_inference.exceptions import (
    InferenceError,
    InputError,
    InputErrorReason,
)
from health_inference.types import InferenceOutput
from health_inference.utils.ppg_input import load_ppg
from health_service.application.execution import _safe_failure_fields


@pytest.mark.parametrize(
    ("reason", "expected_message"),
    [
        (InputErrorReason.CSV_FORMAT, "PPG CSV 格式无效"),
        (InputErrorReason.MISSING_COLUMN, "PPG CSV 缺少必需列"),
        (InputErrorReason.INVALID_CHANNELS, "PPG 必须完整提供一组 RGB 或 YUV 通道"),
        (InputErrorReason.INVALID_NUMERIC_VALUE, "PPG 通道必须为合法数值"),
        (InputErrorReason.NON_FINITE_VALUE, "PPG 输入数值必须为有限值"),
        (
            InputErrorReason.INVALID_TIMESTAMP,
            "PPG 时间戳必须为有限且严格递增的毫秒数值",
        ),
        (InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT, "当前算法版本仅支持单块 PPG"),
        (InputErrorReason.INSUFFICIENT_DATA, "PPG 数据不足以执行本算法所需处理"),
        (InputErrorReason.INVALID_SAMPLING_RATE, "PPG 采样率不支持本算法所需处理"),
    ],
)
def test_declared_input_reason_maps_to_fixed_public_message(
    reason: InputErrorReason,
    expected_message: str,
):
    """合法输入原因保留错误码并映射固定文案及空普通详情。"""
    error = InputError(reason)

    fields = _safe_failure_fields(error)

    assert fields == ("INFERENCE.INVALID_INPUT", expected_message, None)


@pytest.mark.parametrize("reason", [None, "CSV_FORMAT", "UNKNOWN", object()])
def test_unknown_or_corrupted_reason_uses_generic_safe_input_message(reason: object):
    """未知或被损坏的原因不通过字符串相等伪装成白名单枚举。"""
    error = InputError(InputErrorReason.CSV_FORMAT)
    error.reason = reason
    error.safe_message = "PRIVATE-PATH-SENTINEL"

    fields = _safe_failure_fields(error)

    assert fields == ("INFERENCE.INVALID_INPUT", "推理输入无效", None)
    assert "PRIVATE-PATH-SENTINEL" not in " ".join(str(value) for value in fields)


def test_generic_input_error_uses_safe_fallback():
    """通用输入失败不会透出异常正文。"""
    fields = _safe_failure_fields(
        InferenceError("INFERENCE.INVALID_INPUT", "RAW-INPUT-SENTINEL")
    )

    assert fields == ("INFERENCE.INVALID_INPUT", "推理输入无效", None)


@pytest.mark.parametrize(
    ("content", "expected_message"),
    [
        (
            ("timestampFrame,avgR,avgG,avgB,blockIndex\n0,120,128,136,1\n100,121,128,136,0\n"),
            "当前算法版本仅支持单块 PPG",
        ),
        (
            ("timestampFrame,avgR,avgG,avgB\n0,120,128,136\n0,121,128,136\n"),
            "PPG 时间戳必须为有限且严格递增的毫秒数值",
        ),
        (
            ("timestampFrame,avgR,avgG,avgB\n0,120,128,136\n"),
            "PPG 数据不足以执行本算法所需处理",
        ),
    ],
)
def test_real_parser_errors_map_to_fixed_public_messages(
    tmp_path: Path,
    content: str,
    expected_message: str,
):
    """真实解析器的常见输入拒绝映射为固定文案且不泄露路径。"""
    input_path = tmp_path / "private-input.csv"
    input_path.write_text(content, encoding="utf-8")

    with pytest.raises(InputError) as error:
        load_ppg(input_path)

    fields = _safe_failure_fields(error.value)
    assert fields == ("INFERENCE.INVALID_INPUT", expected_message, None)
    assert str(input_path) not in repr(fields)


def test_non_finite_prediction_stays_a_result_failure():
    """结果校验器拒绝非有限预测并保留结果失败分类。"""
    output = InferenceOutput(
        payload={"hr": 72.0, "br": float("inf"), "quality": {"score": 85.0, "threshold": 70}},
        metrics={"hr": 72.0, "br": float("inf")},
        output_files={
            "processed_ppg": Path("ppg-processed.csv"),
            "processed_rsp": Path("rsp-processed.csv"),
        },
    )

    with pytest.raises(InferenceError) as error:
        valid_output(output)

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert _safe_failure_fields(error.value) == (
        "RESULT.INVALID_OUTPUT",
        "推理结果格式无效",
        None,
    )
