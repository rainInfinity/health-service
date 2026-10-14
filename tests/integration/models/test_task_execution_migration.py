"""验证任务执行字段迁移保留历史并恢复旧运行任务。"""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

MIGRATION_BEFORE_EXECUTION_FIELDS = ("health_service", "0007_taskmetric")
MIGRATION_WITH_EXECUTION_FIELDS = (
    "health_service",
    "0008_task_attempt_count_task_execution_token_and_more",
)


def _create_legacy_tasks(apps):
    """在旧表结构中建立成功、失败和运行中任务。"""
    user_model = apps.get_model("health_service", "User")
    channel_model = apps.get_model("health_service", "Channel")
    model_model = apps.get_model("health_service", "Model")
    version_model = apps.get_model("health_service", "ModelVersion")
    task_model = apps.get_model("health_service", "Task")
    now = timezone.now()

    user = user_model.objects.create(
        username="migration-user",
        password="not-a-real-hash",
        created_at=now,
        updated_at=now,
    )
    channel = channel_model.objects.create(
        label="migration-channel",
        created_at=now,
        updated_at=now,
    )
    model = model_model.objects.create(
        code="migration-model",
        name="Migration model",
        created_at=now,
        updated_at=now,
    )
    version = version_model.objects.create(
        model=model,
        version="1.0",
        algorithm_key="migration.example",
        created_at=now,
        updated_at=now,
    )
    shared = {
        "user": user,
        "channel": channel,
        "model_version": version,
        "profile_snapshot": {"age": 42},
        "submitted_at": now - timedelta(minutes=3),
        "started_at": now - timedelta(minutes=2),
        "created_at": now - timedelta(minutes=4),
        "updated_at": now - timedelta(minutes=2),
    }
    success = task_model.objects.create(
        **shared,
        status="SUCCEEDED",
        finished_at=now,
        result_payload={"example": True, "sample_count": 8},
    )
    failure = task_model.objects.create(
        **shared,
        status="FAILED",
        finished_at=now,
        failure_code="INFERENCE.INVALID_INPUT",
        failure_message="输入无效",
        failure_detail={"reason": "invalid"},
    )
    running = task_model.objects.create(**shared, status="RUNNING")
    return (
        success.pk,
        failure.pk,
        running.pk,
        shared["started_at"],
        shared["submitted_at"],
        now,
    )


@pytest.mark.django_db(transaction=True)
def test_migration_preserves_terminal_history_and_expires_legacy_running_tasks():
    """验证迁移保留终态历史并使旧运行任务可恢复。"""
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_BEFORE_EXECUTION_FIELDS])
        old_apps = executor.loader.project_state([MIGRATION_BEFORE_EXECUTION_FIELDS]).apps
        (
            success_id,
            failure_id,
            running_id,
            started_at,
            submitted_at,
            finished_at,
        ) = _create_legacy_tasks(old_apps)
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_WITH_EXECUTION_FIELDS])
        current_apps = executor.loader.project_state([MIGRATION_WITH_EXECUTION_FIELDS]).apps
        task_model = current_apps.get_model("health_service", "Task")
        success = task_model.objects.get(pk=success_id)
        failure = task_model.objects.get(pk=failure_id)
        running = task_model.objects.get(pk=running_id)

        assert success.result_payload == {"example": True, "sample_count": 8}
        assert success.profile_snapshot == {"age": 42}
        assert success.submitted_at == submitted_at
        assert success.started_at == started_at
        assert success.finished_at == finished_at
        assert success.finished_at is not None
        assert success.execution_token is None
        assert success.lease_expires_at is None
        assert success.attempt_count == 0
        assert failure.failure_code == "INFERENCE.INVALID_INPUT"
        assert failure.profile_snapshot == {"age": 42}
        assert failure.submitted_at == submitted_at
        assert failure.started_at == started_at
        assert failure.failure_message == "输入无效"
        assert failure.failure_detail == {"reason": "invalid"}
        assert failure.finished_at is not None
        assert failure.finished_at == finished_at
        assert failure.execution_token is None
        assert failure.lease_expires_at is None
        assert failure.attempt_count == 0
        assert running.started_at == started_at
        assert running.profile_snapshot == {"age": 42}
        assert running.execution_token is not None
        assert running.lease_expires_at <= timezone.now()
        assert running.attempt_count == 0
        assert running.status == "RUNNING"
    finally:
        MigrationExecutor(connection).migrate([MIGRATION_WITH_EXECUTION_FIELDS])
