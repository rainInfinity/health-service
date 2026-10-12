from django.conf import settings
from django.db import models

from .base import AuditModel


class Profile(AuditModel):
    class GenderChoices(models.IntegerChoices):
        UNKNOWN = 0, "未知"
        MALE = 1, "男"
        FEMALE = 2, "女"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.PROTECT,
        related_name="profile",
        db_comment="用户ID，同时作为资料主键",
    )
    name = models.CharField(max_length=50, null=True, blank=True, db_comment="姓名")
    gender = models.SmallIntegerField(
        choices=GenderChoices.choices,
        default=GenderChoices.UNKNOWN,
        db_comment="性别：0未知、1男、2女",
    )
    birth_date = models.DateField(null=True, blank=True, db_comment="出生日期")
    height_cm = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        db_comment="身高，单位 cm；未知为 NULL",
    )
    weight_kg = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        db_comment="体重，单位 kg；未知为 NULL",
    )
    medical_history = models.TextField(null=True, blank=True, db_comment="病史文字")
    history_json = models.JSONField(
        null=True,
        blank=True,
        db_comment="结构化病史；未填写的布尔键保持缺失",
    )

    class Meta:
        db_table = "health_profile"
        verbose_name = "健康资料"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gender__in=[0, 1, 2]),
                name="health_profile_gender_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(height_cm__isnull=True) | models.Q(height_cm__gt=0),
                name="health_profile_height_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(weight_kg__isnull=True) | models.Q(weight_kg__gt=0),
                name="health_profile_weight_positive",
            ),
        ]

    def __str__(self):
        return self.user.username
