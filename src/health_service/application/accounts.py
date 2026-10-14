"""编排账号注册与认证业务用例。"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from health_service.domain.channels import ChannelLabel
from health_service.infrastructure.auth.tokens import (
    InvalidRefreshTokenError,
    IssuedTokenPair,
    RefreshChannelMismatchError,
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
)
from health_service.infrastructure.persistence.accounts import create_account
from health_service.infrastructure.persistence.exceptions import UsernameConflictError
from health_service.infrastructure.persistence.profiles import create_profile
from health_service.schemas.internal import AccountSummary

from .exceptions import (
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshChannelMismatch,
)


class UsernameAlreadyExists(Exception):
    """表示注册用户名已被其他账号占用。"""


def register_user(*, username: str, password: str) -> AccountSummary:
    """校验注册信息，并在同一事务内创建账号及空资料。"""
    user_model = get_user_model()
    normalized_username = user_model.normalize_username(username)
    username_field = user_model._meta.get_field("username")
    username_field.run_validators(normalized_username)
    candidate = user_model(username=normalized_username)
    validate_password(password, user=candidate)

    try:
        with transaction.atomic():
            account = create_account(username=normalized_username, password=password)
            create_profile(user_id=account.user_id)
    except UsernameConflictError as error:
        raise UsernameAlreadyExists from error

    return account


def login_user(*, username: str, password: str, channel: str) -> IssuedTokenPair:
    """验证账号可用状态及管理权限，并签发绑定当前渠道的令牌对。"""
    user_model = get_user_model()
    normalized_username = user_model.normalize_username(username)
    user = authenticate(username=normalized_username, password=password)
    if user is None or (
        channel == ChannelLabel.MANAGER.value and not user.is_staff
    ):
        raise InvalidCredentials
    return issue_token_pair(user, channel)




def refresh_user_tokens(*, raw_token: str, channel: str) -> IssuedTokenPair:
    """将令牌适配层错误转换为稳定的账号用例异常。"""
    try:
        return rotate_refresh_token(raw_token, channel)
    except InvalidRefreshTokenError as error:
        raise InvalidRefreshToken from error
    except RefreshChannelMismatchError as error:
        raise RefreshChannelMismatch from error


def logout_user(*, raw_token: str, channel: str) -> None:
    """将令牌适配层登出错误转换为稳定的账号用例异常。"""
    try:
        revoke_refresh_token(raw_token, channel)
    except InvalidRefreshTokenError as error:
        raise InvalidRefreshToken from error
    except RefreshChannelMismatchError as error:
        raise RefreshChannelMismatch from error
