from typing import ClassVar

from django.db import models

from .base import AuditModel


class Channel(AuditModel):
    label = models.CharField(max_length=32, unique=True, db_comment="唯一渠道标识")

    class Meta:
        db_table = "health_channel"
        verbose_name = "渠道"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.label


class Model(AuditModel):
    code = models.CharField(max_length=100, unique=True, db_comment="唯一模型编码")
    name = models.CharField(max_length=255, db_comment="模型名称")
    description = models.TextField(null=True, blank=True, db_comment="模型描述")

    class Meta:
        db_table = "health_model"
        verbose_name = "模型"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.code


class ModelVersion(AuditModel):
    model = models.ForeignKey(
        "health_service.Model",
        on_delete=models.PROTECT,
        related_name="versions",
        db_comment="所属模型",
    )
    version = models.CharField(max_length=64, db_comment="模型版本号")
    algorithm_key = models.CharField(max_length=255, db_comment="算法标识")
    params = models.JSONField(null=True, blank=True, db_comment="模型参数")
    published_at = models.DateTimeField(null=True, blank=True, db_comment="发布时间")

    class Meta:
        db_table = "health_model_version"
        verbose_name = "模型版本"
        verbose_name_plural = verbose_name
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["model", "version"],
                name="health_model_version_unique",
            ),
        ]

    def __str__(self):
        return f"{self.model.code}:{self.version}"


class ChannelModelSelection(AuditModel):
    channel = models.ForeignKey(
        "health_service.Channel",
        on_delete=models.PROTECT,
        related_name="model_selections",
        db_comment="所属渠道",
    )
    model = models.ForeignKey(
        "health_service.Model",
        on_delete=models.PROTECT,
        related_name="channel_selections",
        db_comment="配置的模型",
    )
    current_version = models.ForeignKey(
        "health_service.ModelVersion",
        on_delete=models.PROTECT,
        related_name="channel_selections",
        db_comment="渠道当前选择的模型版本",
    )

    class Meta:
        db_table = "health_channel_model_selection"
        verbose_name = "渠道模型配置"
        verbose_name_plural = verbose_name
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["channel", "model"],
                name="health_channel_model_unique",
            ),
        ]


class ModelArtifact(AuditModel):
    model_version = models.ForeignKey(
        "health_service.ModelVersion",
        on_delete=models.PROTECT,
        related_name="artifacts",
        db_comment="所属模型版本",
    )
    logical_name = models.CharField(max_length=255, db_comment="制品逻辑名")
    storage_key = models.TextField(unique=True, db_comment="全局唯一制品存储键")
    size_bytes = models.BigIntegerField(db_comment="制品大小，单位字节")

    class Meta:
        db_table = "health_model_artifact"
        verbose_name = "模型制品"
        verbose_name_plural = verbose_name
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["model_version", "logical_name"],
                name="health_artifact_name_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0),
                name="health_artifact_size_positive",
            ),
        ]

    def __str__(self):
        return self.logical_name


class MetricDefinition(AuditModel):
    class ValueTypeChoices(models.TextChoices):
        NUMBER = "number", "数值"
        BOOLEAN = "boolean", "布尔"

    code = models.CharField(max_length=128, unique=True, db_comment="唯一指标代码")
    description = models.TextField(null=True, blank=True, db_comment="指标含义")
    value_type = models.CharField(
        max_length=16,
        choices=ValueTypeChoices.choices,
        db_comment="指标值类型：数值或布尔",
    )
    unit = models.CharField(max_length=32, null=True, blank=True, db_comment="指标单位")

    class Meta:
        db_table = "health_metric_definition"
        verbose_name = "指标定义"
        verbose_name_plural = verbose_name
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(value_type__in=["number", "boolean"]),
                name="health_metric_value_type_valid",
            ),
        ]

    def __str__(self):
        return self.code


class ModelVersionMetric(AuditModel):
    model_version = models.ForeignKey(
        "health_service.ModelVersion",
        on_delete=models.PROTECT,
        related_name="metric_definitions",
        db_comment="模型版本",
    )
    metric = models.ForeignKey(
        "health_service.MetricDefinition",
        on_delete=models.PROTECT,
        related_name="model_versions",
        db_comment="可投影指标",
    )

    class Meta:
        db_table = "health_model_version_metric"
        verbose_name = "模型版本指标"
        verbose_name_plural = verbose_name
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["model_version", "metric"],
                name="health_version_metric_unique",
            ),
        ]
