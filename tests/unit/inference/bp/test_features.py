"""验证血压 v1 的峰值、PTT 与 PR 旧规则。"""

import numpy as np
import pytest
from health_inference.algorithms.ppg_acc_active_offline_bp_v1 import inference
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.features import (
    build_feature_vector,
    calculate_pr,
    calculate_ptt_ms,
    find_ao_peaks,
    find_ppg_peaks,
    pair_aos_by_peaks,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    BPProfileSnapshot,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    PreprocessedBP,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.resources import (
    BPResources,
)


class _FixedModel:
    """为推理行为测试提供固定且合法的单值预测。"""

    def __init__(self, value: float):
        self.value = value

    def predict(self, features):
        """返回单条输入对应的固定预测值。"""
        return np.asarray([self.value])


def test_find_ppg_peaks_uses_negative_signal_prominence_and_minimum_distance():
    """验证在清洗 PPG 负向峰上使用 0.6 prominence 与最大心率间距。"""
    signal = np.zeros(50)
    signal[[5, 15, 25, 35]] = (-0.5, -0.7, -0.8, -0.7)
    signal[45] = 1.0

    peaks = find_ppg_peaks(signal, 20.0)

    np.testing.assert_array_equal(peaks, np.asarray([15, 25, 35]))


def test_find_ao_peaks_uses_400ms_window_and_selects_highest_local_peak(
    monkeypatch,
):
    """验证 AO 包络中心两侧 400 ms 内选最高局部峰，而非最近峰。"""
    envelope = np.zeros(400)
    envelope[[100, 250]] = 2.0
    signal = np.zeros(400)
    signal[[80, 130, 230, 280]] = (1.0, 2.0, 1.0, 2.0)

    def analytic_signal(values):
        """提供可控解析包络，隔离 Hilbert 数值细节。"""
        return envelope.astype(np.complex128)

    def identity_filter(values, **kwargs):
        """保留预设包络以验证中心峰和回溯窗口。"""
        return values

    monkeypatch.setattr("scipy.signal.hilbert", analytic_signal)
    monkeypatch.setattr("neurokit2.signal_filter", identity_filter)

    peaks = find_ao_peaks(signal, 100.0)

    np.testing.assert_array_equal(peaks, np.asarray([130, 280]))


def test_pair_aos_uses_inclusive_window_nearest_candidate_and_absolute_times():
    """验证 50–500 ms 边界、最近候选、无候选跳过与绝对时标保留。"""
    ao_times = (1_000_000, 1_000_100, 1_000_450, 1_000_500)
    ppg_peak_times = (1_000_500, 1_001_000, 1_002_500)

    paired_aos, paired_peaks = pair_aos_by_peaks(ao_times, ppg_peak_times)

    np.testing.assert_array_equal(paired_aos, np.asarray([1_000_450, 1_000_500]))
    np.testing.assert_array_equal(paired_peaks, np.asarray([1_000_500, 1_001_000]))


@pytest.mark.parametrize(
    ("paired_peaks", "paired_aos"),
    [((), ()), ((1, 2), ())],
)
def test_calculate_ptt_returns_none_when_either_pair_side_is_empty(
    paired_peaks,
    paired_aos,
):
    """验证缺少任一侧配对时间戳时返回 None。"""

    assert calculate_ptt_ms(paired_peaks, paired_aos) is None


def test_calculate_ptt_uses_strict_iqr_and_rounds_to_six_decimals():
    """验证严格 IQR 去除离群值后返回六位小数的绝对均值。"""
    paired_peaks = (50.000001, 51.000001, 52.000001, 53.000001, 1_000.0)
    paired_aos = (0.0, 0.0, 0.0, 0.0, 0.0)

    ptt_ms = calculate_ptt_ms(paired_peaks, paired_aos)

    assert ptt_ms == 51.500001


def test_calculate_ptt_preserves_nan_when_strict_iqr_removes_equal_values():
    """验证相等 PTT 被严格边界全部筛除后保留 NaN。"""
    with pytest.warns(RuntimeWarning):
        ptt_ms = calculate_ptt_ms((1_052, 2_052, 3_052), (1_000, 2_000, 3_000))

    assert np.isnan(ptt_ms)


def test_calculate_pr_uses_ceiling_duration_and_truncates_to_integer():
    """验证 PR 使用向上取整的时长及旧 int 截断规则。"""
    pr = calculate_pr(3, (1_000_000.0, 1_007_001.0))

    assert pr == 22


def test_estimate_bp_substitutes_zero_when_no_ao_can_pair_with_ppg(
    monkeypatch,
):
    """验证无配对 PTT 在正式估算入口中补零且不伪造质量拒绝。"""
    prepared = PreprocessedBP(
        ppg_timestamps_ms=(1_000_000.0, 1_001_000.0, 1_002_000.0, 1_003_000.0),
        scg_timestamps_ms=(2_000_000.0, 2_000_010.0, 2_000_020.0, 2_000_030.0),
        ppg_signal=(-1.0, 0.0, -1.0, 0.0),
        scg_signal=(0.0, 0.0, 0.0, 0.0),
        ppg_sampling_rate_hz=30.0,
        scg_sampling_rate_hz=100.0,
        profile=BPProfileSnapshot(30, 170, 70, 1),
        channel_format="rgb",
    )
    normalization = {
        name: {"miu": 0.0, "segma": 1.0}
        for name in ("X1", "age", "pr", "bmi", "height")
    }

    def reconstruct(signal, sampling_rate_hz):
        """返回固定长度 AO 信号供推理入口执行配对。"""
        return (0.0,) * 4

    def ppg_peaks(signal, sampling_rate_hz):
        """返回两个有效 PPG 索引，构造无 AO 配对场景。"""
        return np.asarray([0, 2], dtype=np.int64)

    def no_ao_peaks(signal, sampling_rate_hz):
        """返回空 AO 峰，触发旧版无配对补零规则。"""
        return np.asarray([], dtype=np.int64)

    monkeypatch.setattr(inference, "reconstruct_ao_signal", reconstruct)
    monkeypatch.setattr(inference, "find_ppg_peaks", ppg_peaks)
    monkeypatch.setattr(inference, "find_ao_peaks", no_ao_peaks)
    resources = BPResources(
        sbp_model=_FixedModel(120.0),
        dbp_model=_FixedModel(80.0),
        normalization=normalization,
    )

    prediction = inference.estimate_bp(prepared, resources)

    assert prediction.ptt_ms == 0.0
    assert prediction.pr == 40

def test_build_feature_vector_converts_decimal_strings_in_legacy_order():
    """验证十进制资料转换、固定字段顺序和原始 sex 编码。"""
    profile = BPProfileSnapshot("24", "170.00", "55.00", 2)
    normalization = {
        "height": {"miu": 1.6, "segma": 0.2},
        "bmi": {"miu": 20.0, "segma": 5.0},
        "pr": {"miu": 60.0, "segma": 10.0},
        "age": {"miu": 20.0, "segma": 10.0},
        "X1": {"miu": 0.01, "segma": 0.02},
    }

    features = build_feature_vector(profile, 68.038462, 79, normalization)

    expected = np.asarray(
        [[2.9019231, 0.4, 1.9, -0.1937716262975778, 0.5, 2.0]]
    )
    np.testing.assert_allclose(features, expected, rtol=0.0, atol=1e-12)


def test_build_feature_vector_uses_zero_for_missing_numeric_profile_fields():
    """验证缺失年龄、身高、体重走零值路径且 gender=0 原样保留。"""
    profile = BPProfileSnapshot(None, None, None, 0)
    normalization = {
        "X1": {"miu": 1.0, "segma": 1.0},
        "age": {"miu": 2.0, "segma": 1.0},
        "pr": {"miu": 3.0, "segma": 1.0},
        "bmi": {"miu": 4.0, "segma": 1.0},
        "height": {"miu": 5.0, "segma": 1.0},
    }

    features = build_feature_vector(profile, 0, 0, normalization)

    np.testing.assert_array_equal(features, np.asarray([[-1, -2, -3, -4, -5, 0]]))
