"""复现旧版 pyPPG 生物标志物统计及 918 列特征顺序。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    FEATURE_COUNT,
    FEATURE_GROUP_ORDER,
    FEATURE_GROUP_SIZES,
    FEATURE_SCHEMA_SHA256,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    PreprocessedDiabetes,
)
from health_inference.exceptions import InferenceError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

_STATISTIC_ORDER = (
    "mean",
    "median",
    "std",
    "percentile_25",
    "percentile_75",
    "iqr",
    "skew",
    "kurtosis",
    "mad",
)
_CORRECTED_FIDUCIALS = ("on", "dn", "dp", "v", "w", "f")


@dataclass(frozen=True, slots=True)
class DiabetesFeatureMatrix:
    """保存本次固定顺序的特征矩阵及可核对列名。"""

    values: NDArray[np.float32]  # ONNX 输入矩阵，形状固定为 (1, 918)。
    names: tuple[str, ...]  # 按旧统计行优先规则排列的列名。


def build_diabetes_features(prepared: PreprocessedDiabetes) -> DiabetesFeatureMatrix:
    """用整段 PPG 计算旧版四组统计并拒绝特征顺序漂移。"""
    import numpy as np
    import pandas as pd
    from dotmap import DotMap

    from health_inference.libs.pyPPG import PPG, Biomarkers, Fiducials
    from health_inference.libs.pyPPG import biomarkers as biomarker_module
    from health_inference.libs.pyPPG import fiducials as fiducial_module

    signal = DotMap(
        start_sig=0,
        end_sig=len(prepared.ppg_signal),
        v=prepared.ppg_signal,
        fs=prepared.sampling_rate_hz,
        name="signal_0",
        ppg=prepared.ppg_signal,
        vpg=np.gradient(prepared.ppg_signal),
    )
    signal.apg = np.gradient(signal.vpg)
    signal.jpg = np.gradient(signal.apg)
    correction = pd.DataFrame()
    correction.loc[0, list(_CORRECTED_FIDUCIALS)] = True
    signal.correction = correction

    ppg = PPG(signal)
    fiducial_collection = fiducial_module.FpCollection(s=ppg)
    fiducials = Fiducials(fp=fiducial_collection.get_fiducials(s=ppg))
    biomarker_collection = biomarker_module.BmCollection(s=ppg, fp=fiducials)
    biomarkers = Biomarkers(*biomarker_collection.get_biomarkers())
    statistics = biomarkers.bm_stats
    if tuple(statistics.keys()) != ("ppg_sig", "sig_ratios", "ppg_derivs", "derivs_ratios"):
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 特征结构无效")

    feature_names: list[str] = []
    feature_values: list[object] = []
    group_sizes: list[int] = []
    for group_name in FEATURE_GROUP_ORDER:
        frame = statistics[group_name]
        if tuple(frame.index.tolist()) != _STATISTIC_ORDER:
            raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 特征统计顺序无效")
        names = [
            f"{feature}_{statistic}"
            for statistic in frame.index.tolist()
            for feature in frame.columns.tolist()
        ]
        flattened = frame.to_numpy().reshape(-1)
        feature_names.extend(names)
        feature_values.extend(flattened.tolist())
        group_sizes.append(len(names))

    schema_digest = hashlib.sha256("\n".join(feature_names).encode("utf-8")).hexdigest()
    if (
        tuple(group_sizes) != FEATURE_GROUP_SIZES
        or len(feature_names) != FEATURE_COUNT
        or schema_digest != FEATURE_SCHEMA_SHA256
    ):
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 特征版本不兼容")

    try:
        matrix = np.asarray(feature_values).astype(np.float32).reshape(1, FEATURE_COUNT)
    except (TypeError, ValueError, OverflowError) as error:
        raise InferenceError("INFERENCE.EXECUTION_ERROR", "PPG 特征无法转换") from error
    return DiabetesFeatureMatrix(values=matrix, names=tuple(feature_names))
