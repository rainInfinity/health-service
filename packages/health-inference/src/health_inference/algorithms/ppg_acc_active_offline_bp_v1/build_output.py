"""校验血压双结果，并向当前执行目录写入处理后 CSV。"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.algorithms.ppg_acc_active_offline_bp_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.inference import (
    RawPrediction,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    LoadedBPInput,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.quality import (
    BPQualityResult,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput

_SBP_METRIC = "sbp"
_DBP_METRIC = "dbp"
_PPG_OUTPUT_NAME = "ppg-processed.csv"
_SCG_OUTPUT_NAME = "scg-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: LoadedBPInput,
    prepared: PreprocessedBP,
    quality: BPQualityResult | None,
    prediction: RawPrediction,
) -> InferenceOutput:
    """先验证双预测与质量，再写本次任务隔离的两份处理 CSV。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if not _is_finite_float(prediction.sbp) or not _is_finite_float(prediction.dbp):
        raise InferenceError("RESULT.INVALID_OUTPUT", "推理结果无效")
    if len(prediction.ao_signal) > len(prepared.scg_timestamps_ms):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")

    ppg_path = inference_input.output_dir / _PPG_OUTPUT_NAME
    scg_path = inference_input.output_dir / _SCG_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "sbp": prediction.sbp,
            "dbp": prediction.dbp,
            "quality": {"score": quality.score, "threshold": quality.threshold},
        },
        metrics={_SBP_METRIC: prediction.sbp, _DBP_METRIC: prediction.dbp},
        output_files={"processed_ppg": ppg_path, "processed_scg": scg_path},
    )
    valid_output(output)
    _write_ppg_csv(ppg_path, loaded.ppg.timestamps_ms, prepared.ppg_signal)
    _write_scg_csv(
        scg_path,
        prepared.scg_timestamps_ms[: len(prediction.ao_signal)],
        prediction.ao_signal,
    )
    return output


def valid_output(output: InferenceOutput) -> None:
    """校验 payload、实际质量、投影指标和值文件逻辑名。"""
    payload = output.payload
    metrics = output.metrics
    files = output.output_files
    if set(payload) != {"sbp", "dbp", "quality"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {_SBP_METRIC, _DBP_METRIC}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
    sbp = payload["sbp"]
    dbp = payload["dbp"]
    if (
        not _is_finite_float(sbp)
        or not _is_finite_float(dbp)
        or type(metrics[_SBP_METRIC]) is not float
        or type(metrics[_DBP_METRIC]) is not float
        or metrics[_SBP_METRIC] != sbp
        or metrics[_DBP_METRIC] != dbp
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
        set(files) != {"processed_ppg", "processed_scg"}
        or not isinstance(files["processed_ppg"], Path)
        or files["processed_ppg"].name != _PPG_OUTPUT_NAME
        or not isinstance(files["processed_scg"], Path)
        or files["processed_scg"].name != _SCG_OUTPUT_NAME
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")


def _write_ppg_csv(
    path: Path,
    timestamps_ms: tuple[float, ...],
    signal: tuple[float, ...],
) -> None:
    """以毫秒原始时间轴写出旧版清洗后的 PPG。"""
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file, lineterminator="\r\n")
        writer.writerow(("timestamp", "ppg"))
        writer.writerows(zip(timestamps_ms, signal, strict=True))


def _write_scg_csv(
    path: Path,
    timestamps_ms: tuple[float, ...],
    signal: tuple[float, ...],
) -> None:
    """写出截断到 AO 长度的毫秒时间轴与零 X/Y、重建 Z。"""
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file, lineterminator="\r\n")
        writer.writerow(("timestamp", "scgX", "scgY", "scgZ"))
        writer.writerows(
            (timestamp, 0, 0, value)
            for timestamp, value in zip(timestamps_ms, signal, strict=True)
        )


def _is_finite_float(value: object) -> bool:
    """只接受有限原生浮点预测，不把整数或布尔值伪装成模型结果。"""
    return type(value) is float and math.isfinite(value)
