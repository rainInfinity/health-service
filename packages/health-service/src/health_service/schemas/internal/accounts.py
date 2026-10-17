"""定义账号用例与持久化之间的最小内部结果。"""

from pydantic import BaseModel, Field


class AccountSummary(BaseModel):
    """保存注册账号对应用层必需的字段。"""

    user_id: int = Field(description="账号的全局主键")
    username: str = Field(description="规范化后的全局用户名")
