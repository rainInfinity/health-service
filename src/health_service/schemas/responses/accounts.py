"""定义公开账号与令牌响应字段。"""

from ninja import Schema
from pydantic import ConfigDict, Field


class UserPublicResponse(Schema):
    """仅公开当前账号的稳定标识和用户名。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(alias="userId", description="全局账号 ID")
    username: str = Field(description="全局唯一用户名")


class TokenPairResponse(Schema):
    """返回当前渠道签发的 Access 与 Refresh。"""

    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(alias="accessToken", description="短期 Access Token")
    refresh_token: str = Field(alias="refreshToken", description="可轮换的 Refresh Token")
