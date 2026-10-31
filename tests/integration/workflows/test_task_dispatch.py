"""验证 Redis Streams 派发内容、数据库补记与安全重放。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, transaction
from django.test import override_settings
from django.utils import timezone
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.dispatch import dispatch_pending_tasks, dispatch_task
from health_service.application.tasks import (
    create_task_draft,
    submit_and_dispatch_user_task,
    upload_task_input,
)
from health_service.models import Channel, Task
from redis import Redis

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


def _create_submitted_tasks(count: int) -> list[Task]:
    """为同一已发布版本批量创建待派发任务。"""
    user = get_user_model().objects.create_user(
        username=f"dispatch-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"dispatch-{user.pk}",
        name="Dispatch",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    channel = Channel.objects.get(label="ios-cn")
    now = timezone.now()
    return Task.objects.bulk_create(
        [
            Task(
                user=user,
                channel=channel,
                model_version_id=version.version_id,
                status=Task.StatusChoices.SUBMITTED,
                profile_snapshot={},
                submitted_at=now,
                created_at=now,
                updated_at=now,
            )
            for _ in range(count)
        ]
    )


def _create_submitted_task() -> Task:
    """创建带已发布固定版本的数据库已接受任务。"""
    return _create_submitted_tasks(1)[0]


def _redis_client() -> Redis:
    """创建用于检查隔离测试 Stream 的 Redis 客户端。"""
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


@pytest.mark.django_db(transaction=True)
def test_dispatch_publishes_only_task_id_and_records_database_time():
    """真实 Redis 收到唯一任务字段，数据库记录成功发布时刻。"""
    task = _create_submitted_task()
    stream = f"test:health:dispatch:{uuid4().hex}"
    client = _redis_client()
    try:
        with override_settings(INFERENCE_STREAM=stream):
            assert dispatch_task(task.pk) is True
        entries = client.xrange(stream)
        assert len(entries) == 1
        assert entries[0][1] == {"task_id": str(task.pk)}
        task.refresh_from_db()
        assert task.status == Task.StatusChoices.SUBMITTED
        assert task.last_dispatched_at is not None
        assert task.updated_by == "system:dispatcher"
    finally:
        client.delete(stream)
        client.close()


@pytest.mark.django_db(transaction=True)
def test_marker_database_failure_keeps_published_message_safe_to_replay(monkeypatch):
    """成功发布后补记数据库失败不撤回任务，重复消息仍安全可发。"""
    task = _create_submitted_task()
    stream = f"test:health:dispatch-marker:{uuid4().hex}"
    client = _redis_client()

    def fail_marker(*, task_id: int) -> bool:
        """模拟 Redis 已成功后派发时间更新遇到数据库故障。"""
        raise OperationalError("temporary marker failure")

    try:
        with override_settings(INFERENCE_STREAM=stream):
            monkeypatch.setattr(
                "health_service.application.dispatch.persistence.mark_task_dispatched",
                fail_marker,
            )
            assert dispatch_task(task.pk) is True
            task.refresh_from_db()
            assert task.status == Task.StatusChoices.SUBMITTED
            assert task.last_dispatched_at is None

            monkeypatch.undo()
            assert dispatch_task(task.pk) is True
        entries = client.xrange(stream)
        assert len(entries) == 2
        assert all(fields == {"task_id": str(task.pk)} for _, fields in entries)
        task.refresh_from_db()
        assert task.last_dispatched_at is not None
    finally:
        client.delete(stream)
        client.close()


@pytest.mark.django_db(transaction=True)
def test_redis_connection_failure_preserves_submission_and_dispatch_eligibility():
    """Redis 恢复后补发扫描重新发布仍未派发的已提交任务。"""
    task = _create_submitted_task()
    stream = f"test:health:dispatch-failure:{uuid4().hex}"
    client = _redis_client()
    try:
        with override_settings(
            REDIS_URL="redis://127.0.0.1:1/0",
            INFERENCE_STREAM=stream,
        ):
            assert dispatch_task(task.pk) is False
            assert dispatch_pending_tasks() == 0

        task.refresh_from_db()
        assert task.status == Task.StatusChoices.SUBMITTED
        assert task.last_dispatched_at is None
        assert task.attempt_count == 0

        with override_settings(INFERENCE_STREAM=stream):
            assert dispatch_pending_tasks() == 1

        entries = client.xrange(stream)
        assert len(entries) == 1
        assert entries[0][1] == {"task_id": str(task.pk)}
        task.refresh_from_db()
        assert task.last_dispatched_at is not None
        assert task.attempt_count == 0
    finally:
        client.delete(stream)
        client.close()




@pytest.mark.django_db(transaction=True)
def test_dispatch_scan_is_bounded_and_uses_stable_task_order():
    """真实 Redis 验证扫描逐批前进且不重复已补记任务。"""
    tasks = _create_submitted_tasks(101)
    stream = f"test:health:dispatch-scan:{uuid4().hex}"
    client = _redis_client()
    try:
        with override_settings(INFERENCE_STREAM=stream):
            assert dispatch_pending_tasks() == 100
            first_batch = client.xrange(stream)
            assert [fields["task_id"] for _, fields in first_batch] == [
                str(task.pk) for task in tasks[:100]
            ]

            assert dispatch_pending_tasks() == 1
            assert dispatch_pending_tasks() == 0

        entries = client.xrange(stream)
        assert len(entries) == 101
        assert [fields["task_id"] for _, fields in entries] == [
            str(task.pk) for task in tasks
        ]
    finally:
        client.delete(stream)
        client.close()


@pytest.mark.django_db(transaction=True)
def test_dispatch_scan_recovers_expired_running_without_stream_entry():
    """原消息条目缺席时，扫描仍重新发布过期运行任务。"""
    expired_task, active_task = _create_submitted_tasks(2)
    now = timezone.now()
    expired_task.status = Task.StatusChoices.RUNNING
    expired_task.execution_token = uuid4()
    expired_task.lease_expires_at = now - timedelta(seconds=1)
    expired_task.attempt_count = 1
    expired_task.started_at = now - timedelta(seconds=30)
    expired_task.save(
        update_fields=[
            "status",
            "execution_token",
            "lease_expires_at",
            "attempt_count",
            "started_at",
        ]
    )
    active_task.status = Task.StatusChoices.RUNNING
    active_task.execution_token = uuid4()
    active_task.lease_expires_at = now + timedelta(minutes=1)
    active_task.attempt_count = 1
    active_task.started_at = now
    active_task.save(
        update_fields=[
            "status",
            "execution_token",
            "lease_expires_at",
            "attempt_count",
            "started_at",
        ]
    )
    stream = f"test:health:dispatch-expired:{uuid4().hex}"
    client = _redis_client()
    try:
        assert client.exists(stream) == 0
        with override_settings(INFERENCE_STREAM=stream):
            assert dispatch_pending_tasks() == 1

        entries = client.xrange(stream)
        assert len(entries) == 1
        assert entries[0][1] == {"task_id": str(expired_task.pk)}
        expired_task.refresh_from_db()
        active_task.refresh_from_db()
        assert expired_task.status == Task.StatusChoices.RUNNING
        assert expired_task.last_dispatched_at is not None
        assert active_task.last_dispatched_at is None
    finally:
        client.delete(stream)
        client.close()


@pytest.mark.django_db(transaction=True)
def test_submit_dispatch_is_deferred_until_outer_database_commit(tmp_path):
    """外层事务提交前不发消息，提交后才向真实 Redis 发送。"""
    user = get_user_model().objects.create_user(
        username=f"dispatch-submit-{uuid4().hex}",
        password="Dawn-valley-583!strong",
    )
    model = create_model(
        code=f"dispatch-submit-{user.pk}",
        name="Dispatch submit",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    task = create_task_draft(
        user_id=user.pk,
        channel="ios-cn",
        model_id=model.model_id,
    )
    stream = f"test:health:submit-commit:{uuid4().hex}"
    client = _redis_client()
    try:
        with override_settings(
            INFERENCE_STREAM=stream,
            TASK_DATA_ROOT=tmp_path / "tasks",
            MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
            MAX_UPLOAD_BYTES=1024,
        ):
            upload_task_input(
                task_id=task.task_id,
                user_id=user.pk,
                channel="ios-cn",
                logical_name="ppg",
                source=SimpleUploadedFile(
                    "ppg.csv",
                    b"timestampFrame,avgR\\n1,2\\n",
                    content_type="text/csv",
                ),
            )
            with transaction.atomic():
                submitted = submit_and_dispatch_user_task(
                    task_id=task.task_id,
                    user_id=user.pk,
                    channel="ios-cn",
                )
                assert submitted.status == Task.StatusChoices.SUBMITTED
                assert client.xrange(stream) == []
            entries = client.xrange(stream)
            assert len(entries) == 1
            assert entries[0][1] == {"task_id": str(task.task_id)}
            persisted = Task.objects.get(pk=task.task_id)
            assert persisted.status == Task.StatusChoices.SUBMITTED
            assert persisted.last_dispatched_at is not None
    finally:
        client.delete(stream)
        client.close()
