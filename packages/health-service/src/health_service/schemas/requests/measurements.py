"""定义健康历史的指标、北京时间日期和分页输入。"""

import re
from datetime import date
from typing import Annotated

from ninja import Schema
from pydantic import BeforeValidator, ConfigDict, Field, model_validator

from health_service.domain.measurements import Metric


def _strict_calendar_date(value: object) -> date:
    """只接受完整的公历日期，不接受时间戳或宽松日期格式。"""
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("日期必须为 YYYY-MM-DD")
    return date.fromisoformat(value)


def _integer_parameter(value: object) -> int:
    """解析十进制整数查询参数，拒绝小数和布尔隐式转换。"""
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    raise ValueError("分页参数必须为整数")


CalendarDate = Annotated[date, BeforeValidator(_strict_calendar_date)]
QueryInteger = Annotated[int, BeforeValidator(_integer_parameter)]


class MeasurementHistoryQuery(Schema):
    """限定一个指标在北京时间包含首尾日期内的历史页。"""

    model_config = ConfigDict(populate_by_name=True)

    metric: Metric = Field(description="必填健康指标")
    from_date: CalendarDate = Field(alias="from", description="北京时间起始日，YYYY-MM-DD，包含当天")
    to_date: CalendarDate = Field(alias="to", description="北京时间结束日，YYYY-MM-DD，包含当天")
    offset: QueryInteger = Field(default=0, ge=0, description="非负记录偏移")
    limit: QueryInteger = Field(default=20, ge=1, le=100, description="每页上限，1–100")

    @model_validator(mode="after")
    def validate_date_order(self) -> "MeasurementHistoryQuery":
        """拒绝反向区间，不静默交换日期。"""
        if self.from_date > self.to_date:
            raise ValueError("起始日不得晚于结束日")
        return self
