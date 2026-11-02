"""验证血压 v1 的旧版颜色转换、采样率和滤波标准化规则。"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    load_bp_input,
)
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.preprocessing import (
    preprocess_bp,
)
from health_inference.types import InferenceInput
from scipy.signal import butter, filtfilt


@pytest.mark.parametrize("channel_format", ["rgb", "yuv"])
def test_preprocessing_matches_legacy_filter_equations_without_resampling(
    tmp_path: Path,
    write_ppg_csv,
    write_acc_csv,
    channel_format: str,
):
    """两种颜色格式均复现旧转换/Butterworth/z-score且保留时间轴。"""
    ppg_path = write_ppg_csv(
        tmp_path / "ppg.csv",
        sample_count=900,
        start_ms=1000.0,
        channel_format=channel_format,
    )
    acc_path = write_acc_csv(
        tmp_path / "acc.csv",
        sample_count=6300,
        start_ms=1996.0,
    )
    inference_input = InferenceInput(
        files={"ppg": ppg_path, "acc": acc_path},
        artifacts={},
        profile={},
        params={},
        output_dir=tmp_path / "execution",
    )

    loaded = load_bp_input(inference_input)
    actual = preprocess_bp(loaded)

    legacy_ppg = pd.read_csv(ppg_path)
    if channel_format == "rgb":
        raw_ppg = -legacy_ppg["avgR"].to_numpy() / 255.0
    else:
        yuv_to_rgb = np.asarray(
            (
                (1.0, 0.0, 1.5748),
                (1.0, -0.1873, -0.4681),
                (1.0, 1.8556, 0.0),
            )
        )
        yuv = np.asarray(
            (
                legacy_ppg["avgY"].to_numpy(),
                legacy_ppg["avgU"].to_numpy() - 128.0,
                legacy_ppg["avgV"].to_numpy() - 128.0,
            )
        )
        raw_ppg = -(yuv_to_rgb @ yuv)[0] / 255.0
    ppg_nyquist = loaded.ppg_sampling_rate_hz / 2.0
    ppg_b, ppg_a = butter(
        2,
        (0.75 / ppg_nyquist, 4.0 / ppg_nyquist),
        btype="band",
    )
    expected_ppg = filtfilt(ppg_b, ppg_a, raw_ppg)
    expected_ppg = (expected_ppg - np.mean(expected_ppg)) / np.std(expected_ppg)

    legacy_acc = pd.read_csv(acc_path)
    raw_scg = -legacy_acc["accZ"].to_numpy()
    scg_nyquist = loaded.acc.sampling_rate_hz / 2.0
    scg_highcut = min(25.0, float(math.floor(loaded.acc.sampling_rate_hz / 2.0)))
    scg_b, scg_a = butter(
        2,
        (1.0 / scg_nyquist, scg_highcut / scg_nyquist),
        btype="bandpass",
        analog=False,
    )
    expected_scg = filtfilt(scg_b, scg_a, raw_scg)
    expected_scg = (expected_scg - np.mean(expected_scg)) / np.std(expected_scg)

    np.testing.assert_allclose(actual.ppg_signal, expected_ppg, rtol=0.0, atol=5e-13)
    np.testing.assert_allclose(actual.scg_signal, expected_scg, rtol=0.0, atol=5e-13)
    assert actual.ppg_timestamps_ms == loaded.ppg.timestamps_ms
    assert actual.scg_timestamps_ms == loaded.acc.timestamps_ms
    assert actual.ppg_timestamps_ms[0] == 1000.0
    assert actual.scg_timestamps_ms[0] == 1996.0
