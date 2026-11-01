"""验证血氧 v1 的九通道、窗口、生物标志物退化和特征矩阵。"""

import builtins
import math

import numpy as np
import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.features import (
    _BASIC_FEATURE_NAMES,
    build_9ch_matrix,
    dicts_to_matrix,
    extract_ppg_features,
    extract_window_features,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import (
    preprocess_ppg,
)
from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.utils.ppg_input import PPGInput
from scipy.ndimage import median_filter


def _input_for_format(channel_format: str) -> PPGInput:
    """生成采样率 40 Hz 的 RGB 或可转换 YUV 单块输入。"""
    timestamps_ms = tuple(index * 25.0 for index in range(600))
    channels = []
    for index in range(600):
        pulse = 8.0 * math.sin(2.0 * math.pi * 1.2 * index / 40.0)
        if channel_format == "rgb":
            channels.append((130.0 + pulse, 145.0 + pulse / 2.0, 150.0 - pulse / 3.0))
        else:
            channels.append((130.0 + pulse, 128.0, 128.0))
    return PPGInput(timestamps_ms, tuple(channels), channel_format, 40.0)


def test_rgb_and_yuv_keep_legacy_channel_order_and_window_positions():
    """验证 RGB/YUV 转换后的九通道顺序、窗口起点和旧均值特征。"""
    for channel_format in ("rgb", "yuv"):
        preprocessed = preprocess_ppg(_input_for_format(channel_format))
        matrix = build_9ch_matrix(preprocessed.feature_rgb, 40.0)
        feature_dicts, starts = extract_window_features(matrix, 40.0)

        assert matrix.shape == (600, 9)
        np.testing.assert_array_equal(matrix[:, :3], preprocessed.feature_rgb)
        np.testing.assert_allclose(
            matrix[:, 4], median_filter(preprocessed.feature_rgb[:, 0], size=81)
        )
        assert starts.tolist() == [0, 75, 150]
        assert len(feature_dicts) == 3
        for features, start in zip(feature_dicts, starts, strict=True):
            window = matrix[start : start + 450]
            assert features["r_mean"] == pytest.approx(np.mean(window[:, 0]))
            assert features["r_ac_mean"] == pytest.approx(np.mean(window[:, 3]))
            assert features["r_dc_mean"] == pytest.approx(np.mean(window[:, 4]))
            assert features["r_g_ratio"] == pytest.approx(
                (features["r_ac_mean"] / features["r_dc_mean"])
                / (features["g_ac_mean"] / features["g_dc_mean"])
            )
            assert all(math.isfinite(value) for value in features.values())


def test_pyppg_features_match_fixed_pulse_reference():
    """验证真实 pyPPG 路径保留固定合成脉搏的特征点统计。"""
    time_seconds = np.arange(900, dtype=np.float64) / 30.0
    phase = np.mod(time_seconds * 1.2, 1.0)
    pulse = np.exp(-0.5 * ((phase - 0.18) / 0.10) ** 2)
    pulse += 0.22 * np.exp(-0.5 * ((phase - 0.52) / 0.08) ** 2)
    signal = 1.0 + 0.4 * pulse

    features = extract_ppg_features(signal, 30.0, "r_")

    assert features["r_ppg_ppg_sig_Tpi_mean"] == pytest.approx(
        0.848551724138, abs=1e-12
    )
    assert features["r_ppg_ppg_sig_Tpp_mean"] == pytest.approx(
        0.833379310345, abs=1e-12
    )
    assert features["r_mean"] == pytest.approx(float(np.mean(signal)))
    assert features["r_ppg_sig_ratios_IPA_std"] == pytest.approx(
        22.78588894191891, abs=1e-8
    )
    assert all(math.isfinite(value) for value in features.values())


def test_basic_features_cover_short_and_near_constant_signals():
    """验证短信号和近常量信号仅进入有限的基础特征路径。"""
    short_signal = np.sin(np.arange(449, dtype=np.float64) / 12.0)
    constant_signal = 5.0 + (np.arange(600) % 2) * 1e-7

    short_features = extract_ppg_features(short_signal, 30.0, "r_")
    constant_features = extract_ppg_features(constant_signal, 30.0, "g_")

    assert {name[2:] for name in short_features} == set(_BASIC_FEATURE_NAMES)
    assert {name[2:] for name in constant_features} == set(_BASIC_FEATURE_NAMES)
    assert constant_features["g_skewness"] == 0.0
    assert constant_features["g_kurtosis"] == 0.0
    assert all(math.isfinite(value) for value in short_features.values())
    assert all(math.isfinite(value) for value in constant_features.values())


def test_empty_fiducials_use_basic_features(monkeypatch, capsys):
    """验证 pyPPG 返回空特征点时回退基础特征，不输出异常。"""
    import pandas as pd

    signal = 1.0 + np.sin(2.0 * math.pi * 1.2 * np.arange(600) / 30.0)
    monkeypatch.setattr(
        "health_inference.libs.pyPPG.fiducials.FpCollection.get_fiducials",
        lambda self, s: pd.DataFrame(),
    )

    features = extract_ppg_features(signal, 30.0, "r_")

    assert {name[2:] for name in features} == set(_BASIC_FEATURE_NAMES)
    assert capsys.readouterr().out == ""


def test_pyppg_dependency_failure_propagates_without_fallback_or_output(
    monkeypatch, capsys
):
    """验证 pyPPG 依赖导入失败不会被吞掉并伪装成成功。"""
    signal = 1.0 + np.sin(2.0 * math.pi * 1.2 * np.arange(600) / 30.0)
    original_import = builtins.__import__

    def fail_dotmap_import(name, globals=None, locals=None, fromlist=(), level=0):
        """只在 pyPPG 请求 DotMap 时模拟缺失依赖。"""
        if name == "dotmap":
            raise ModuleNotFoundError("pyPPG dependency unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_dotmap_import)
    with pytest.raises(ModuleNotFoundError, match="pyPPG dependency unavailable"):
        extract_ppg_features(signal, 30.0, "r_")
    assert capsys.readouterr().out == ""


def test_feature_matrix_only_zero_fills_missing_names_and_rejects_non_finite():
    """验证矩阵仅对清单缺失项填零，已有非有限值必须失败。"""
    matrix = dicts_to_matrix([{"known": 2.5}], ["missing", "known"])

    assert matrix.dtype == np.float32
    np.testing.assert_array_equal(matrix, np.asarray([[0.0, 2.5]], dtype=np.float32))
    with pytest.raises(InferenceError, match="特征结果无效"):
        dicts_to_matrix([{"known": float("nan")}], ["known"])


def test_window_extraction_rejects_incomplete_and_non_finite_inputs():
    """验证窗口提取拒绝不完整数据及非有限九通道矩阵。"""
    with pytest.raises(InputError) as too_short:
        extract_window_features(np.zeros((449, 9)), 30.0)
    assert too_short.value.reason == InputErrorReason.INSUFFICIENT_DATA

    invalid_matrix = np.zeros((450, 9))
    invalid_matrix[10, 0] = np.inf
    with pytest.raises(InputError) as non_finite:
        extract_window_features(invalid_matrix, 30.0)
    assert non_finite.value.reason == InputErrorReason.NON_FINITE_VALUE
