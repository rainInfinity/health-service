"""加载固定版本的 SBP、DBP 与归一化制品。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from health_inference.exceptions import InferenceError

_REQUIRED_ARTIFACTS = frozenset({"sbp", "dbp", "normalize"})
_FEATURE_NAMES = ("X1", "age", "pr", "bmi", "height")


@dataclass(frozen=True, slots=True)
class BPResources:
    """保存一个发布版本固定的双模型和归一化参数。"""

    sbp_model: object  # 仅对应当前版本 sbp 制品的模型对象。
    dbp_model: object  # 仅对应当前版本 dbp 制品的模型对象。
    normalization: Mapping[str, Mapping[str, float]]  # 五项特征的 miu 与 segma。


def load_bp_resources(artifact_paths: Mapping[str, Path]) -> BPResources:
    """读取精确绑定的三件套并验证归一化与模型六维预测兼容性。"""
    import joblib
    import numpy as np
    import pandas as pd

    if set(artifact_paths) != _REQUIRED_ARTIFACTS:
        raise _invalid_artifact()
    if any(
        not isinstance(path, Path)
        or not path.is_file()
        or path.suffix.lower() != (".csv" if name == "normalize" else ".joblib")
        for name, path in artifact_paths.items()
    ):
        raise _invalid_artifact()

    normalization = _load_normalization(artifact_paths["normalize"], pd)
    try:
        sbp_model = joblib.load(artifact_paths["sbp"])
        dbp_model = joblib.load(artifact_paths["dbp"])
    except Exception as error:
        raise _invalid_artifact() from error

    probe = np.asarray([[0.0, 0.0, 0.0, 0.0, 0.0, 1.0]], dtype=np.float64)
    _validate_model(sbp_model, probe, np)
    _validate_model(dbp_model, probe, np)
    return BPResources(
        sbp_model=sbp_model,
        dbp_model=dbp_model,
        normalization=normalization,
    )


def _load_normalization(path: Path, pandas) -> Mapping[str, Mapping[str, float]]:
    """校验 CSV 的五个特征均值与非零标准差。"""
    try:
        table = pandas.read_csv(path, index_col=0)
    except Exception as error:
        raise _invalid_artifact() from error
    if (
        table.index.has_duplicates
        or table.columns.has_duplicates
        or not {"miu", "segma"}.issubset(table.index)
        or not set(_FEATURE_NAMES).issubset(table.columns)
    ):
        raise _invalid_artifact()

    normalization: dict[str, Mapping[str, float]] = {}
    for name in _FEATURE_NAMES:
        try:
            mean = float(table.loc["miu", name])
            standard_deviation = float(table.loc["segma", name])
        except (TypeError, ValueError, OverflowError) as error:
            raise _invalid_artifact() from error
        if (
            not math.isfinite(mean)
            or not math.isfinite(standard_deviation)
            or standard_deviation == 0
        ):
            raise _invalid_artifact()
        normalization[name] = {"miu": mean, "segma": standard_deviation}
    return normalization


def _validate_model(model: object, probe, numpy) -> None:
    """以合法 sex=1 六维探针确认固定模型支持单样本预测。"""
    predictor = getattr(model, "predict", None)
    if not callable(predictor):
        raise _invalid_artifact()
    try:
        prediction = numpy.asarray(predictor(probe))
    except Exception as error:
        raise _invalid_artifact() from error
    if (
        prediction.shape != (1,)
        or not numpy.issubdtype(prediction.dtype, numpy.number)
        or not numpy.isfinite(prediction[0])
    ):
        raise _invalid_artifact()


def _invalid_artifact() -> InferenceError:
    """创建不泄露制品路径或反序列化异常内容的固定错误。"""
    return InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品格式无效")
