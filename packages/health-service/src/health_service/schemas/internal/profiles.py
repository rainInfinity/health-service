"""定义资料用例与持久化之间的已求值数据。"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ProfileData(BaseModel):
    """保存资料归属及当前资料值，不携带 ORM 对象。"""

    user_id: int = Field(description="资料所属账号的主键")
    name: str | None = Field(default=None, description="姓名；未填写时为 null")
    gender: int = Field(default=0, description="性别：0 未知、1 男、2 女")
    birth_date: date | None = Field(default=None, description="出生日期；未填写时为 null")
    height_cm: Decimal | None = Field(default=None, description="身高，单位 cm；未填写时为 null")
    weight_kg: Decimal | None = Field(default=None, description="体重，单位 kg；未填写时为 null")
    medical_history: str | None = Field(default=None, description="病史文字；未填写时为 null")
    history_json: dict[str, Any] | None = Field(
        default=None,
        description="结构化病史；内部已定义键使用下划线，缺席状态保持不变",
    )
