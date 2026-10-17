"""定义注册与密码登录的外部请求契约。"""

from ninja import Schema
from pydantic import ConfigDict, Field


class AccountCredentialsRequest(Schema):
    """接受公开账号用例所需的用户名和密码。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150, description="全局唯一用户名")
    password: str = Field(min_length=1, description="通过 Django 密码校验的明文密码")


class RefreshTokenRequest(Schema):
    """接受专用刷新接口中的 Refresh Token。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    refresh_token: str = Field(
        alias="refreshToken",
        min_length=1,
        description="用于签发同一渠道新令牌的一次性凭证",
    )
