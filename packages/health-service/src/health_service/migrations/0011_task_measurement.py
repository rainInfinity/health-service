"""添加可空采集属性与查询索引，不回填历史或修改结果及投影。"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """只扩展任务结构，既有记录的采集属性保持未知。"""

    dependencies = [("health_service", "0010_task_client_metadata")]

    operations = [
        migrations.AddField(
            model_name="task",
            name="capture_method",
            field=models.CharField(
                max_length=32,
                null=True,
                blank=True,
                db_comment="提交时冻结的采集方式；缺失保持未知",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="capture_mode",
            field=models.CharField(
                max_length=16,
                null=True,
                blank=True,
                db_comment="提交时冻结的采集来源；当前仅 active",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="duration_ms",
            field=models.BigIntegerField(
                null=True,
                blank=True,
                db_comment="实际采集时长，单位毫秒；缺失保持未知",
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        capture_method__isnull=True,
                        capture_mode__isnull=True,
                        duration_ms__isnull=True,
                    )
                    | models.Q(
                        capture_method__isnull=False,
                        capture_mode__isnull=False,
                        duration_ms__isnull=False,
                        capture_method__in=["fingertip", "fingertip_chest"],
                        capture_mode="active",
                        duration_ms__gt=0,
                    )
                ),
                name="health_task_measurement_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status="DRAFT")
                    | models.Q(
                        capture_method__isnull=True,
                        capture_mode__isnull=True,
                        duration_ms__isnull=True,
                    )
                ),
                name="health_task_draft_measurement_null",
            ),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["user", "channel", "-submitted_at", "-id"],
                name="htask_user_chan_submit_idx",
            ),
        ),
    ]
