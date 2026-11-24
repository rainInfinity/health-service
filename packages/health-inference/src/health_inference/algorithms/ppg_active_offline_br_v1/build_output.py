"""构建、校验并写入独立 PPG BR v1 的最终结果。"""

import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput
from health_inference.utils.ppg_output import write_processed_csv
from health_inference.utils.ppg_preprocessing import PreprocessedPPG
from health_inference.utils.ppg_quality import QUALITY_THRESHOLD, PPGQualityResult

_MIN_BREATHING_RATE_BPM = 8.0
_MAX_BREATHING_RATE_BPM = 20.0
_RSP_OUTPUT_NAME = "rsp-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedPPG,
    quality: PPGQualityResult | None,
    prediction: float,
) -> InferenceOutput:
    """拒绝非法原始估计，将有限 BR 限幅后校验结果，再写入呼吸波形。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if not _is_finite_float(prediction):
        raise InferenceError("RESULT.INVALID_OUTPUT", "推理结果无效")

    br = max(_MIN_BREATHING_RATE_BPM, min(prediction, _MAX_BREATHING_RATE_BPM))
    rsp_path = inference_input.output_dir / _RSP_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "br": br,
            "quality": {"score": quality.score, "threshold": quality.threshold},
        },
        metrics={"br": br},
        output_files={"processed_rsp": rsp_path},
    )
    valid_output(output)
    write_processed_csv(
        rsp_path,
        signal_name="rsp",
        timestamps_ms=loaded.timestamps_ms,
        signal=prepared.rsp_signal,
    )
    return output


def valid_output(output: InferenceOutput) -> None:
    """校验独立 BR、质量门禁、指标一致性及唯一固定输出文件名。"""
    payload = output.payload
    metrics = output.metrics
    output_files = output.output_files
    if set(payload) != {"br", "quality"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {"br"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")

    br = payload["br"]
    quality = payload["quality"]
    if (
        not _is_finite_float(br)
        or type(metrics["br"]) is not float
        or metrics["br"] != br
        or not _MIN_BREATHING_RATE_BPM <= br <= _MAX_BREATHING_RATE_BPM
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if (
        not isinstance(quality, Mapping)
        or set(quality) != {"score", "threshold"}
        or not _is_finite_float(quality["score"])
        or quality["score"] < QUALITY_THRESHOLD
        or type(quality["threshold"]) is not int
        or quality["threshold"] != QUALITY_THRESHOLD
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")
    if set(output_files) != {"processed_rsp"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
    if (
        not isinstance(output_files["processed_rsp"], Path)
        or output_files["processed_rsp"].name != _RSP_OUTPUT_NAME
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")


def _is_finite_float(value: object) -> bool:
    """只接受有限原生浮点数，不将整数或布尔值当作生理估计。"""
    return type(value) is float and math.isfinite(value)
