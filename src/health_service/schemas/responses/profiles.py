"""定义本人健康资料的公开响应字段和未填写默认值。"""

from datetime import date
from decimal import Decimal
from typing import Any

from ninja import Schema
from pydantic import ConfigDict, Field


class MyProfileResponse(Schema):
    """公开本人资料字段，缺少资料时投影为未填写值。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    name: str | None = Field(default=None, description="姓名；未填写时为 null")
    gender: int = Field(default=0, description="性别：0未知、1男、2女")
    birth_date: date | None = Field(
        default=None,
        alias="birthDate",
        description="出生日期；未填写时为 null",
    )
    height_cm: Decimal | None = Field(
        default=None,
        alias="heightCm",
        description="身高，单位 cm；未填写时为 null",
    )
    weight_kg: Decimal | None = Field(
        default=None,
        alias="weightKg",
        description="体重，单位 kg；未填写时为 null",
    )
    medical_history: str | None = Field(
        default=None,
        alias="medicalHistory",
        description="病史文字；未填写时为 null",
    )
    history_json: dict[str, Any] | None = Field(
        default=None,
        alias="historyJson",
        description="结构化病史；未填写时为 null",
    )
