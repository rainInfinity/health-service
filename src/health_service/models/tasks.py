from django.db import models

from .base import AuditModel


class Task(AuditModel):
    class StatusChoices(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        SUBMITTED = "SUBMITTED", "已提交"
        RUNNING = "RUNNING", "执行中"
        SUCCEEDED = "SUCCEEDED", "成功"
        FAILED = "FAILED", "失败"

    user = models.ForeignKey(
        "health_service.User",
        on_delete=models.PROTECT,
        related_name="tasks",
        db_comment="任务归属用户",
    )
    channel = models.ForeignKey(
        "health_service.Channel",
        on_delete=models.PROTECT,
        related_name="tasks",
        db_comment="任务来源渠道",
    )
    model_version = models.ForeignKey(
        "health_service.ModelVersion",
        on_delete=models.PROTECT,
        related_name="tasks",
        db_comment="任务固定执行版本",
    )
    status = models.CharField(
        max_length=16,
        choices=StatusChoices.choices,
        default=StatusChoices.DRAFT,
        db_comment="任务状态",
    )
    profile_snapshot = models.JSONField(
        null=True,
        blank=True,
        db_comment="提交时冻结的健康资料快照",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_comment="提交时间")
    started_at = models.DateTimeField(null=True, blank=True, db_comment="开始执行时间")
    finished_at = models.DateTimeField(null=True, blank=True, db_comment="结束时间")
    result_payload = models.JSONField(
        null=True,
        blank=True,
        db_comment="完整成功结果",
    )
    failure_code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_comment="稳定失败代码",
    )
    failure_message = models.TextField(
        null=True,
        blank=True,
        db_comment="可安全对外展示的失败摘要",
    )
    failure_detail = models.JSONField(
        null=True,
        blank=True,
        db_comment="结构化失败诊断详情",
    )

    class Meta:
        db_table = "health_task"
        verbose_name = "推理任务"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["DRAFT", "SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED"]
                ),
                name="health_task_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="SUCCEEDED", result_payload__isnull=False)
                    | models.Q(
                        status__in=["DRAFT", "SUBMITTED", "RUNNING", "FAILED"],
                        result_payload__isnull=True,
                    )
                ),
                name="health_task_result_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["SUCCEEDED", "FAILED"],
                        finished_at__isnull=False,
                    )
                    | models.Q(
                        status__in=["DRAFT", "SUBMITTED", "RUNNING"],
                        finished_at__isnull=True,
                    )
                ),
                name="health_task_terminal_time",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(status="FAILED", failure_code__isnull=False)
                        & ~models.Q(failure_code="")
                    )
                    | models.Q(
                        status__in=["DRAFT", "SUBMITTED", "RUNNING", "SUCCEEDED"],
                        failure_code__isnull=True,
                        failure_message__isnull=True,
                        failure_detail__isnull=True,
                    )
                ),
                name="health_task_failure_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="htask_user_created_idx",
            ),
            models.Index(
                fields=["channel", "model_version", "status"],
                name="htask_chan_ver_status_idx",
            ),
            models.Index(
                fields=["model_version", "status"],
                name="htask_version_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.pk}:{self.status}"


class TaskInputFile(AuditModel):
    task = models.ForeignKey(
        "health_service.Task",
        on_delete=models.PROTECT,
        related_name="input_files",
        db_comment="所属任务",
    )
    logical_name = models.CharField(max_length=255, db_comment="输入文件逻辑名")
    storage_key = models.TextField(unique=True, db_comment="全局唯一原始文件存储键")
    size_bytes = models.BigIntegerField(db_comment="文件大小，单位字节")
    uploaded_at = models.DateTimeField(db_comment="上传完成时间")
    is_selected = models.BooleanField(db_comment="是否为当前选中输入")

    class Meta:
        db_table = "health_task_input_file"
        verbose_name = "任务输入文件"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["task", "logical_name"],
                condition=models.Q(is_selected=True),
                name="health_input_selected_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0),
                name="health_input_size_positive",
            ),
        ]

    def __str__(self):
        return f"{self.task_id}:{self.logical_name}"


class TaskMetric(AuditModel):
    task = models.ForeignKey(
        "health_service.Task",
        on_delete=models.PROTECT,
        related_name="metrics",
        db_comment="所属推理任务",
    )
    metric = models.ForeignKey(
        "health_service.MetricDefinition",
        on_delete=models.PROTECT,
        related_name="task_values",
        db_comment="指标定义",
    )
    numeric_value = models.FloatField(
        null=True,
        blank=True,
        db_comment="数值指标值",
    )
    boolean_value = models.BooleanField(
        null=True,
        blank=True,
        db_comment="布尔指标值；false 与 NULL 含义不同",
    )

    class Meta:
        db_table = "health_task_metric"
        verbose_name = "任务指标投影"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["task", "metric"],
                name="health_task_metric_unique",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(numeric_value__isnull=False)
                        & models.Q(boolean_value__isnull=True)
                    )
                    | (
                        models.Q(numeric_value__isnull=True)
                        & models.Q(boolean_value__isnull=False)
                    )
                ),
                name="health_task_metric_one_value",
            ),
        ]

    def __str__(self):
        return f"{self.task_id}:{self.metric_id}"
