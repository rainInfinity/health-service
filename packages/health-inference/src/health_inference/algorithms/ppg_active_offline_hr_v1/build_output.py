"""校验并构建独立心率结果，只写出处理后的 PPG 波形。"""

import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput
from health_inference.utils.ppg_output import write_processed_csv
from health_inference.utils.ppg_preprocessing import PreprocessedPPG
from health_inference.utils.ppg_quality import QUALITY_THRESHOLD, PPGQualityResult

_PPG_OUTPUT_NAME = "ppg-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedPPG,
    quality: PPGQualityResult | None,
    prediction: float,
) -> InferenceOutput:
    """写文件前校验全部结果，保留有限心率原值，不限幅或舍入。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")

    ppg_path = inference_input.output_dir / _PPG_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "hr": prediction,
            "quality": {"score": quality.score, "threshold": quality.threshold},
        },
        metrics={"hr": prediction},
        output_files={"processed_ppg": ppg_path},
    )
    valid_output(output)
    write_processed_csv(
        ppg_path,
        signal_name="ppg",
        timestamps_ms=loaded.timestamps_ms,
        signal=prepared.ppg_signal,
    )
    return output


def valid_output(output: InferenceOutput) -> None:
    """约束心率、固定质量门禁、指标一致性和唯一输出文件名。"""
    payload = output.payload
    metrics = output.metrics
    output_files = output.output_files
    if set(payload) != {"hr", "quality"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {"hr"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
    if (
        not _is_finite_float(payload["hr"])
        or not _is_finite_float(metrics["hr"])
        or metrics["hr"] != payload["hr"]
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")

    quality = payload["quality"]
    if (
        not isinstance(quality, Mapping)
        or set(quality) != {"score", "threshold"}
        or not _is_finite_float(quality["score"])
        or quality["score"] < QUALITY_THRESHOLD
        or type(quality["threshold"]) is not int
        or quality["threshold"] != QUALITY_THRESHOLD
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")
    if (
        set(output_files) != {"processed_ppg"}
        or not isinstance(output_files["processed_ppg"], Path)
        or output_files["processed_ppg"].name != _PPG_OUTPUT_NAME
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")


def _is_finite_float(value: object) -> bool:
    """仅接受有限原生浮点数，不接受整数、布尔值或替代值。"""
    return type(value) is float and math.isfinite(value)
