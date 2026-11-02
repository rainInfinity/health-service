"""提供血压 v1 单元测试使用的确定性双传感器 CSV。"""

import csv
import math
from pathlib import Path

import pytest


@pytest.fixture
def write_ppg_csv():
    """返回可生成 RGB/YUV、单块或多块 PPG 文件的测试写入器。"""
    def write(
        path: Path,
        *,
        sample_count: int = 900,
        sampling_rate_hz: float = 30.0,
        start_ms: float = 1000.0,
        channel_format: str = "rgb",
        block_index: int = 0,
    ) -> Path:
        """写入指定时间轴和通道格式的确定性 PPG CSV。"""
        columns = (
            ("avgR", "avgG", "avgB")
            if channel_format == "rgb"
            else ("avgY", "avgU", "avgV")
        )
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("timestampFrame", "blockIndex", *columns))
            for index in range(sample_count):
                seconds = index / sampling_rate_hz
                pulse = 7.0 * math.sin(2.0 * math.pi * 1.2 * seconds)
                drift = 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
                value = 120.0 + pulse + drift
                channels = (
                    (value, 128.0, 136.0)
                    if channel_format == "rgb"
                    else (value, 128.0, 128.0)
                )
                writer.writerow(
                    (
                        start_ms + index * 1000.0 / sampling_rate_hz,
                        block_index,
                        *channels,
                    )
                )
        return path

    return write


@pytest.fixture
def write_acc_csv():
    """返回可生成确定性 ACC 三轴数据的测试写入器。"""
    def write(
        path: Path,
        *,
        sample_count: int = 6300,
        sampling_rate_hz: float = 210.0,
        start_ms: float = 1996.0,
    ) -> Path:
        """写入保留原始时间起点的 ACC CSV。"""
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("timestampEvent", "accX", "accY", "accZ"))
            for index in range(sample_count):
                seconds = index / sampling_rate_hz
                z_value = -(
                    0.8 * math.sin(2.0 * math.pi * 15.0 * seconds)
                    + 0.3 * math.sin(2.0 * math.pi * 2.0 * seconds)
                )
                writer.writerow(
                    (
                        start_ms + index * 1000.0 / sampling_rate_hz,
                        0.1,
                        0.2,
                        z_value,
                    )
                )
        return path

    return write
