"""提供公开回复专用类型，使实际序列化与 OpenAPI 契约一致。"""

from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

from health_service.time_formats import format_beijing_datetime

# 仅在公开字段序列化时转换；Ninja 的 Python dump 也必须应用此规则。
BeijingDateTime = Annotated[
    datetime,
    PlainSerializer(format_beijing_datetime, return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$",
            "description": "北京时间（Asia/Shanghai），格式 YYYY-MM-DD HH:mm:ss",
            "examples": ["2026-10-10 19:19:19"],
        }
    ),
]
