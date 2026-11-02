"""验证血压 v1 的顺序 VMD 与 AO 模态选择边界。"""

import numpy as np
from health_inference.algorithms.ppg_acc_active_offline_bp_v1 import ao


def _install_fixed_modes(monkeypatch, modes, frequencies, waveform_factors):
    """注入固定模态及其频率、波形因子，隔离选择规则。"""
    def sequential_vmd(signal, vmd, numpy):
        """返回预设模态，避免测试调用真实 VMD。"""
        return modes

    def average_frequency(mode, sampling_rate_hz):
        """按模态首值返回预设平均频率。"""
        return frequencies[int(mode[0]) - 1]

    def waveform_factor(mode, numpy):
        """按模态首值返回预设波形因子。"""
        return waveform_factors[int(mode[0]) - 1]

    monkeypatch.setattr(ao, "_sequential_vmd", sequential_vmd)
    monkeypatch.setattr(ao, "_calculate_average_frequency", average_frequency)
    monkeypatch.setattr(ao, "_calculate_waveform_factor", waveform_factor)


def test_sequential_vmd_keeps_legacy_parameters_and_has_no_small_mode_cap():
    """验证固定 VMD 参数、1% 能量停止条件及超过常见小上限的循环。"""
    calls = []

    def vmd(residual, *parameters):
        """每轮提取残差的 10%，使循环需运行 22 次才达停止条件。"""
        calls.append(parameters)
        return np.asarray([residual * 0.1]), None, None

    modes = ao._sequential_vmd(np.ones(8), vmd, np)

    assert len(calls) == 22
    assert calls == [(2000, 0.0, 1, 0, 1, 1e-7)] * 22
    assert modes.shape == (22, 8)


def test_sequential_vmd_truncates_mode_and_residual_to_shorter_length():
    """验证 VMD 缩短信号时两者都截断到较短长度。"""
    signal = np.asarray([1.0, -2.0, 3.0, -4.0, 5.0])
    input_lengths = []

    def vmd(residual, *parameters):
        """返回比当前残差短一个样本的完整模态。"""
        input_lengths.append(len(residual))
        return np.asarray([residual[:-1]]), None, None

    modes = ao._sequential_vmd(signal, vmd, np)

    assert input_lengths == [5]
    assert modes.shape == (1, 4)
    np.testing.assert_array_equal(modes[0], signal[:-1])


def test_reconstruction_selects_intersection_and_includes_frequency_bounds(
    monkeypatch,
):
    """验证频率与波形因子交集选择，并包含 8 Hz、24 Hz 边界。"""
    modes = np.vstack([np.full(6, value) for value in (1.0, 2.0, 3.0)])
    _install_fixed_modes(
        monkeypatch,
        modes,
        frequencies=(8.0, 16.0, 24.0),
        waveform_factors=(1.0, 3.0, 4.0),
    )

    result = ao.reconstruct_ao_signal((0.0,) * 6, 200.0)

    np.testing.assert_array_equal(result, np.full(6, 5.0))


def test_reconstruction_falls_back_to_frequency_modes_when_intersection_empty(
    monkeypatch,
):
    """验证双条件交集为空时回退频率模态，严格保留频率边界。"""
    modes = np.vstack([np.full(6, value) for value in (1.0, 2.0, 3.0, 4.0)])
    _install_fixed_modes(
        monkeypatch,
        modes,
        frequencies=(8.0, 24.0, 7.999, 24.001),
        waveform_factors=(1.0, 2.0, 3.0, 4.0),
    )

    result = ao.reconstruct_ao_signal((0.0,) * 6, 200.0)

    np.testing.assert_array_equal(result, np.full(6, 3.0))


def test_reconstruction_returns_zeros_when_no_frequency_mode_is_available(
    monkeypatch,
):
    """验证频率范围内没有模态时按旧规则返回输入长度的零信号。"""
    modes = np.full((1, 5), 1.0)
    _install_fixed_modes(
        monkeypatch,
        modes,
        frequencies=(7.999,),
        waveform_factors=(2.0,),
    )

    result = ao.reconstruct_ao_signal((0.0,) * 5, 200.0)

    np.testing.assert_array_equal(result, np.zeros(5))


def test_reconstruction_keeps_vmd_truncated_output_length(monkeypatch):
    """验证选中截断模态时 AO 输出沿用 VMD 的较短长度。"""
    modes = np.full((1, 4), 1.0)
    _install_fixed_modes(
        monkeypatch,
        modes,
        frequencies=(16.0,),
        waveform_factors=(1.0,),
    )

    result = ao.reconstruct_ao_signal((0.0,) * 5, 200.0)

    np.testing.assert_array_equal(result, np.ones(4))


def test_reconstruction_returns_zeros_when_vmd_produces_no_modes(monkeypatch):
    """验证空 VMD 结果回退为与输入等长的零信号。"""
    def empty_modes(signal, vmd, numpy):
        """返回输入长度对应的空模态矩阵。"""
        return np.zeros((0, len(signal)))

    monkeypatch.setattr(ao, "_sequential_vmd", empty_modes)

    result = ao.reconstruct_ao_signal((0.0,) * 5, 200.0)

    np.testing.assert_array_equal(result, np.zeros(5))
