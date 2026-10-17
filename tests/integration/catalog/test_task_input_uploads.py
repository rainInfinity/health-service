"""验证任务输入完整登记、历史保留与草稿行锁并发。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connections
from django.test import override_settings
from django.utils import timezone
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.task_exceptions import (
    TaskConflict,
    TaskInputInvalid,
    TaskNotFound,
)
from health_service.application.tasks import create_task_draft, upload_task_input
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.models import Task, TaskInputFile


def _create_draft():
    """创建拥有 ppg 输入声明的当前渠道草稿。"""
    user = get_user_model().objects.create_user(
        username=f"upload-owner-{timezone.now().timestamp()}",
        password="Cobalt-river-582!strong",
    )
    model = create_model(code=f"upload-{user.pk}", name="Upload", description=None)
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
    task = create_task_draft(
        user_id=user.pk,
        channel="ios-cn",
        model_id=model.model_id,
    )
    return user, task


def _uploaded_file(content: bytes) -> SimpleUploadedFile:
    """创建带客户端文件名的实际上传流。"""
    return SimpleUploadedFile("ppg.csv", content, content_type="text/csv")


@pytest.mark.django_db
def test_reupload_selects_complete_file_and_preserves_historical_record(tmp_path):
    """同名重传只切换选中记录，旧数据库记录和文件继续可读。"""
    user, task = _create_draft()
    storage = LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts")
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=100,
    ):
        first = upload_task_input(
            task_id=task.task_id,
            user_id=user.pk,
            channel="ios-cn",
            logical_name="ppg",
            source=_uploaded_file(b"timestampFrame,avgR\n1,2\n"),
        )
        second = upload_task_input(
            task_id=task.task_id,
            user_id=user.pk,
            channel="ios-cn",
            logical_name="ppg",
            source=_uploaded_file(b"timestampFrame,avgR\n2,3\n"),
        )

    records = tuple(TaskInputFile.objects.filter(task_id=task.task_id).order_by("pk"))
    assert len(records) == 2
    assert [(item.pk, item.is_selected) for item in records] == [
        (first.file_id, False),
        (second.file_id, True),
    ]
    assert storage.resolve_file(StorageArea.TASK_DATA, records[0].storage_key).read_bytes() == (
        b"timestampFrame,avgR\n1,2\n"
    )
    assert storage.resolve_file(StorageArea.TASK_DATA, records[1].storage_key).read_bytes() == (
        b"timestampFrame,avgR\n2,3\n"
    )
    with pytest.raises(TaskInputInvalid):
        upload_task_input(
            task_id=task.task_id,
            user_id=user.pk,
            channel="ios-cn",
            logical_name="undeclared",
            source=_uploaded_file(b"payload"),
        )
    assert TaskInputFile.objects.filter(task_id=task.task_id).count() == 2


@pytest.mark.parametrize("status", [Task.StatusChoices.SUBMITTED, Task.StatusChoices.FAILED])
@pytest.mark.django_db
def test_non_draft_and_wrong_owner_uploads_are_rejected_without_registration(
    tmp_path,
    status,
):
    """非草稿状态与其他账号上传均拒绝且不登记输入。"""
    owner, task = _create_draft()
    other = get_user_model().objects.create_user(
        username=f"upload-other-{owner.pk}",
        password="Cobalt-river-682!strong",
    )
    if status == Task.StatusChoices.SUBMITTED:
        Task.objects.filter(pk=task.task_id).update(
            status=status,
            submitted_at=timezone.now(),
        )
    else:
        Task.objects.filter(pk=task.task_id).update(
            status=status,
            finished_at=timezone.now(),
            failure_code="TASK.TEST_FAILURE",
        )

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=100,
    ):
        with pytest.raises(TaskConflict):
            upload_task_input(
                task_id=task.task_id,
                user_id=owner.pk,
                channel="ios-cn",
                logical_name="ppg",
                source=_uploaded_file(b"submitted"),
            )
        with pytest.raises(TaskNotFound):
            upload_task_input(
                task_id=task.task_id,
                user_id=other.pk,
                channel="ios-cn",
                logical_name="ppg",
                source=_uploaded_file(b"unauthorized"),
            )

    assert not TaskInputFile.objects.filter(task_id=task.task_id).exists()
    assert not (tmp_path / "tasks" / "uploads").exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_name_uploads_leave_one_selected_file(tmp_path):
    """两个并发同名上传都保留完整文件，最终恰有一个当前选择。"""
    user, task = _create_draft()
    barrier = Barrier(2)

    def upload_concurrently(content: bytes) -> int:
        """在独立 PostgreSQL 连接上上传同名文件。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result = upload_task_input(
                task_id=task.task_id,
                user_id=user.pk,
                channel="ios-cn",
                logical_name="ppg",
                source=_uploaded_file(content),
            )
            return result.file_id
        finally:
            connections.close_all()

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=100,
    ), ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(upload_concurrently, b"first-content"),
            executor.submit(upload_concurrently, b"second-content"),
        )
        uploaded_ids = tuple(future.result(timeout=20) for future in futures)

    records = tuple(TaskInputFile.objects.filter(task_id=task.task_id).order_by("pk"))
    assert {item.pk for item in records} == set(uploaded_ids)
    assert sum(item.is_selected for item in records) == 1
    stored_contents = {
        LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts")
        .resolve_file(StorageArea.TASK_DATA, item.storage_key)
        .read_bytes()
        for item in records
    }
    assert stored_contents == {b"first-content", b"second-content"}
