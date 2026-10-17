"""验证 PostgreSQL 任务执行租约的并发、到期与续约边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.db.models.functions import Now
from django.test import override_settings
from django.utils import timezone
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.execution import (
    claim_task_for_execution,
    renew_task_execution_lease,
)
from health_service.models import Channel, Task


def _create_submitted_task() -> Task:
    """创建指向已发布示例版本的待执行任务。"""
    user = get_user_model().objects.create_user(
        username=f"execution-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    model = create_model(
        code=f"execution-{user.pk}",
        name="Execution",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="hr_br.example",
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    return Task.objects.create(
        user=user,
        channel=Channel.objects.get(label="ios-cn"),
        model_version_id=version.version_id,
        status=Task.StatusChoices.SUBMITTED,
        profile_snapshot={},
        submitted_at=timezone.now(),
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_messages_grant_only_one_execution_lease():
    """两个并发重复领取至多增加一次执行次数并返回一个当前令牌。"""
    task = _create_submitted_task()
    barrier = Barrier(2)

    def claim_concurrently() -> UUID | None:
        """在独立数据库连接上尝试领取同一任务。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return claim_task_for_execution(task_id=task.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim_concurrently) for _ in range(2)]
        tokens = [future.result(timeout=20) for future in futures]

    claimed_tokens = [token for token in tokens if token is not None]
    assert len(claimed_tokens) == 1
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.execution_token == claimed_tokens[0]
    assert task.lease_expires_at is not None
    assert task.attempt_count == 1
    assert task.started_at is not None
    assert task.updated_by == "system:worker"
    assert claim_task_for_execution(task_id=task.pk) is None
    task.refresh_from_db()
    assert task.attempt_count == 1


@pytest.mark.django_db(transaction=True)
def test_expired_lease_can_be_reclaimed_but_late_heartbeat_cannot_revive_it():
    """过期执行可换新令牌重领，旧令牌心跳不能复活或改写首次开始时间。"""
    task = _create_submitted_task()
    first_token = claim_task_for_execution(task_id=task.pk)
    assert first_token is not None
    task.refresh_from_db()
    first_started_at = task.started_at
    assert first_started_at is not None

    Task.objects.filter(pk=task.pk).update(
        lease_expires_at=Now() - timedelta(seconds=1)
    )
    assert not Task.objects.filter(
        pk=task.pk,
        lease_expires_at__gt=Now(),
    ).exists()
    assert renew_task_execution_lease(
        task_id=task.pk,
        execution_token=first_token,
    ) is False

    second_token = claim_task_for_execution(task_id=task.pk)
    assert second_token is not None
    assert second_token != first_token
    task.refresh_from_db()
    assert task.execution_token == second_token
    assert task.attempt_count == 2
    assert task.started_at == first_started_at
    assert Task.objects.filter(
        pk=task.pk,
        lease_expires_at__gt=Now(),
    ).exists()
    assert renew_task_execution_lease(
        task_id=task.pk,
        execution_token=first_token,
    ) is False
    assert renew_task_execution_lease(
        task_id=task.pk,
        execution_token=second_token,
    ) is True
    task.refresh_from_db()
    assert task.execution_token == second_token
    assert task.attempt_count == 2
    assert task.started_at == first_started_at


@pytest.mark.django_db(transaction=True)
def test_expired_task_at_attempt_limit_fails_with_safe_retry_code():
    """最后一次执行租约过期后安全终结且不再增加执行次数。"""
    task = _create_submitted_task()
    with override_settings(INFERENCE_MAX_ATTEMPTS=2):
        first_token = claim_task_for_execution(task_id=task.pk)
        assert first_token is not None
        Task.objects.filter(pk=task.pk).update(
            lease_expires_at=Now() - timedelta(seconds=1)
        )
        second_token = claim_task_for_execution(task_id=task.pk)
        assert second_token is not None
        assert second_token != first_token
        Task.objects.filter(pk=task.pk).update(
            lease_expires_at=Now() - timedelta(seconds=1)
        )

        assert claim_task_for_execution(task_id=task.pk) is None

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "TASK.RETRY_EXHAUSTED"
    assert task.failure_message == "任务执行次数已耗尽，无法继续重试。"
    assert task.failure_detail is None
    assert task.finished_at is not None
    assert task.result_payload is None
    assert task.execution_token is None
    assert task.lease_expires_at is None
    assert task.attempt_count == 2
    assert task.started_at is not None
    assert task.updated_by == "system:worker"
    assert claim_task_for_execution(task_id=task.pk) is None
