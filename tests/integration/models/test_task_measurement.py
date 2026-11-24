"""验证冻结采集属性的真实数据库完整性与历史升级。"""

from itertools import product

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from health_service.models import Channel, Model, ModelVersion, Task

BEFORE_MEASUREMENT = ("health_service", "0010_task_client_metadata")
WITH_MEASUREMENT = ("health_service", "0011_task_measurement")


@pytest.fixture
def measurement_task_relationships():
    """建立约束测试使用的最小任务归属与版本。"""
    user = get_user_model().objects.create_user(username="measurement-constraint-owner")
    channel = Channel.objects.create(label="measurement-constraint")
    model = Model.objects.create(code="measurement-constraint", name="采集约束")
    version = ModelVersion.objects.create(
        model=model, version="1.0", algorithm_key="constraint.example"
    )
    return {"user": user, "channel": channel, "model_version": version}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "mode", "duration"),
    [
        values
        for values in product((None, "fingertip"), (None, "active"), (None, 1))
        if any(value is None for value in values) and any(value is not None for value in values)
    ]
    + [
        ("unknown", "active", 1),
        ("face", "active", 1),
        ("fingertip", "passive", 1),
        ("", "active", 1),
        ("fingertip", "", 1),
        ("fingertip", "active", 0),
        ("fingertip", "active", -1),
    ],
)
def test_measurement_database_rejects_partial_or_invalid_attributes(
    measurement_task_relationships, method, mode, duration
):
    """显式非空检查阻止所有六种部分赋值绕过 SQL 三值逻辑。"""
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.create(
            **measurement_task_relationships,
            status="SUBMITTED",
            capture_method=method,
            capture_mode=mode,
            duration_ms=duration,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["fingertip", "fingertip_chest"])
@pytest.mark.parametrize("duration", [1, 2**63 - 1])
def test_measurement_database_accepts_complete_positive_int64_attributes(
    measurement_task_relationships, method, duration
):
    """合法两种方式和时长两端都可按原值持久化。"""
    task = Task.objects.create(
        **measurement_task_relationships,
        status="SUBMITTED",
        capture_method=method,
        capture_mode="active",
        duration_ms=duration,
    )
    task.refresh_from_db()
    assert (task.capture_method, task.capture_mode, task.duration_ms) == (
        method, "active", duration
    )


@pytest.mark.django_db
def test_draft_requires_unknown_measurement(measurement_task_relationships):
    """草稿允许三项全空但不能提前冻结完整属性。"""
    draft = Task.objects.create(**measurement_task_relationships)
    assert (draft.capture_method, draft.capture_mode, draft.duration_ms) == (None, None, None)
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.filter(pk=draft.pk).update(
            capture_method="fingertip", capture_mode="active", duration_ms=1
        )


@pytest.mark.django_db(transaction=True)
def test_measurement_migration_preserves_history_results_and_optional_projections():
    """升级仅加空属性，不补造结果或投影，并在结束时恢复最新结构。"""
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE_MEASUREMENT])
        apps = executor.loader.project_state([BEFORE_MEASUREMENT]).apps
        now = timezone.now()
        audit = {"created_at": now, "updated_at": now, "created_by": "legacy:user"}
        user = apps.get_model("health_service", "User").objects.create(
            username="measurement-migration-owner", password="unused", **audit
        )
        channel = apps.get_model("health_service", "Channel").objects.create(
            label="measurement-migration", **audit
        )
        model = apps.get_model("health_service", "Model").objects.create(
            code="measurement-migration", name="迁移测试", **audit
        )
        version = apps.get_model("health_service", "ModelVersion").objects.create(
            model=model,
            version="1.0",
            algorithm_key="ppg.active.offline.hr.v1",
            published_at=now,
            **audit,
        )
        metric = apps.get_model("health_service", "MetricDefinition").objects.create(
            code="migration.hr", value_type="number", unit="beats_per_minute", **audit
        )
        apps.get_model("health_service", "ModelVersionMetric").objects.create(
            model_version=version, metric=metric, **audit
        )
        task_model = apps.get_model("health_service", "Task")
        projection_model = apps.get_model("health_service", "TaskMetric")
        input_model = apps.get_model("health_service", "TaskInputFile")
        for with_projection in (False, True):
            task = task_model.objects.create(
                user=user,
                channel=channel,
                model_version=version,
                status="SUCCEEDED",
                profile_snapshot={"age_years": 31},
                client_metadata={"private": "kept"},
                submitted_at=now,
                started_at=now,
                finished_at=now,
                result_payload={"hr": 73.125, "private": {"raw": [1, 2]}},
                **audit,
            )
            input_model.objects.create(
                task=task,
                logical_name="ppg",
                storage_key=f"legacy/{task.pk}/ppg.csv",
                size_bytes=12,
                uploaded_at=now,
                is_selected=True,
                **audit,
            )
            if with_projection:
                projection_model.objects.create(
                    task=task, metric=metric, numeric_value=73.125, **audit
                )
        old_tasks = list(task_model.objects.order_by("pk").values())
        old_projections = list(projection_model.objects.order_by("pk").values())
        old_inputs = list(input_model.objects.order_by("pk").values())
        old_versions = list(apps.get_model("health_service", "ModelVersion").objects.values())
        old_selections = list(apps.get_model("health_service", "ModelVersionMetric").objects.values())

        executor = MigrationExecutor(connection)
        executor.migrate([WITH_MEASUREMENT])
        new_apps = executor.loader.project_state([WITH_MEASUREMENT]).apps
        new_tasks = list(new_apps.get_model("health_service", "Task").objects.order_by("pk").values())
        for old_task, new_task in zip(old_tasks, new_tasks, strict=True):
            assert new_task.pop("capture_method") is None
            assert new_task.pop("capture_mode") is None
            assert new_task.pop("duration_ms") is None
            assert new_task == old_task
        assert list(new_apps.get_model("health_service", "TaskMetric").objects.order_by("pk").values()) == old_projections
        assert list(new_apps.get_model("health_service", "TaskInputFile").objects.order_by("pk").values()) == old_inputs
        assert list(new_apps.get_model("health_service", "ModelVersion").objects.values()) == old_versions
        assert list(new_apps.get_model("health_service", "ModelVersionMetric").objects.values()) == old_selections
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "health_task")
        assert constraints["htask_user_chan_submit_idx"]["columns"] == [
            "user_id", "channel_id", "submitted_at", "id"
        ]
        assert constraints["health_task_measurement_valid"]["check"]
        assert constraints["health_task_draft_measurement_null"]["check"]
    finally:
        MigrationExecutor(connection).migrate(latest)
