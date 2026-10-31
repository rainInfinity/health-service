"""验证渠道固定版本草稿、任务分页和安全详情投影。"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.task_exceptions import (
    InvalidTaskPagination,
    TaskNotFound,
)
from health_service.application.tasks import create_task_draft, list_user_tasks, read_user_task
from health_service.models import Channel, Task

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


def _create_selected_test_algorithm(*, channel: str = "ios-cn", code: str = "pulse"):
    """创建并为普通渠道选择可执行的测试算法版本。"""
    model = create_model(code=code, name="Pulse", description=None)
    version = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )
    published = publish_model_version(version_id=version.version_id)
    select_channel_model_version(
        channel=channel,
        model_id=model.model_id,
        version_id=published.version_id,
    )
    return model, published


@pytest.mark.django_db
def test_draft_requires_channel_selection_and_fixes_current_published_version():
    """没有当前选择时拒绝创建；有选择时固定已发布版本且不冻结资料。"""
    user = get_user_model().objects.create_user(
        username="draft-owner",
        password="Willow-lake-247!strong",
    )
    model = create_model(code="unselected", name="Unselected", description=None)
    with pytest.raises(TaskNotFound):
        create_task_draft(
            user_id=user.pk,
            channel="ios-cn",
            model_id=model.model_id,
        )
    assert not Task.objects.exists()

    selected_model, version = _create_selected_test_algorithm()
    task = create_task_draft(
        user_id=user.pk,
        channel="ios-cn",
        model_id=selected_model.model_id,
    )
    record = Task.objects.get(pk=task.task_id)
    assert (task.model_id, task.version_id, task.status, task.is_example) == (
        selected_model.model_id,
        version.version_id,
        Task.StatusChoices.DRAFT,
        False,
    )
    assert record.model_version_id == version.version_id
    assert record.submitted_at is None
    assert record.profile_snapshot is None


@pytest.mark.django_db
def test_task_list_uses_stable_bounded_order_without_loading_large_payloads():
    """摘要分页按创建时间和 ID 倒序，查询不载入快照或完整结果。"""
    user = get_user_model().objects.create_user(
        username="task-page-owner",
        password="Willow-lake-347!strong",
    )
    model, _ = _create_selected_test_algorithm()
    tasks = tuple(
        create_task_draft(
            user_id=user.pk,
            channel="ios-cn",
            model_id=model.model_id,
        )
        for _ in range(3)
    )
    same_time = timezone.now()
    Task.objects.filter(pk__in=tuple(task.task_id for task in tasks)).update(
        created_at=same_time
    )

    with CaptureQueriesContext(connection) as queries:
        total, page = list_user_tasks(
            user_id=user.pk,
            channel="ios-cn",
            offset=0,
            limit=2,
        )
    assert sum(query["sql"].lstrip().upper().startswith("SELECT") for query in queries) == 2
    page_sql = queries[-1]["sql"].lower()
    assert "result_payload" not in page_sql
    assert "profile_snapshot" not in page_sql
    assert "failure_detail" not in page_sql
    assert total == 3
    assert tuple(item.task_id for item in page) == tuple(
        sorted((task.task_id for task in tasks), reverse=True)[:2]
    )
    assert all(item.is_example is False for item in page)

    with pytest.raises(InvalidTaskPagination):
        list_user_tasks(user_id=user.pk, channel="ios-cn", offset=-1, limit=20)
    with pytest.raises(InvalidTaskPagination):
        list_user_tasks(user_id=user.pk, channel="ios-cn", offset=0, limit=101)


@pytest.mark.django_db
def test_task_detail_requires_matching_owner_and_source_channel():
    """同账号跨渠道和其他账号访问均返回相同任务不存在异常。"""
    owner = get_user_model().objects.create_user(
        username="task-detail-owner",
        password="Birch-ridge-648!strong",
    )
    other = get_user_model().objects.create_user(
        username="task-detail-other",
        password="Birch-ridge-748!strong",
    )
    model, _ = _create_selected_test_algorithm()
    task = create_task_draft(
        user_id=owner.pk,
        channel="ios-cn",
        model_id=model.model_id,
    )

    for user_id, channel in ((other.pk, "ios-cn"), (owner.pk, "wxapp")):
        with pytest.raises(TaskNotFound):
            read_user_task(task_id=task.task_id, user_id=user_id, channel=channel)

    own_task = read_user_task(
        task_id=task.task_id,
        user_id=owner.pk,
        channel="ios-cn",
    )
    assert own_task.task_id == task.task_id
    assert own_task.result_payload is None
    assert own_task.failure_code is None


@pytest.mark.django_db
def test_task_detail_projects_success_result_and_only_safe_failure_summary():
    """详情返回完整成功结果或安全失败摘要，不读取诊断明细。"""
    user = get_user_model().objects.create_user(
        username="task-detail-results",
        password="Birch-ridge-848!strong",
    )
    _model, version = _create_selected_test_algorithm(code="task-results")
    channel = Channel.objects.get(label="ios-cn")
    finished_at = timezone.now()
    success = Task.objects.create(
        user=user,
        channel=channel,
        model_version_id=version.version_id,
        status=Task.StatusChoices.SUCCEEDED,
        result_payload={"completed": True},
        finished_at=finished_at,
    )
    failed = Task.objects.create(
        user=user,
        channel=channel,
        model_version_id=version.version_id,
        status=Task.StatusChoices.FAILED,
        finished_at=finished_at,
        failure_code="INFERENCE.INVALID_INPUT",
        failure_message="输入格式无效",
        failure_detail={"path": "/private/health.csv", "raw": "sensitive"},
    )

    successful_detail = read_user_task(
        task_id=success.pk,
        user_id=user.pk,
        channel="ios-cn",
    )
    failed_detail = read_user_task(
        task_id=failed.pk,
        user_id=user.pk,
        channel="ios-cn",
    )
    assert successful_detail.result_payload == {"completed": True}
    assert successful_detail.failure_code is None
    assert failed_detail.result_payload is None
    assert failed_detail.failure_code == "INFERENCE.INVALID_INPUT"
    assert failed_detail.failure_message == "输入格式无效"
    assert not hasattr(failed_detail, "failure_detail")
