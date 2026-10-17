"""实现只返回实际 PPG 输入诊断的非临床示例算法。"""

import csv
import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.exceptions import InferenceError
from health_inference.types import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
)

_RGB_COLUMNS = ("avgR", "avgG", "avgB")
_YUV_COLUMNS = ("avgY", "avgU", "avgV")
_METRIC_SAMPLE_COUNT = "example.ppg.sample_count"
_METRIC_DURATION = "example.ppg.duration_seconds"
_METRIC_SAMPLING_RATE = "example.ppg.sampling_rate_hz"


def _invalid_input() -> InferenceError:
    """返回不包含文件路径或原始数据的输入错误。"""
    return InferenceError("INFERENCE.INVALID_INPUT", "PPG 输入格式无效")


def _finite_number(value: str | None) -> float:
    """解析有限数值，拒绝缺失、非法及非有限输入。"""
    if value is None:
        raise _invalid_input()
    try:
        number = float(value)
    except ValueError as error:
        raise _invalid_input() from error
    if not math.isfinite(number):
        raise _invalid_input()
    return number


def _required_channels(fieldnames: list[str] | None) -> tuple[str, ...]:
    """确认 CSV 含时间戳和且仅一组完整 RGB/YUV 通道。"""
    if fieldnames is None or len(fieldnames) != len(set(fieldnames)):
        raise _invalid_input()
    columns = set(fieldnames)
    if "timestampFrame" not in columns:
        raise _invalid_input()
    has_rgb = set(_RGB_COLUMNS).issubset(columns)
    has_yuv = set(_YUV_COLUMNS).issubset(columns)
    if has_rgb == has_yuv:
        raise _invalid_input()
    return _RGB_COLUMNS if has_rgb else _YUV_COLUMNS


def _validate_parameters(params: Mapping[str, object]) -> None:
    """示例不接受可改变身份或计算解释的客户端参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "示例算法不接受参数")


def _validate_output(output: InferenceOutput) -> None:
    """校验示例完整结果和诊断指标与输入契约一致。"""
    expected_fields = {"example", "sample_count", "duration_seconds", "sampling_rate_hz"}
    payload = output.payload
    metrics = output.metrics
    if set(payload) != expected_fields or payload.get("example") is not True:
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")
    if output.output_files or set(metrics) != {
        _METRIC_SAMPLE_COUNT,
        _METRIC_DURATION,
        _METRIC_SAMPLING_RATE,
    }:
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")

    sample_count = payload.get("sample_count")
    duration = payload.get("duration_seconds")
    sampling_rate = payload.get("sampling_rate_hz")
    if type(sample_count) is not int or sample_count < 2:
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")
    if not _is_finite_number(duration) or duration <= 0:
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")
    if not _is_finite_number(sampling_rate) or sampling_rate <= 0:
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")
    if (
        type(metrics[_METRIC_SAMPLE_COUNT]) is not int
        or metrics[_METRIC_SAMPLE_COUNT] != sample_count
        or not _is_finite_number(metrics[_METRIC_DURATION])
        or metrics[_METRIC_DURATION] != duration
        or not _is_finite_number(metrics[_METRIC_SAMPLING_RATE])
        or metrics[_METRIC_SAMPLING_RATE] != sampling_rate
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "示例结果格式无效")


def _is_finite_number(value: object) -> bool:
    """判断结果数值有限且不把布尔值当作数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _run_example(inference_input: InferenceInput) -> InferenceOutput:
    """流式读取 PPG CSV 并计算样本数、时长和平均采样率。"""
    ppg_path = inference_input.files["ppg"]
    sample_count = 0
    first_timestamp: float | None = None
    previous_timestamp: float | None = None
    last_timestamp: float | None = None

    try:
        with Path(ppg_path).open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            channels = _required_channels(reader.fieldnames)
            for row in reader:
                timestamp = _finite_number(row.get("timestampFrame"))
                for channel in channels:
                    _finite_number(row.get(channel))
                if previous_timestamp is not None and timestamp <= previous_timestamp:
                    raise _invalid_input()
                if first_timestamp is None:
                    first_timestamp = timestamp
                previous_timestamp = timestamp
                last_timestamp = timestamp
                sample_count += 1
    except InferenceError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise _invalid_input() from error

    if sample_count < 2 or first_timestamp is None or last_timestamp is None:
        raise _invalid_input()
    duration_seconds = (last_timestamp - first_timestamp) / 1000
    if not _is_finite_number(duration_seconds) or duration_seconds <= 0:
        raise _invalid_input()
    sampling_rate_hz = (sample_count - 1) / duration_seconds
    if not _is_finite_number(sampling_rate_hz):
        raise _invalid_input()

    payload = {
        "example": True,
        "sample_count": sample_count,
        "duration_seconds": duration_seconds,
        "sampling_rate_hz": sampling_rate_hz,
    }
    metrics = {
        _METRIC_SAMPLE_COUNT: sample_count,
        _METRIC_DURATION: duration_seconds,
        _METRIC_SAMPLING_RATE: sampling_rate_hz,
    }
    return InferenceOutput(payload=payload, metrics=metrics, output_files={})


HR_BR_EXAMPLE = AlgorithmDefinition(
    key="hr_br.example",
    is_example=True,
    required_inputs=("ppg",),
    required_artifacts=(),
    required_profile_fields=(),
    parameter_validator=_validate_parameters,
    result_validator=_validate_output,
    runner=_run_example,
    metric_contracts=(
        MetricContract(_METRIC_SAMPLE_COUNT, "number", "samples"),
        MetricContract(_METRIC_DURATION, "number", "seconds"),
        MetricContract(_METRIC_SAMPLING_RATE, "number", "hertz"),
    ),
)
