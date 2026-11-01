"""构建、校验并写入血氧 v1 的完整结果与处理后 PPG。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path

from health_inference.algorithms.ppg_active_offline_spo2_v1.constants import (
    QUALITY_THRESHOLD,
    WINDOW_SIZE,
    WINDOW_STEP,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.inference import (
    Spo2InferencePrediction,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    PreprocessedSpo2,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.quality import (
    Spo2QualityResult,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput

_SPO2_METRIC = "spo2"
_PPG_OUTPUT_NAME = "ppg-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedSpo2,
    quality: Spo2QualityResult | None,
    prediction: Spo2InferencePrediction,
) -> InferenceOutput:
    """校验阶段数据后，生成单值投影和本次任务的处理后 PPG 文件。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")
    if (
        len(loaded.timestamps_ms) != len(prepared.ppg_signal)
        or any(not math.isfinite(float(value)) for value in prepared.ppg_signal)
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后 PPG 波形无效")

    expected_starts = tuple(
        range(0, len(loaded.timestamps_ms) - WINDOW_SIZE + 1, WINDOW_STEP)
    )
    if prediction.window_starts != expected_starts:
        raise InferenceError("RESULT.INVALID_OUTPUT", "血氧窗口起点无效")

    ppg_path = inference_input.output_dir / _PPG_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "spo2": prediction.prediction.spo2,
            "quality": {"score": quality.score, "threshold": quality.threshold},
            "window_predictions": list(prediction.prediction.window_predictions),
            "window_starts": list(prediction.window_starts),
        },
        metrics={_SPO2_METRIC: prediction.prediction.spo2},
        output_files={"processed_ppg": ppg_path},
    )
    valid_output(output)
    _write_processed_csv(ppg_path, loaded.timestamps_ms, prepared.ppg_signal)
    return output


def valid_output(output: InferenceOutput) -> None:
    """校验血氧结果、质量、逐窗口值、指标一致性和文件逻辑名。"""
    payload = output.payload
    metrics = output.metrics
    output_files = output.output_files
    if set(payload) != {"spo2", "quality", "window_predictions", "window_starts"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {_SPO2_METRIC}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")

    spo2 = payload["spo2"]
    window_predictions = payload["window_predictions"]
    window_starts = payload["window_starts"]
    quality = payload["quality"]
    if (
        not _is_finite_float(spo2)
        or type(metrics[_SPO2_METRIC]) is not float
        or metrics[_SPO2_METRIC] != spo2
        or not isinstance(window_predictions, list)
        or not window_predictions
        or any(not _is_finite_float(value) for value in window_predictions)
        or spo2 != min(max(max(window_predictions), 90.0), 100.0)
        or not isinstance(window_starts, list)
        or len(window_starts) != len(window_predictions)
        or any(type(start) is not int or start < 0 for start in window_starts)
        or window_starts[0] != 0
        or any(
            later - earlier != WINDOW_STEP
            for earlier, later in pairwise(window_starts)
        )
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
    if set(output_files) != {"processed_ppg"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")
    if (
        not isinstance(output_files["processed_ppg"], Path)
        or output_files["processed_ppg"].name != _PPG_OUTPUT_NAME
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后文件格式无效")


def _write_processed_csv(
    path: Path,
    timestamps_ms: tuple[float, ...],
    signal: object,
) -> None:
    """写入原始毫秒时间戳与逐样本对应的处理后 PPG。"""
    import pandas as pd

    pd.DataFrame({"timestampFrame": timestamps_ms, "ppg": signal}).to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\r\n",
    )


def _is_finite_float(value: object) -> bool:
    """只接受有限原生浮点数，避免布尔值或整数混入百分数。"""
    return type(value) is float and math.isfinite(value)
