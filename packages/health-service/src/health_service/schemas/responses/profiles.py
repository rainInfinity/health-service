"""定义本人健康资料的公开响应字段、默认值和字段命名转换。"""

from datetime import date
from decimal import Decimal
from typing import Any

from ninja import Schema
from pydantic import ConfigDict, Field

from health_service.schemas.internal import ProfileData


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
        json_schema_extra={
            "properties": {
                "isDiabetic": {
                    "type": "boolean",
                    "description": "糖尿病标记；未填写时缺席",
                },
                "isHypertensive": {
                    "type": "boolean",
                    "description": "高血压标记；未填写时缺席",
                },
            },
            "additionalProperties": True,
        },
    )


def to_my_profile_response(profile: ProfileData | None) -> MyProfileResponse:
    """将内部资料结果转换为完整公开响应。"""
    if profile is None:
        return MyProfileResponse()
    return MyProfileResponse(
        name=profile.name,
        gender=profile.gender,
        birth_date=profile.birth_date,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        medical_history=profile.medical_history,
        history_json=_public_history_json(profile.history_json),
    )


def _public_history_json(history_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """只转换已定义病史键并保留扩展内容与键缺席状态。"""
    if history_json is None:
        return None
    public_history = dict(history_json)
    for internal_key, public_key in (
        ("is_diabetic", "isDiabetic"),
        ("is_hypertensive", "isHypertensive"),
    ):
        if internal_key in public_history:
            public_history[public_key] = public_history.pop(internal_key)
    return public_history
