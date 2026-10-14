"""适配 django-ninja-jwt 的渠道绑定令牌签发。"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from ninja_jwt.tokens import RefreshToken

from health_service.domain.channels import ChannelLabel


@dataclass(frozen=True, slots=True)
class IssuedTokenPair:
    """承载一次登录签发的 Access 与 Refresh。"""

    access_token: str  # 短期访问凭证，含用户 ID 与渠道声明
    refresh_token: str  # 可轮换凭证，含独立 jti 与渠道声明


def issue_token_pair(user: AbstractBaseUser, channel: str) -> IssuedTokenPair:
    """创建同一账号和渠道绑定的一对 JWT。"""
    refresh = RefreshToken.for_user(user)
    refresh["channel"] = channel
    OutstandingToken.objects.filter(jti=refresh["jti"]).update(token=str(refresh))

    access = refresh.access_token
    return IssuedTokenPair(
        access_token=str(access),
        refresh_token=str(refresh),
    )


class InvalidRefreshTokenError(Exception):
    """表示 Refresh 无效、已撤销或所属账号不可用。"""


class RefreshChannelMismatchError(Exception):
    """表示 Refresh 与请求的普通渠道不匹配。"""


def _validated_refresh_token(raw_token: str) -> RefreshToken:
    """验证签名、期限、令牌类型和令牌必须包含的身份声明。"""
    try:
        refresh = RefreshToken(raw_token)
        user_id = refresh[api_settings.USER_ID_CLAIM]
        jti = refresh[api_settings.JTI_CLAIM]
        channel = refresh["channel"]
    except (KeyError, TokenError, TypeError, ValueError) as error:
        raise InvalidRefreshTokenError from error
    if (
        not isinstance(user_id, (int, str))
        or isinstance(user_id, bool)
        or not isinstance(jti, str)
        or not jti
        or not isinstance(channel, str)
        or not channel
    ):
        raise InvalidRefreshTokenError
    return refresh


def _lock_active_refresh_owner(
    refresh: RefreshToken, channel: str
) -> tuple[AbstractBaseUser, OutstandingToken]:
    """在事务内锁住旧 jti，并重检撤销状态、用户和渠道。"""
    try:
        outstanding = OutstandingToken.objects.select_for_update().get(
            jti=refresh[api_settings.JTI_CLAIM]
        )
    except OutstandingToken.DoesNotExist as error:
        raise InvalidRefreshTokenError from error

    if BlacklistedToken.objects.filter(token_id=outstanding.pk).exists():
        raise InvalidRefreshTokenError
    if str(outstanding.user_id) != str(refresh[api_settings.USER_ID_CLAIM]):
        raise InvalidRefreshTokenError

    user_model = get_user_model()
    try:
        user = user_model.objects.select_for_update().get(pk=outstanding.user_id)
    except user_model.DoesNotExist as error:
        raise InvalidRefreshTokenError from error
    if not user.is_active:
        raise InvalidRefreshTokenError
    if refresh["channel"] != channel:
        raise RefreshChannelMismatchError
    return user, outstanding


def _blacklist_locked_refresh(outstanding: OutstandingToken) -> None:
    """撤销当前事务已锁定且确认有效的 Refresh。"""
    BlacklistedToken.objects.create(token=outstanding)


def rotate_refresh_token(raw_token: str, channel: str) -> IssuedTokenPair:
    """原子撤销旧 Refresh 并为同一账号和渠道签发新令牌对。"""
    refresh = _validated_refresh_token(raw_token)
    with transaction.atomic():
        user, outstanding = _lock_active_refresh_owner(refresh, channel)
        if channel == ChannelLabel.MANAGER.value and not user.is_staff:
            raise InvalidRefreshTokenError
        _blacklist_locked_refresh(outstanding)
        return issue_token_pair(user, channel)


def revoke_refresh_token(raw_token: str, channel: str) -> None:
    """在事务中锁定并撤销指定渠道的当前 Refresh。"""
    refresh = _validated_refresh_token(raw_token)
    with transaction.atomic():
        _, outstanding = _lock_active_refresh_owner(refresh, channel)
        _blacklist_locked_refresh(outstanding)
