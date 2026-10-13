"""编排账号注册与认证业务用例。"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction

from health_service.infrastructure.auth.tokens import (
    InvalidRefreshTokenError,
    IssuedTokenPair,
    RefreshChannelMismatchError,
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
)
from health_service.models import Profile

from .exceptions import (
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshChannelMismatch,
)


class UsernameAlreadyExists(Exception):
    """表示注册用户名已被其他账号占用。"""


def register_user(*, username: str, password: str):
    """校验注册信息，并在同一事务内创建账号及空资料。"""
    user_model = get_user_model()
    normalized_username = user_model.normalize_username(username)
    username_field = user_model._meta.get_field("username")
    username_field.run_validators(normalized_username)
    candidate = user_model(username=normalized_username)
    validate_password(password, user=candidate)

    try:
        with transaction.atomic():
            user = user_model.objects.create_user(
                username=normalized_username,
                password=password,
                phone=None,
                is_staff=False,
                is_superuser=False,
            )
            Profile.objects.create(user=user)
    except IntegrityError as error:
        if _is_username_conflict(error, user_model):
            raise UsernameAlreadyExists from error
        raise

    return user


def login_user(*, username: str, password: str, channel: str) -> IssuedTokenPair:
    """验证账号可用状态，并签发绑定当前渠道的令牌对。"""
    user_model = get_user_model()
    normalized_username = user_model.normalize_username(username)
    user = authenticate(username=normalized_username, password=password)
    if user is None:
        raise InvalidCredentials
    return issue_token_pair(user, channel)


def _is_username_conflict(error: IntegrityError, user_model) -> bool:
    """只识别 PostgreSQL 的账号用户名唯一约束冲突。"""
    cause = error.__cause__
    diagnostic = getattr(cause, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    username_column = user_model._meta.get_field("username").column
    expected_name = f"{user_model._meta.db_table}_{username_column}_key"
    return constraint_name == expected_name


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
