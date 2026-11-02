"""读取并校验血压 v1 的 PPG、加速度及提交资料快照。"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from health_inference.exceptions import InferenceError, InputError, InputErrorReason
from health_inference.types import InferenceInput
from health_inference.utils.ppg_input import PPGInput, load_ppg

_ACC_COLUMNS = ("accX", "accY", "accZ")
_TIMESTAMP_COLUMN = "timestampEvent"
_FILTER_PAD_LENGTH = 15


@dataclass(frozen=True, slots=True)
class ACCInput:
    """保存经过结构和时间轴校验的加速度采样。"""

    timestamps_ms: tuple[float, ...]  # 加速度原始毫秒时间戳，严格递增。
    channels: tuple[tuple[float, float, float], ...]  # 每行 accX、accY、accZ 有限值。
    sampling_rate_hz: float  # 按旧版平均采样间隔并保留两位小数的采样率。


@dataclass(frozen=True, slots=True)
class BPProfileSnapshot:
    """保存任务提交时冻结的血压特征资料原值。"""

    age_years: object  # 年龄，沿用快照中的数值或十进制字符串。
    height_cm: object  # 身高，单位厘米；缺失值保留为 None。
    weight_kg: object  # 体重，单位千克；缺失值保留为 None。
    gender: object  # 性别编码原样传递，未知值通常为 0。


@dataclass(frozen=True, slots=True)
class LoadedBPInput:
    """保存本次任务两路输入、旧版采样率和资料快照。"""

    ppg: PPGInput  # 通过共享解析器校验的单块 PPG 输入。
    acc: ACCInput  # 通过血压专用解析器校验的加速度输入。
    ppg_sampling_rate_hz: float  # 按旧版 calc_fs 计算的 PPG 采样率。
    profile: BPProfileSnapshot  # 本次任务独占的提交资料快照。


def load_bp_input(inference_input: InferenceInput) -> LoadedBPInput:
    """读取双 CSV 和冻结资料，并拒绝滤波不可执行的采样边界。"""
    ppg = load_ppg(inference_input.files["ppg"])
    ppg_sampling_rate_hz = _legacy_sampling_rate(ppg.timestamps_ms)
    _validate_filter_input(
        len(ppg.timestamps_ms),
        ppg_sampling_rate_hz,
        lowcut_hz=0.75,
        highcut_hz=4.0,
    )
    acc = load_acc(inference_input.files["acc"])
    profile = inference_input.profile
    if not isinstance(profile, Mapping):
        raise InferenceError("INFERENCE.INVALID_PROFILE", "提交资料快照无效")
    return LoadedBPInput(
        ppg=ppg,
        acc=acc,
        ppg_sampling_rate_hz=ppg_sampling_rate_hz,
        profile=BPProfileSnapshot(
            age_years=profile.get("age_years"),
            height_cm=profile.get("height_cm"),
            weight_kg=profile.get("weight_kg"),
            gender=profile.get("gender"),
        ),
    )


def load_acc(path: Path) -> ACCInput:
    """读取含必需列的 ACC CSV，并拒绝无效值、时间轴和采样率。"""
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
    required_columns = (_TIMESTAMP_COLUMN, *_ACC_COLUMNS)
    if any(column not in positions for column in required_columns):
        raise InputError(InputErrorReason.MISSING_COLUMN)

    rows = table.iloc[1:]
    if rows.isna().to_numpy().any():
        raise InputError(InputErrorReason.CSV_FORMAT)

    timestamps_ms: list[float] = []
    channels: list[tuple[float, float, float]] = []
    for row in rows.itertuples(index=False, name=None):
        timestamp = _parse_timestamp(row[positions[_TIMESTAMP_COLUMN]])
        if timestamps_ms and timestamp <= timestamps_ms[-1]:
            raise InputError(InputErrorReason.INVALID_TIMESTAMP)
        timestamps_ms.append(timestamp)
        channels.append(
            tuple(_parse_numeric(row[positions[column]]) for column in _ACC_COLUMNS)
        )

    if len(timestamps_ms) < 2:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)
    sampling_rate_hz = _legacy_sampling_rate(tuple(timestamps_ms))
    highcut_hz = min(25.0, float(math.floor(sampling_rate_hz / 2.0)))
    _validate_filter_input(
        len(timestamps_ms),
        sampling_rate_hz,
        lowcut_hz=1.0,
        highcut_hz=highcut_hz,
    )
    return ACCInput(
        timestamps_ms=tuple(timestamps_ms),
        channels=tuple(channels),
        sampling_rate_hz=sampling_rate_hz,
    )


def _legacy_sampling_rate(timestamps_ms: tuple[float, ...]) -> float:
    """按旧版 `calc_fs` 的毫秒差分均值和两位小数规则计算采样率。"""
    import numpy as np

    timestamps = np.asarray(timestamps_ms, dtype=np.float64) / 1000.0
    intervals = np.diff(timestamps)
    intervals = intervals[intervals > 0]
    if intervals.size == 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    mean_interval = float(np.mean(intervals))
    if not math.isfinite(mean_interval) or mean_interval <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    sampling_rate_hz = round(1.0 / mean_interval, 2)
    if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0:
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    return sampling_rate_hz


def _validate_filter_input(
    sample_count: int,
    sampling_rate_hz: float,
    *,
    lowcut_hz: float,
    highcut_hz: float,
) -> None:
    """要求截止频率处于 Nyquist 内且样本长于旧滤波填充长度。"""
    nyquist_hz = sampling_rate_hz / 2.0
    if (
        not math.isfinite(sampling_rate_hz)
        or sampling_rate_hz <= 0
        or lowcut_hz >= highcut_hz
        or highcut_hz >= nyquist_hz
    ):
        raise InputError(InputErrorReason.INVALID_SAMPLING_RATE)
    if sample_count <= _FILTER_PAD_LENGTH:
        raise InputError(InputErrorReason.INSUFFICIENT_DATA)


def _parse_timestamp(value: str) -> float:
    """解析有限时间戳并将格式错误归为受控时间轴失败。"""
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as error:
        raise InputError(InputErrorReason.INVALID_TIMESTAMP) from error
    if not math.isfinite(timestamp):
        raise InputError(InputErrorReason.INVALID_TIMESTAMP)
    return timestamp


def _parse_numeric(value: str) -> float:
    """解析有限加速度值并区分文本错误与 NaN/无穷值。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise InputError(InputErrorReason.INVALID_NUMERIC_VALUE) from error
    if not math.isfinite(number):
        raise InputError(InputErrorReason.NON_FINITE_VALUE)
    return number
