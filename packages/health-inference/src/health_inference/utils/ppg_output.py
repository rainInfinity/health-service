"""按原有 CSV 合同写出独立心率或呼吸率的处理波形。"""

from pathlib import Path


def write_processed_csv(
    path: Path,
    *,
    signal_name: str,
    timestamps_ms: tuple[float, ...],
    signal: tuple[float, ...],
) -> None:
    """保留毫秒时间轴、列名和 CRLF 换行，不改变波形数值。"""
    import pandas as pd

    pd.DataFrame({"timestamp": timestamps_ms, signal_name: signal}).to_csv(
        path,
        index=False,
        encoding="utf-8",
        lineterminator="\r\n",
    )
