"""提供糖尿病 v1 单元测试使用的确定性 PPG CSV。"""

import csv
import math
from pathlib import Path

import pytest


@pytest.fixture
def write_diabetes_ppg_csv():
    """返回可生成 RGB/YUV、指定采样率和块布局的 PPG 写入器。"""

    def write(
        path: Path,
        *,
        sample_count: int = 900,
        sampling_rate_hz: float = 30.0,
        start_ms: float = 1000.0,
        channel_format: str = "rgb",
        block_switch_at: int | None = None,
        non_finite_index: int | None = None,
    ) -> Path:
        """写入带脉搏与低频漂移的确定性采样点。"""
        columns = ("avgR", "avgG", "avgB") if channel_format == "rgb" else ("avgY", "avgU", "avgV")
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("timestampFrame", "blockIndex", *columns))
            for index in range(sample_count):
                seconds = index / sampling_rate_hz
                pulse = 7.0 * math.sin(2.0 * math.pi * 1.2 * seconds)
                drift = 2.0 * math.sin(2.0 * math.pi * 0.2 * seconds)
                value = 120.0 + pulse + drift
                channels = (
                    (value, 128.0 + pulse / 2.0, 136.0 - pulse / 3.0)
                    if channel_format == "rgb"
                    else (value, 128.0 + pulse / 2.0, 128.0 - pulse / 3.0)
                )
                if index == non_finite_index:
                    channels = ("NaN", *channels[1:])
                block_index = 1 if block_switch_at is not None and index >= block_switch_at else 0
                writer.writerow(
                    (
                        start_ms + index * 1000.0 / sampling_rate_hz,
                        block_index,
                        *channels,
                    )
                )
        return path

    return write
