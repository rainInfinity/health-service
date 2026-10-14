"""为旧运行任务建立可恢复租约并添加执行字段。"""

import uuid
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def _expire_legacy_running_tasks(apps, schema_editor):
    """为迁移前遗留的运行任务建立已过期执行权。"""
    task_model = apps.get_model("health_service", "Task")
    database = schema_editor.connection.alias
    expiration = timezone.now() - timedelta(seconds=1)
    modified_at = timezone.now()
    batch = []

    for task in task_model.objects.using(database).filter(status="RUNNING").iterator(
        chunk_size=500
    ):
        task.execution_token = uuid.uuid4()
        task.lease_expires_at = expiration
        task.updated_at = modified_at
        task.updated_by = "system:migration"
        batch.append(task)
        if len(batch) == 500:
            task_model.objects.using(database).bulk_update(
                batch,
                ["execution_token", "lease_expires_at", "updated_at", "updated_by"],
                batch_size=500,
            )
            batch.clear()

    if batch:
        task_model.objects.using(database).bulk_update(
            batch,
            ["execution_token", "lease_expires_at", "updated_at", "updated_by"],
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('health_service', '0007_taskmetric'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='attempt_count',
            field=models.PositiveIntegerField(db_comment='有效执行领取次数', default=0),
        ),
        migrations.AddField(
            model_name='task',
            name='execution_token',
            field=models.UUIDField(blank=True, db_comment='当前执行权令牌', null=True),
        ),
        migrations.AddField(
            model_name='task',
            name='last_dispatched_at',
            field=models.DateTimeField(blank=True, db_comment='最近一次成功消息发布时间', null=True),
        ),
        migrations.AddField(
            model_name='task',
            name='lease_expires_at',
            field=models.DateTimeField(blank=True, db_comment='执行租约到期时间', null=True),
        ),
        migrations.RunPython(
            _expire_legacy_running_tasks,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('execution_token__isnull', False), ('lease_expires_at__isnull', False), ('status', 'RUNNING')), models.Q(('execution_token__isnull', True), ('lease_expires_at__isnull', True), ('status__in', ['DRAFT', 'SUBMITTED', 'SUCCEEDED', 'FAILED'])), _connector='OR'), name='health_task_execution_lease_consistent'),
        ),
        migrations.AddConstraint(
            model_name='task',
            constraint=models.CheckConstraint(condition=models.Q(('attempt_count__gte', 0)), name='health_task_attempt_count_nonnegative'),
        ),
    ]
