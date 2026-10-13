"""定义本人健康资料部分更新的受限请求字段。"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from ninja import Schema
from pydantic import ConfigDict, Field, StrictInt, field_validator


class StructuredHistoryPatch(Schema):
    """只接受已定义的病史键，null 表示删除对应键。"""

    model_config = ConfigDict(extra="forbid")

    is_diabetic: bool | None = Field(
        default=None,
        alias="isDiabetic",
        description="糖尿病标记；null 删除该键",
    )
    is_hypertensive: bool | None = Field(
        default=None,
        alias="isHypertensive",
        description="高血压标记；null 删除该键",
    )


class ProfilePatchRequest(Schema):
    """接受有限字段的部分资料更新并保留缺席与 null 的区别。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=50, description="姓名；null 清空")
    gender: Annotated[StrictInt, Field(ge=0, le=2)] | None = Field(
        default=None,
        description="性别：0未知、1男、2女；不可为 null",
    )
    birth_date: date | None = Field(
        default=None,
        alias="birthDate",
        description="出生日期；null 清空",
    )
    height_cm: Decimal | None = Field(
        default=None,
        alias="heightCm",
        gt=0,
        max_digits=5,
        decimal_places=2,
        description="身高，单位 cm；null 清空，非空值必须为正数",
    )
    weight_kg: Decimal | None = Field(
        default=None,
        alias="weightKg",
        gt=0,
        max_digits=6,
        decimal_places=2,
        description="体重，单位 kg；null 清空，非空值必须为正数",
    )
    medical_history: str | None = Field(
        default=None,
        alias="medicalHistory",
        description="病史文字；null 清空",
    )
    history_json: StructuredHistoryPatch | None = Field(
        default=None,
        alias="historyJson",
        description="结构化病史的局部更新；null 清空整个对象",
    )

    @field_validator("gender")
    @classmethod
    def reject_null_gender(cls, value: StrictInt | None) -> StrictInt:
        """区分性别字段缺席和显式 null，后者不是有效取值。"""
        if value is None:
            raise ValueError("性别不能为 null")
        return value
