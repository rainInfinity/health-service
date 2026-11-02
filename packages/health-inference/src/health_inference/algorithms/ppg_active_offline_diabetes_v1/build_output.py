"""校验糖尿病风险结果并写入本次执行的处理后 PPG。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    QUALITY_THRESHOLD,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.postprocessing import (
    DiabetesPrediction,
    classify_probability,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.quality import (
    DiabetesQualityResult,
)
from health_inference.exceptions import InferenceError
from health_inference.types import InferenceInput, InferenceOutput
from health_inference.utils.ppg_input import PPGInput

_METRIC_CODE = "diabetes_probability"
_PPG_OUTPUT_NAME = "ppg-processed.csv"


def build_output(
    inference_input: InferenceInput,
    loaded: PPGInput,
    prepared: PreprocessedDiabetes,
    quality: DiabetesQualityResult | None,
    prediction: DiabetesPrediction,
) -> InferenceOutput:
    """验证阶段结果后，生成概率投影和本次任务的 PPG 文件。"""
    if quality is None:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法质量信息无效")
    if (
        prepared.timestamps_ms != loaded.timestamps_ms
        or len(prepared.ppg_signal) != len(loaded.timestamps_ms)
        or any(not math.isfinite(float(value)) for value in prepared.ppg_signal)
    ):
        raise InferenceError("RESULT.INVALID_OUTPUT", "处理后 PPG 波形无效")

    ppg_path = inference_input.output_dir / _PPG_OUTPUT_NAME
    output = InferenceOutput(
        payload={
            "prob": prediction.probability,
            "risk_level": prediction.risk_level,
            "quality": {"score": quality.score, "threshold": quality.threshold},
        },
        metrics={_METRIC_CODE: prediction.probability},
        output_files={"processed_ppg": ppg_path},
    )
    valid_output(output)
    _write_processed_csv(ppg_path, loaded.timestamps_ms, prepared.ppg_signal)
    return output


def valid_output(output: InferenceOutput) -> None:
    """校验概率、分类、质量信息、指标一致性和文件逻辑名。"""
    payload = output.payload
    metrics = output.metrics
    output_files = output.output_files
    if set(payload) != {"prob", "risk_level", "quality"}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
    if set(metrics) != {_METRIC_CODE}:
        raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")

    probability = payload["prob"]
    risk_level = payload["risk_level"]
    quality = payload["quality"]
    if (
        not _is_finite_float(probability)
        or not 0.0 <= probability <= 1.0
        or type(risk_level) is not int
        or risk_level not in (0, 1, 2)
        or risk_level != classify_probability(probability)
        or type(metrics[_METRIC_CODE]) is not float
        or metrics[_METRIC_CODE] != probability
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
    """写入原始毫秒时间轴与逐样本对应的清洗 PPG。"""
    import pandas as pd

    pd.DataFrame({"timestampFrame": timestamps_ms, "ppg": signal}).to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\r\n",
    )


def _is_finite_float(value: object) -> bool:
    """只接受有限原生浮点数，排除布尔值和整型。"""
    return type(value) is float and math.isfinite(value)
