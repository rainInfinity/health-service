"""验证糖尿病 v1 的 PPG 时长、采样率和单块输入边界。"""

from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_diabetes_v1.load_input import (
    load_diabetes_ppg,
)
from health_inference.exceptions import InputError, InputErrorReason


def test_loads_rgb_and_yuv_with_their_sampling_axis(write_diabetes_ppg_csv, tmp_path: Path):
    """RGB/YUV 均保留原始毫秒时间戳和共享解析器采样率。"""
    rgb_path = write_diabetes_ppg_csv(tmp_path / "rgb.csv")
    yuv_path = write_diabetes_ppg_csv(
        tmp_path / "yuv.csv",
        channel_format="yuv",
        start_ms=2500.0,
    )

    rgb = load_diabetes_ppg(rgb_path)
    yuv = load_diabetes_ppg(yuv_path)

    assert rgb.channel_format == "rgb"
    assert yuv.channel_format == "yuv"
    assert len(rgb.timestamps_ms) == len(yuv.timestamps_ms) == 900
    assert rgb.timestamps_ms[0] == 1000.0
    assert yuv.timestamps_ms[0] == 2500.0
    assert rgb.sampling_rate_hz == yuv.sampling_rate_hz == 30.0


@pytest.mark.parametrize("sample_count", (449,))
def test_rejects_less_than_fifteen_seconds_without_padding(
    write_diabetes_ppg_csv,
    tmp_path: Path,
    sample_count: int,
):
    """少于 pyPPG 所需 15 秒时受控失败，不插值或补齐。"""
    path = write_diabetes_ppg_csv(tmp_path / "short.csv", sample_count=sample_count)

    with pytest.raises(InputError) as error:
        load_diabetes_ppg(path)

    assert error.value.reason is InputErrorReason.INSUFFICIENT_DATA


def test_accepts_exactly_fifteen_seconds(write_diabetes_ppg_csv, tmp_path: Path):
    """满足 pyPPG 15 秒下限的 450 个 30 Hz 采样点可进入后续阶段。"""
    path = write_diabetes_ppg_csv(tmp_path / "minimum.csv", sample_count=450)

    loaded = load_diabetes_ppg(path)

    assert len(loaded.timestamps_ms) == 450


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"sampling_rate_hz": 8.0}, InputErrorReason.INVALID_SAMPLING_RATE),
        ({"block_switch_at": 450}, InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT),
        ({"non_finite_index": 20}, InputErrorReason.NON_FINITE_VALUE),
    ),
)
def test_rejects_unexecutable_sampling_and_block_layout(
    write_diabetes_ppg_csv,
    tmp_path: Path,
    kwargs: dict[str, object],
    reason: InputErrorReason,
):
    """滤波 Nyquist 边界、多块布局和非有限采样值均被拒绝。"""
    path = write_diabetes_ppg_csv(tmp_path / "invalid.csv", **kwargs)

    with pytest.raises(InputError) as error:
        load_diabetes_ppg(path)

    assert error.value.reason is reason
