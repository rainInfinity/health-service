"""验证血压 v1 固定版本模型与归一化制品契约。"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.resources import (
    load_bp_resources,
)
from health_inference.exceptions import InferenceError

_FEATURE_NAMES = ("X1", "age", "pr", "bmi", "height")


class _LinearModel:
    """提供可序列化的确定性六维线性制品。"""

    def __init__(self, weights: tuple[float, ...], intercept: float):
        """保存单样本预测使用的权重和截距。"""
        self.weights = np.asarray(weights, dtype=np.float64)
        self.intercept = float(intercept)

    def predict(self, features):
        """按固定权重计算每个六维样本的预测值。"""
        return np.asarray(features, dtype=np.float64) @ self.weights + self.intercept


class _FixedOutputModel:
    """为模型输出契约测试保存一个指定预测结果。"""

    def __init__(self, output):
        """保存待返回的测试数组。"""
        self.output = output

    def predict(self, features):
        """返回指定数组以覆盖模型输出边界。"""
        return np.asarray(self.output)


def _normalization_table() -> pd.DataFrame:
    """创建五维特征的有限均值与非零标准差表。"""
    return pd.DataFrame(
        {name: [float(index + 1), float(index + 2)] for index, name in enumerate(_FEATURE_NAMES)},
        index=("miu", "segma"),
    )


def _write_bundle(
    directory: Path,
    *,
    sbp_model: object | None = None,
    dbp_model: object | None = None,
    normalization: pd.DataFrame | None = None,
) -> dict[str, Path]:
    """在独立版本目录写入合成双模型和归一化制品。"""
    directory.mkdir(parents=True, exist_ok=True)
    sbp_path = directory / "sbp-registered.joblib"
    dbp_path = directory / "dbp-registered.joblib"
    normalization_path = directory / "normalize-registered.csv"
    joblib.dump(
        sbp_model if sbp_model is not None else _LinearModel((0.0,) * 6, 120.0),
        sbp_path,
    )
    joblib.dump(
        dbp_model if dbp_model is not None else _LinearModel((0.0,) * 6, 80.0),
        dbp_path,
    )
    table = _normalization_table() if normalization is None else normalization
    table.to_csv(normalization_path)
    return {
        "sbp": sbp_path,
        "dbp": dbp_path,
        "normalize": normalization_path,
    }


def test_load_bp_resources_keeps_each_registered_version_bundle_isolated(
    tmp_path: Path,
):
    """验证两个版本各用其登记模型与归一化，不受 latest 文件影响。"""
    first_table = _normalization_table()
    second_table = _normalization_table()
    second_table.loc["miu", "X1"] = 12.5
    first_paths = _write_bundle(
        tmp_path / "bp" / "1.0",
        sbp_model=_LinearModel((1.0, 0.0, 0.0, 0.0, 0.0, 0.0), 100.0),
        dbp_model=_LinearModel((0.0,) * 6, 70.0),
        normalization=first_table,
    )
    second_paths = _write_bundle(
        tmp_path / "bp" / "2.0",
        sbp_model=_LinearModel((1.0, 0.0, 0.0, 0.0, 0.0, 0.0), 200.0),
        dbp_model=_LinearModel((0.0,) * 6, 90.0),
        normalization=second_table,
    )
    latest_paths = _write_bundle(
        tmp_path / "bp" / "latest",
        sbp_model=_LinearModel((0.0,) * 6, 999.0),
        dbp_model=_LinearModel((0.0,) * 6, 999.0),
    )

    first = load_bp_resources(first_paths)
    second = load_bp_resources(second_paths)
    features = np.asarray([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]])

    assert first_paths["sbp"] != latest_paths["sbp"]
    np.testing.assert_array_equal(first.sbp_model.predict(features), [101.0])
    np.testing.assert_array_equal(first.dbp_model.predict(features), [70.0])
    np.testing.assert_array_equal(second.sbp_model.predict(features), [201.0])
    np.testing.assert_array_equal(second.dbp_model.predict(features), [90.0])
    assert first.normalization["X1"]["miu"] != second.normalization["X1"]["miu"]


@pytest.mark.parametrize(
    "mutation",
    ("missing_key", "extra_latest_key", "missing_file", "wrong_suffix"),
)
def test_load_bp_resources_rejects_unbound_or_missing_artifact_paths(
    tmp_path: Path,
    mutation: str,
):
    """验证制品清单必须完整、精确且指向真实约定后缀文件。"""
    paths = _write_bundle(tmp_path / "version")
    if mutation == "missing_key":
        paths.pop("dbp")
    elif mutation == "extra_latest_key":
        paths["latest"] = paths["sbp"]
    elif mutation == "missing_file":
        paths["dbp"] = tmp_path / "version" / "missing.joblib"
    else:
        wrong_path = tmp_path / "version" / "sbp.bin"
        wrong_path.write_bytes(b"not an artifact")
        paths["sbp"] = wrong_path

    with pytest.raises(InferenceError) as error:
        load_bp_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"
    assert str(error.value) == "模型制品格式无效"


@pytest.mark.parametrize(
    "invalid_case",
    (
        "missing_feature",
        "missing_row",
        "zero_standard_deviation",
        "nonfinite_mean",
        "nonfinite_standard_deviation",
        "duplicate_row",
    ),
)
def test_load_bp_resources_rejects_invalid_normalization(
    tmp_path: Path,
    invalid_case: str,
):
    """验证归一化必须含唯一字段和有限、非零标准差。"""
    table = _normalization_table()
    if invalid_case == "missing_feature":
        table = table.drop(columns=["height"])
    elif invalid_case == "missing_row":
        table = table.drop(index="segma")
    elif invalid_case == "zero_standard_deviation":
        table.loc["segma", "X1"] = 0.0
    elif invalid_case == "nonfinite_mean":
        table.loc["miu", "age"] = np.inf
    elif invalid_case == "nonfinite_standard_deviation":
        table.loc["segma", "bmi"] = np.nan
    elif invalid_case == "duplicate_row":
        table = pd.concat((table, table.iloc[[0]]))

    paths = _write_bundle(tmp_path / "version", normalization=table)

    with pytest.raises(InferenceError) as error:
        load_bp_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"


@pytest.mark.parametrize(
    "sbp_model",
    (
        _LinearModel((1.0,) * 5, 0.0),
        _FixedOutputModel([[120.0]]),
        _FixedOutputModel([np.nan]),
        _FixedOutputModel(["120.0"]),
        object(),
    ),
    ids=("five_features", "two_dimensional_output", "nonfinite", "nonnumeric", "no_predict"),
)
def test_load_bp_resources_rejects_models_incompatible_with_single_six_feature_input(
    tmp_path: Path,
    sbp_model: object,
):
    """验证模型必须接受六维输入并返回单个有限数值。"""
    paths = _write_bundle(tmp_path / "version", sbp_model=sbp_model)

    with pytest.raises(InferenceError) as error:
        load_bp_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"


def test_load_bp_resources_hides_joblib_deserialization_details(tmp_path: Path):
    """验证损坏的 joblib 只产生受控制品错误，不泄露反序列化信息。"""
    paths = _write_bundle(tmp_path / "version")
    paths["sbp"].write_bytes(b"private deserialization detail")

    with pytest.raises(InferenceError) as error:
        load_bp_resources(paths)

    assert error.value.code == "INFERENCE.INVALID_ARTIFACT"
    assert str(error.value) == "模型制品格式无效"
    assert "private deserialization detail" not in str(error.value)
