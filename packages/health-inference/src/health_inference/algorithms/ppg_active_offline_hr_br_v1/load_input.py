"""读取并校验 HR/BR v1 所需的单块 PPG CSV 输入。"""

import math
import warnings
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal

from health_inference.exceptions import InputError, InputErrorReason

_RGB_COLUMNS = ("avgR", "avgG", "avgB")
_YUV_COLUMNS = ("avgY", "avgU", "avgV")
_TIMESTAMP_COLUMN = "timestampFrame"
_BLOCK_INDEX_COLUMN = "blockIndex"


@dataclass(frozen=True, slots=True)
class PPGInput:
    """保存一份通过结构和采样时间校验的 PPG 输入。"""

    timestamps_ms: tuple[float, ...]  # 每帧原始毫秒时间戳，严格递增。
    channels: tuple[tuple[float, float, float], ...]  # 每帧完整 RGB 或 YUV 通道值。
    channel_format: Literal["rgb", "yuv"]  # 本次输入实际提供的通道组。
    sampling_rate_hz: float  # 按相邻毫秒时间差均值并保留两位小数计算的采样率。


def load_ppg(path: Path) -> PPGInput:
    """用 pandas 读取 UTF-8/BOM CSV，并拒绝多块、缺列和无效时间轴。"""
    import pandas as pd

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", pd.errors.ParserWarning)
            table = pd.read_csv(
                path,
                header=None,
                dtype=str,
                encoding="utf-8-sig",
                keep_default_na=False,
                na_filter=False,
                skip_blank_lines=False,
                on_bad_lines="error",
                index_col=False,
                engine="python",
            )
    except (
        UnicodeError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
        pd.errors.ParserWarning,
    ) as error:
        raise InputError(InputErrorReason.CSV_FORMAT) from error

    if table.empty:
        raise InputError(InputErrorReason.CSV_FORMAT)
    headers = tuple(table.iloc[0].tolist())
    if not headers or any(not header for header in headers) or len(headers) != len(set(headers)):
        raise InputError(InputErrorReason.CSV_FORMAT)

    positions = {name: index for index, name in enumerate(headers)}
    if _TIMESTAMP_COLUMN not in positions:
        raise InputError(InputErrorReason.MISSING_COLUMN)

    present_rgb = set(headers).intersection(_RGB_COLUMNS)
    present_yuv = set(headers).intersection(_YUV_COLUMNS)
    has_rgb = present_rgb == set(_RGB_COLUMNS)
    has_yuv = present_yuv == set(_YUV_COLUMNS)
    if has_rgb == has_yuv or (present_rgb and not has_rgb) or (present_yuv and not has_yuv):
        raise InputError(InputErrorReason.INVALID_CHANNELS)

    channel_format: Literal["rgb", "yuv"] = "rgb" if has_rgb else "yuv"
    channel_columns = _RGB_COLUMNS if has_rgb else _YUV_COLUMNS
    timestamp_position = positions[_TIMESTAMP_COLUMN]
    block_position = positions.get(_BLOCK_INDEX_COLUMN)
    timestamps: list[float] = []
    channels: list[tuple[float, float, float]] = []
    rows = table.iloc[1:]
    if rows.isna().to_numpy().any():
        raise InputError(InputErrorReason.CSV_FORMAT)

    for row in rows.itertuples(index=False, name=None):
        if block_position is not None:
            try:
                block_index = int(row[block_position])
            except ValueError as error:
                raise InputError(InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT) from error
            if block_index != 0:
                raise InputError(InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT)

        timestamp = _parse_timestamp(row[timestamp_position])
        if timestamps and timestamp <= timestamps[-1]:
            raise InputError(InputErrorReason.INVALID_TIMESTAMP)
        timestamps.append(timestamp)
        channels.append(
            tuple(_parse_channel_value(row[positions[column]]) for column in channel_columns)
        )

    if len(timestamps) < 2:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)

    intervals_seconds = tuple((later - earlier) / 1000.0 for earlier, later in pairwise(timestamps))
    if any(not math.isfinite(interval) or interval <= 0 for interval in intervals_seconds):
        raise InputError(InputErrorReason.INVALID_TIMESTAMP)

    try:
        mean_interval = math.fsum(intervals_seconds) / len(intervals_seconds)
    except OverflowError as error:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE) from error
    if not math.isfinite(mean_interval) or mean_interval <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    sampling_rate_hz = 1.0 / mean_interval
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    sampling_rate_hz = round(sampling_rate_hz, 2)
    if sampling_rate_hz <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)

    return PPGInput(
        timestamps_ms=tuple(timestamps),
        channels=tuple(channels),
        channel_format=channel_format,
        sampling_rate_hz=sampling_rate_hz,
    )


def _parse_timestamp(value: str) -> float:
    """解析有限毫秒时间戳并将格式错误归入时间轴原因。"""
    try:
        timestamp = float(value)
    except ValueError as error:
        raise InputError(InputErrorReason.INVALID_TIMESTAMP) from error
    if not math.isfinite(timestamp):
        raise InputError(InputErrorReason.INVALID_TIMESTAMP)
    return timestamp


def _parse_channel_value(value: str) -> float:
    """解析有限通道值，区分非法文本与 NaN/无穷值。"""
    try:
        channel_value = float(value)
    except ValueError as error:
        raise InputError(InputErrorReason.INVALID_NUMERIC_VALUE) from error
    if not math.isfinite(channel_value):
        raise InputError(InputErrorReason.NON_FINITE_VALUE)
    return channel_value
