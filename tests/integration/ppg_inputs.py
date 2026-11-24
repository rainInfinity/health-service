"""生成满足正式 PPG v1 前提的确定性集成测试输入。"""

import csv
import math
import random
from io import StringIO


def synthetic_ppg_csv(
    *,
    heart_rate_hz: float,
    noise_amplitude: float = 0.0,
    duration_seconds: float = 60.0,
    respiration_rate_hz: float = 0.2,
) -> str:
    """生成指定时长和呼吸频率的 30 Hz 单块 RGB 信号，可确定性加入噪声。"""
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("timestampFrame", "avgR", "avgG", "avgB"))
    rng = random.Random(42)
    for index in range(int(duration_seconds * 30)):
        seconds = index / 30.0
        red = (
            120.0
            + 4.0 * math.sin(2.0 * math.pi * heart_rate_hz * seconds)
            + 2.0 * math.sin(2.0 * math.pi * respiration_rate_hz * seconds)
            + noise_amplitude * rng.uniform(-1.0, 1.0)
        )
        writer.writerow((index * 1000.0 / 30.0, red, 128.0, 136.0))
    return output.getvalue()
