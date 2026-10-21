"""实现账号相关的具体 ORM 持久化操作。"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from health_service.infrastructure.persistence.exceptions import UsernameConflictError
from health_service.models import Channel
from health_service.schemas.internal import AccountSummary


def create_account(*, username: str, password: str, registration_channel: str) -> AccountSummary:
    """创建带注册来源、无手机号和管理权限的账号并返回最小结果。"""
    user_model = get_user_model()
    channel, _ = Channel.objects.get_or_create(label=registration_channel)
    try:
        user = user_model.objects.create_user(
            username=username,
            password=password,
            phone=None,
            is_staff=False,
            registration_channel=channel,
        )
    except IntegrityError as error:
        if _is_username_conflict(error, user_model):
            raise UsernameConflictError from error
        raise
    return AccountSummary(user_id=user.pk, username=user.username)


def _is_username_conflict(error: IntegrityError, user_model) -> bool:
    """只识别 PostgreSQL 的账号用户名唯一约束冲突。"""
    cause = error.__cause__
    diagnostic = getattr(cause, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    username_column = user_model._meta.get_field("username").column
    expected_name = f"{user_model._meta.db_table}_{username_column}_key"
    return constraint_name == expected_name
