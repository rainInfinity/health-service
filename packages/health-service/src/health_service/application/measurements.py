"""读取本人当前渠道的最新有效健康结果和北京时间历史。"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError

from health_service.application.task_exceptions import TaskNotFound
from health_service.domain.channels import ORDINARY_CHANNELS
from health_service.domain.measurements import METRICS, Metric
from health_service.infrastructure.persistence import measurements as persistence
from health_service.schemas.internal.measurements import MeasurementRecordData

_BEIJING = ZoneInfo("Asia/Shanghai")


def read_latest_measurements(
    *, user_id: int, channel: str
) -> dict[Metric, MeasurementRecordData | None]:
    """为固定五项指标分别选取最新有效记录，不依赖最近任务状态。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    return {
        metric: persistence.latest_user_measurement(
            user_id=user_id, channel_label=channel, metric=metric
        )
        for metric in METRICS
    }


def list_measurements(
    *,
    user_id: int,
    channel: str,
    metric: Metric,
    from_date: date,
    to_date: date,
    offset: int,
    limit: int,
) -> tuple[int, tuple[MeasurementRecordData, ...]]:
    """将北京时间首尾日转换为半开区间后查询完整有效集合。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    if (
        metric not in METRICS
        or from_date > to_date
        or type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValidationError("健康查询参数无效")
    start = datetime.combine(from_date, time.min, tzinfo=_BEIJING)
    # 在 UTC 上推进最后一微秒，避免合法首尾年份发生 datetime 溢出。
    end = (
        datetime.combine(to_date, time.max, tzinfo=_BEIJING).astimezone(UTC)
        + timedelta(microseconds=1)
    )
    return persistence.list_user_measurements(
        user_id=user_id,
        channel_label=channel,
        metric=metric,
        from_time=start,
        to_time=end,
        offset=offset,
        limit=limit,
    )
