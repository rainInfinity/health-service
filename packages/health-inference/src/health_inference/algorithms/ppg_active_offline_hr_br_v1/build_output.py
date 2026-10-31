"""构建、校验并写入 PPG HR/BR v1 的最终结果。"""

import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.algorithms.ppg_active_offline_hr_br_v1.inference import RawPrediction
from health_inference.algorithms.ppg_active_offline_hr_br_v1.load_input import PPGInput
from health_inference.algorithms.ppg_active_offline_hr_br_v1.preprocessing import PreprocessedPPG
from health_inference.algorithms.ppg_active_offline_hr_br_v1.quality import PPGQualityResult
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput

_HR_METRIC = "hr"
_BR_METRIC = "br"
_QUALITY_THRESHOLD = 70
_MAX_BREATHING_RATE_BPM = 18.0
_PPG_OUTPUT_NAME = "ppg-processed.csv"
_RSP_OUTPUT_NAME = "rsp-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedPPG,
    quality: PPGQualityResult | None,
    prediction: RawPrediction,
) -> InferenceOutput:
    """先约束预测值，再构造并写出本次任务的结果。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if not _is_finite_float(prediction.hr) or not _is_finite_float(prediction.br):
        raise InferenceError("RESULT.INVALID_OUTPUT", "推理结果无效")

    br = min(prediction.br, _MAX_BREATHING_RATE_BPM)
    ppg_path = inference_input.output_dir / _PPG_OUTPUT_NAME
    rsp_path = inference_input.output_dir / _RSP_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "hr": prediction.hr,
            "br": br,
            "quality": {"score": quality.score, "threshold": quality.threshold},
        },
        metrics={_HR_METRIC: prediction.hr, _BR_METRIC: br},
        output_files={"processed_ppg": ppg_path, "processed_rsp": rsp_path},
    )
    valid_output(output)
    _write_processed_csv(
        ppg_path,
        signal_name="ppg",
        timestamps_ms=loaded.timestamps_ms,
        signal=prepared.ppg_signal,
    )
    _write_processed_csv(
        rsp_path,
        signal_name="rsp",
        timestamps_ms=loaded.timestamps_ms,
        signal=prepared.rsp_signal,
    )
    return output


def valid_output(output: InferenceOutput) -> None:
    """校验 HR/BR、质量结论、指标一致性和输出文件名。"""
    payload = output.payload
    metrics = output.metrics
    output_files = output.output_files
    if set(payload) != {"hr", "br", "quality"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {_HR_METRIC, _BR_METRIC}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")

    hr = payload["hr"]
    br = payload["br"]
    quality = payload["quality"]
    if (
        not _is_finite_float(hr)
        or not _is_finite_float(br)
        or type(metrics[_HR_METRIC]) is not float
        or type(metrics[_BR_METRIC]) is not float
        or metrics[_HR_METRIC] != hr
        or metrics[_BR_METRIC] != br
        or br > _MAX_BREATHING_RATE_BPM
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if (
        not isinstance(quality, Mapping)
        or set(quality) != {"score", "threshold"}
        or not _is_finite_float(quality["score"])
        or quality["score"] < _QUALITY_THRESHOLD
        or type(quality["threshold"]) is not int
        or quality["threshold"] != _QUALITY_THRESHOLD
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")
    if set(output_files) != {"processed_ppg", "processed_rsp"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
    if (
        not isinstance(output_files["processed_ppg"], Path)
        or output_files["processed_ppg"].name != _PPG_OUTPUT_NAME
        or not isinstance(output_files["processed_rsp"], Path)
        or output_files["processed_rsp"].name != _RSP_OUTPUT_NAME
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")


def _write_processed_csv(
    path: Path,
    *,
    signal_name: str,
    timestamps_ms: tuple[float, ...],
    signal: tuple[float, ...],
) -> None:
    """用 pandas 写入原始毫秒时间轴及处理后信号。"""
    import pandas as pd

    pd.DataFrame({"timestamp": timestamps_ms, signal_name: signal}).to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\r\n",
    )


def _is_finite_float(value: object) -> bool:
    """只接受有限原生浮点数，不把布尔值或整数当作生理估计。"""
    return type(value) is float and math.isfinite(value)
