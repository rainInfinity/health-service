"""验证独立 HR 与 BR 共用的单块 PPG 输入格式和受控错误原因。"""

from pathlib import Path

import pytest
from health_inference.exceptions import InputError, InputErrorReason
from health_inference.utils.ppg_input import load_ppg


def _load_content(tmp_path: Path, content: str):
    """将测试 CSV 写入临时目录后交给正式解析器。"""
    path = tmp_path / "ppg.csv"
    path.write_text(content, encoding="utf-8")
    return load_ppg(path)


def test_loads_bom_rgb_and_uses_mean_millisecond_interval(tmp_path: Path):
    """旧版无帧号 RGB 文件可读取并按时间差均值计算采样率。"""
    result = _load_content(
        tmp_path,
        "\ufefftimestampFrame,avgR,avgG,avgB\n0,120,128,136\n100,122,129,139\n300,124,130,142\n",
    )

    assert result.timestamps_ms == (0.0, 100.0, 300.0)
    assert result.channels == ((120.0, 128.0, 136.0), (122.0, 129.0, 139.0), (124.0, 130.0, 142.0))
    assert result.channel_format == "rgb"
    assert result.sampling_rate_hz == 6.67


def test_loads_client_single_block_format_with_frame_metadata(tmp_path: Path):
    """客户端帧号及零块编号不阻止完整 RGB 单块输入。"""
    result = _load_content(
        tmp_path,
        "frameIndex,timestampFrame,blockIndex,avgR,avgG,avgB\n"
        "8,0,0,120,128,136\n"
        "9,100,0,122,129,139\n",
    )

    assert result.timestamps_ms == (0.0, 100.0)
    assert result.channel_format == "rgb"
    assert result.sampling_rate_hz == 10.0


def test_loads_complete_yuv_channel_group(tmp_path: Path):
    """完整 YUV 输入保留通道并得到采样率。"""
    result = _load_content(
        tmp_path,
        "timestampFrame,avgY,avgU,avgV\n0,120,128,128\n500,121,129,127\n",
    )

    assert result.channels == ((120.0, 128.0, 128.0), (121.0, 129.0, 127.0))
    assert result.channel_format == "yuv"
    assert result.sampling_rate_hz == 2.0


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("avgR,avgG,avgB\n1,2,3\n2,3,4\n", InputErrorReason.MISSING_COLUMN),
        (
            "timestampFrame,avgR,avgG\n0,1,2\n100,2,3\n",
            InputErrorReason.INVALID_CHANNELS,
        ),
        (
            ("timestampFrame,avgR,avgG,avgB,avgY,avgU,avgV\n0,1,2,3,4,5,6\n100,2,3,4,5,6,7\n"),
            InputErrorReason.INVALID_CHANNELS,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,not-a-number,2,3\n",
            InputErrorReason.INVALID_NUMERIC_VALUE,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,nan,2,3\n",
            InputErrorReason.NON_FINITE_VALUE,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,inf,2,3\n",
            InputErrorReason.NON_FINITE_VALUE,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\nnan,2,3,4\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n0,2,3,4\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n100,1,2,3\n0,2,3,4\n",
            InputErrorReason.INVALID_TIMESTAMP,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n",
            InputErrorReason.INSUFFICIENT_DATA,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n1e308,2,3,4\n",
            InputErrorReason.INVALID_SAMPLING_RATE,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,2,3,4,extra\n",
            InputErrorReason.CSV_FORMAT,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,2,3\n",
            InputErrorReason.CSV_FORMAT,
        ),
        (
            "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n\n100,2,3,4\n",
            InputErrorReason.CSV_FORMAT,
        ),
        (
            "timestampFrame,avgR,avgR,avgB\n0,1,2,3\n100,2,3,4\n",
            InputErrorReason.CSV_FORMAT,
        ),
        (
            'timestampFrame,avgR,avgG,avgB\n0,1,2,3\n100,"2"tail,3,4\n',
            InputErrorReason.CSV_FORMAT,
        ),
    ],
    ids=(
        "missing-timestamp",
        "incomplete-rgb",
        "both-channel-groups",
        "invalid-number",
        "nan-channel",
        "infinite-channel",
        "nan-timestamp",
        "duplicate-timestamp",
        "decreasing-timestamp",
        "one-sample",
        "rounded-zero-rate",
        "malformed-row",
        "short-row",
        "empty-row",
        "duplicate-header",
        "malformed-quote",
    ),
)
def test_rejects_invalid_input_with_controlled_reason(
    tmp_path: Path,
    content: str,
    reason: InputErrorReason,
):
    """输入格式、通道、数值、时间轴和执行条件失败使用受控原因。"""
    with pytest.raises(InputError) as error:
        _load_content(tmp_path, content)

    assert error.value.code == "INFERENCE.INVALID_INPUT"
    assert error.value.reason is reason
    assert str(tmp_path) not in error.value.safe_message


@pytest.mark.parametrize("block_value", ["1", "-1", "0.0", "unknown"])
def test_rejects_nonzero_or_non_integer_block_index(tmp_path: Path, block_value: str):
    """只接受整数零块编号，不静默选择或转换其他块。"""
    with pytest.raises(InputError) as error:
        _load_content(
            tmp_path,
            "timestampFrame,blockIndex,avgR,avgG,avgB\n"
            f"0,{block_value},1,2,3\n"
            f"100,{block_value},2,3,4\n",
        )

    assert error.value.reason is InputErrorReason.UNSUPPORTED_BLOCK_LAYOUT


def test_input_error_requires_a_declared_reason_and_keeps_legacy_code():
    """输入异常仅接受枚举原因并保持既有错误分类。"""
    error = InputError(InputErrorReason.CSV_FORMAT)

    assert error.code == "INFERENCE.INVALID_INPUT"
    assert error.reason is InputErrorReason.CSV_FORMAT
    with pytest.raises(TypeError, match="受控原因"):
        InputError("CSV_FORMAT")
