"""验证血氧 v1 单块 PPG CSV 和执行边界。"""

from pathlib import Path

import pytest
from health_inference.algorithms.ppg_active_offline_spo2_v1.load_input import load_spo2_ppg
from health_inference.exceptions import InputError, InputErrorReason


def _write_regular_ppg(
    path: Path,
    *,
    sample_count: int = 450,
    sampling_rate_hz: float = 30.0,
    channel_format: str = "rgb",
) -> Path:
    """写入指定采样率和颜色格式的确定性单块 CSV。"""
    columns = (
        ("avgR", "avgG", "avgB")
        if channel_format == "rgb"
        else ("avgY", "avgU", "avgV")
    )
    lines = [f"timestampFrame,blockIndex,{','.join(columns)},unused"]
    for index in range(sample_count):
        timestamp_ms = index * 1000.0 / sampling_rate_hz
        first = 120.0 + index % 7
        values = (first, 128.0, 136.0)
        lines.append(",".join((f"{timestamp_ms:.12f}", "0", *(str(value) for value in values), "x")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_loads_complete_rgb_and_yuv_single_blocks(tmp_path: Path):
    """RGB/YUV 均保留时间轴、通道顺序和忽略列。"""
    rgb_path = _write_regular_ppg(tmp_path / "rgb.csv")
    yuv_path = _write_regular_ppg(tmp_path / "yuv.csv", channel_format="yuv")

    rgb = load_spo2_ppg(rgb_path)
    yuv = load_spo2_ppg(yuv_path)

    assert rgb.channel_format == "rgb"
    assert yuv.channel_format == "yuv"
    assert len(rgb.timestamps_ms) == len(yuv.timestamps_ms) == 450
    assert rgb.timestamps_ms[0] == yuv.timestamps_ms[0] == 0.0
    assert rgb.channels[0] == yuv.channels[0] == (120.0, 128.0, 136.0)
    assert rgb.sampling_rate_hz == yuv.sampling_rate_hz == 30.0


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            "timestampFrame,avgR,avgG,avgB\n0,120,128,136\n0,121,129,137\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
        ("avgR,avgG,avgB\n120,128,136\n", InputErrorReason.MISSING_COLUMN),
        (
            "timestampFrame,avgR,avgG\n0,120,128\n",
            InputErrorReason.INVALID_CHANNELS,
        ),
        (
            "timestampFrame,avgR,avgG,avgB,avgY,avgU,avgV\n0,120,128,136,120,128,136\n",
            InputErrorReason.INVALID_CHANNELS,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,NaN,128,136\n",
            InputErrorReason.NON_FINITE_VALUE,
        ),
        (
            (
                "timestampFrame,blockIndex,avgR,avgG,avgB\n0,0,120,128,136\n"
                "33.3,1,121,129,137\n"
            ),
            InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT,
        ),
    ],
)
def test_rejects_invalid_time_channels_values_and_blocks(tmp_path: Path, content, reason):
    """时间轴、通道组、非有限值和多块数据均使用受控输入原因。"""
    path = tmp_path / "invalid.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InputError) as error:
        load_spo2_ppg(path)

    assert error.value.reason is reason


def test_rejects_fewer_samples_than_one_window(tmp_path: Path):
    """不足固定 450 个样本时不插值或补齐。"""
    path = _write_regular_ppg(tmp_path / "short.csv", sample_count=449)

    with pytest.raises(InputError) as error:
        load_spo2_ppg(path)

    assert error.value.reason is InputErrorReason.INSUFFICIENT_DATA


def test_rejects_sampling_rate_at_filter_nyquist_limit(tmp_path: Path):
    """4 Hz 高截止在 8 Hz Nyquist 边界不可执行。"""
    path = _write_regular_ppg(
        tmp_path / "low-rate.csv",
        sample_count=450,
        sampling_rate_hz=8.0,
    )

    with pytest.raises(InputError) as error:
        load_spo2_ppg(path)

    assert error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE
