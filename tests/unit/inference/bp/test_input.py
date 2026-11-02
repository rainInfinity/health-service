"""验证血压 v1 双传感器 CSV、资料快照和可执行采样边界。"""

from pathlib import Path

import pytest
from health_inference.algorithms.ppg_acc_active_offline_bp_v1.load_input import (
    load_acc,
    load_bp_input,
)
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.types import InferenceInput


def _inference_input(ppg_path: Path, acc_path: Path, output_dir: Path) -> InferenceInput:
    """组装本次血压输入，保留来源文件的原始路径。"""
    return InferenceInput(
        files={"ppg": ppg_path, "acc": acc_path},
        artifacts={},
        profile={
            "age_years": "24.5",
            "height_cm": "170.0",
            "weight_kg": "55.25",
            "gender": 1,
        },
        params={},
        output_dir=output_dir,
    )


@pytest.mark.parametrize("channel_format", ["rgb", "yuv"])
def test_loads_both_sensor_files_without_time_alignment(
    tmp_path: Path, write_ppg_csv, write_acc_csv, channel_format: str
):
    """复用共享 RGB/YUV 单块校验并保留两路不同的原始起点。"""
    ppg_path = write_ppg_csv(
        tmp_path / "ppg.csv",
        channel_format=channel_format,
        start_ms=1000.0,
    )
    acc_path = write_acc_csv(tmp_path / "acc.csv", start_ms=1996.0)

    loaded = load_bp_input(_inference_input(ppg_path, acc_path, tmp_path / "output"))

    assert loaded.ppg.channel_format == channel_format
    assert len(loaded.ppg.timestamps_ms) == 900
    assert len(loaded.acc.timestamps_ms) == 6300
    assert loaded.ppg.timestamps_ms[0] == 1000.0
    assert loaded.acc.timestamps_ms[0] == 1996.0
    assert loaded.ppg_sampling_rate_hz == 30.0
    assert loaded.acc.sampling_rate_hz == 210.0
    assert loaded.profile.age_years == "24.5"
    assert loaded.profile.height_cm == "170.0"
    assert loaded.profile.weight_kg == "55.25"
    assert loaded.profile.gender == 1


def test_rejects_nonzero_ppg_block_through_shared_parser(
    tmp_path: Path, write_ppg_csv, write_acc_csv
):
    """PPG 多块输入沿用共享解析器的受控拒绝。"""
    ppg_path = write_ppg_csv(tmp_path / "ppg.csv", block_index=1)
    acc_path = write_acc_csv(tmp_path / "acc.csv")

    with pytest.raises(InputError) as error:
        load_bp_input(_inference_input(ppg_path, acc_path, tmp_path / "output"))

    assert error.value.reason is InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            "timestampEvent,accX,accZ\n0,0,0\n5,0,1\n",
            InputErrorReason.MISSING_COLUMN,
        ),
        (
            "timestampEvent,accX,accY,accZ\n0,0,0,0\n5,NaN,0,1\n",
            InputErrorReason.NON_FINITE_VALUE,
        ),
        (
            "timestampEvent,accX,accY,accZ\n0,0,0,0\n5,not-a-number,0,1\n",
            InputErrorReason.INVALID_NUMERIC_VALUE,
        ),
        (
            "timestampEvent,accX,accY,accZ\n5,0,0,0\n5,0,0,1\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
    ],
)
def test_rejects_invalid_acc_columns_values_and_time_axis(
    tmp_path: Path, content: str, reason: InputErrorReason
):
    """ACC 缺列、非有限值、非法数值和非递增时间轴均失败。"""
    path = tmp_path / "invalid-acc.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InputError) as error:
        load_acc(path)

    assert error.value.reason is reason


def test_rejects_ppg_sampling_rate_at_filter_nyquist_boundary(
    tmp_path: Path, write_ppg_csv, write_acc_csv
):
    """4 Hz PPG 高截止在 8 Hz Nyquist 边界不可执行。"""
    ppg_path = write_ppg_csv(
        tmp_path / "ppg.csv",
        sample_count=120,
        sampling_rate_hz=8.0,
    )
    acc_path = write_acc_csv(tmp_path / "acc.csv")

    with pytest.raises(InputError) as error:
        load_bp_input(_inference_input(ppg_path, acc_path, tmp_path / "output"))

    assert error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE


def test_rejects_acc_filter_nyquist_boundary(tmp_path: Path):
    """旧 ACC 截止规则落在 Nyquist 边界时明确拒绝。"""
    path = tmp_path / "low-rate-acc.csv"
    rows = ["timestampEvent,accX,accY,accZ"]
    rows.extend(f"{index * 250},0,0,{index % 2}" for index in range(24))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(InputError) as error:
        load_acc(path)

    assert error.value.reason is InputErrorReason.INVALID_SAMPLING_RATE


@pytest.mark.parametrize("sample_count", [15, 16])
def test_filter_input_length_matches_filtfilt_padding(
    tmp_path: Path, sample_count: int, write_acc_csv
):
    """滤波输入必须长于 15 个样本填充长度，不插值或补齐。"""
    path = tmp_path / "acc.csv"
    write_acc_csv(path, sample_count=sample_count)

    if sample_count == 15:
        with pytest.raises(InputError) as error:
            load_acc(path)
        assert error.value.reason is InputErrorReason.INSUFFICIENT_DATA
    else:
        assert len(load_acc(path).timestamps_ms) == 16
