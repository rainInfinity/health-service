from django.contrib.auth.models import AbstractUser
from django.db import models

from .base import AuditModel


class User(AuditModel, AbstractUser):
    phone = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        db_comment="手机号；逻辑删除后仍占用",
    )
    registration_channel = models.ForeignKey(
        "health_service.Channel",
        on_delete=models.PROTECT,
        related_name="registered_users",
        null=True,
        blank=True,
        db_comment="账号首次注册来源渠道；历史来源未知时为空",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="逻辑删除时间",
    )

    class Meta:
        db_table = "health_user"
        verbose_name = "用户"
        verbose_name_plural = verbose_name
