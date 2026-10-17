"""统一日志与公开回复的北京时间展示，内部时间值保持不变。"""

from datetime import datetime
from zoneinfo import ZoneInfo

_BEIJING = ZoneInfo("Asia/Shanghai")


def format_beijing_datetime(value: datetime) -> str:
    """将带时区时刻转换为北京时间，展示到秒且不四舍五入。"""
    return value.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
