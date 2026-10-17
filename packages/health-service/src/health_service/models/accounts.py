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
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="逻辑删除时间",
    )

    class Meta:
        db_table = "health_user"
        verbose_name = "用户"
        verbose_name_plural = verbose_name
