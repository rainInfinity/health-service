"""验证糖尿病 v1 的 918 维统计顺序及 pyPPG 结构。"""

import hashlib
from pathlib import Path

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.constants import (
    FEATURE_COUNT,
    FEATURE_GROUP_SIZES,
    FEATURE_SCHEMA_SHA256,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.features import (
    build_diabetes_features,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.load_input import (
    load_diabetes_ppg,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1.preprocessing import (
    preprocess_ppg,
)


def test_builds_the_legacy_918_feature_order(
    write_diabetes_ppg_csv,
    tmp_path: Path,
):
    """真实 pyPPG 链保持模型要求的 918 列及旧版四组顺序。"""
    path = write_diabetes_ppg_csv(tmp_path / "features.csv", sample_count=900)
    prepared = preprocess_ppg(load_diabetes_ppg(path))

    result = build_diabetes_features(prepared)

    assert result.values.shape == (1, FEATURE_COUNT)
    assert result.values.dtype == np.float32
    assert len(result.names) == FEATURE_COUNT
    assert result.names[0] == "Tu/Tpi_mean"
    assert result.names[-1] == "Asp/(Tpi-Tsp)_mad"
    assert FEATURE_GROUP_SIZES == (270, 135, 315, 198)
    assert hashlib.sha256("\n".join(result.names).encode("utf-8")).hexdigest() == (
        FEATURE_SCHEMA_SHA256
    )


def test_pyppg_ignores_partial_nan_but_does_not_fill_all_missing_features():
    """统计仅忽略单列中的 NaN；整列缺失仍失败，不补默认值。"""
    import pandas as pd
    from health_inference.libs.pyPPG.ppg_bm.statistics import get_statistics

    peaks = pd.Series([], dtype=np.float64)
    onsets = pd.Series([], dtype=np.float64)
    partial = get_statistics(
        peaks,
        onsets,
        {"group": pd.DataFrame({"feature": [1.0, np.nan, 3.0]})},
    )["group"]
    assert partial.loc["mean", "feature"] == 2.0
    assert partial.loc["percentile_25", "feature"] == 1.5

    with pytest.raises(ValueError, match="Length mismatch"):
        get_statistics(
            peaks,
            onsets,
            {"group": pd.DataFrame({"feature": [np.nan, np.nan]})},
        )
