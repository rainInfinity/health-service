"""验证草稿创建与渠道版本切换共享稳定的渠道锁顺序。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.tasks import create_task_draft
from health_service.models import ChannelModelSelection, Task

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


@pytest.mark.django_db(transaction=True)
def test_concurrent_draft_creation_cannot_escape_model_switch():
    """并发创建要么在切换前被终止，要么固定新版本，不遗留旧草稿。"""
    model = create_model(code="race-model", name="Race model", description=None)
    old_draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    old_version = publish_model_version(version_id=old_draft.version_id)
    next_draft = create_model_version(
        model_id=model.model_id,
        version="2.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    next_version = publish_model_version(version_id=next_draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=old_version.version_id,
    )
    user = get_user_model().objects.create_user(
        username="task-selection-race-owner",
        password="Maple-grove-359!strong",
    )
    barrier = Barrier(2)

    def create_concurrently() -> int:
        """在独立 PostgreSQL 连接上尝试创建固定版本草稿。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            task = create_task_draft(
                user_id=user.pk,
                channel="ios-cn",
                model_id=model.model_id,
            )
            return task.task_id
        finally:
            connections.close_all()

    def switch_concurrently() -> None:
        """在独立 PostgreSQL 连接上切换当前发布版本。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            select_channel_model_version(
                channel="ios-cn",
                model_id=model.model_id,
                version_id=next_version.version_id,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(create_concurrently)
        switch_future = executor.submit(switch_concurrently)
        task_id = create_future.result(timeout=20)
        switch_future.result(timeout=20)

    task = Task.objects.get(pk=task_id)
    selection = ChannelModelSelection.objects.get(
        channel__label="ios-cn",
        model_id=model.model_id,
    )
    assert selection.current_version_id == next_version.version_id
    if task.model_version_id == old_version.version_id:
        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "TASK.MODEL_SWITCHED"
    else:
        assert task.model_version_id == next_version.version_id
        assert task.status == Task.StatusChoices.DRAFT
    assert not Task.objects.filter(
        channel__label="ios-cn",
        model_version_id=old_version.version_id,
        status=Task.StatusChoices.DRAFT,
    ).exists()
