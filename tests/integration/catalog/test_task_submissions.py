"""验证最终提交的文件重检、资料快照与锁顺序。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
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
from health_service.application.profiles import update_profile
from health_service.application.task_exceptions import TaskConflict, TaskInputInvalid
from health_service.application.tasks import (
    create_task_draft,
    submit_user_task,
    upload_task_input,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.models import ChannelModelSelection, Profile, Task, TaskInputFile


def _create_draft(*, username: str):
    """创建当前选择已发布示例版本的普通渠道草稿。"""
    user = get_user_model().objects.create_user(
        username=username,
        password="Cobalt-river-782!strong",
    )
    model = create_model(code=f"submit-{username}", name="Submission", description=None)
    version_draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="hr_br.example",
        params=None,
    )
    version = publish_model_version(version_id=version_draft.version_id)
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
    return user, model, version, task


def _upload_ppg(*, user_id: int, task_id: int):
    """登记一份完整 ppg 输入并切为当前选择。"""
    return upload_task_input(
        task_id=task_id,
        user_id=user_id,
        channel="ios-cn",
        logical_name="ppg",
        source=SimpleUploadedFile(
            "client.csv",
            b"timestampFrame,avgR\n1,2\n",
            content_type="text/csv",
        ),
    )


def _close_worker_database_connection() -> None:
    """关闭并发测试线程持有的数据库连接。"""
    connections.close_all()


@pytest.mark.django_db
def test_submission_rechecks_files_and_freezes_complete_profile(tmp_path):
    """提交重检实际输入并冻结单位、未知病史与提交日年龄。"""
    user, _model, _version, task = _create_draft(username="submit-profile-owner")
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        uploaded = _upload_ppg(user_id=user.pk, task_id=task.task_id)
        update_profile(
            user.pk,
            {
                "name": "测试用户",
                "gender": 2,
                "birth_date": date(1990, 1, 1),
                "height_cm": Decimal("168.50"),
                "weight_kg": Decimal("61.25"),
                "medical_history": "既往情况",
                "history_json": {"is_diabetic": False},
            },
        )
        submitted = submit_user_task(
            task_id=task.task_id,
            user_id=user.pk,
            channel="ios-cn",
        )

    record = Task.objects.get(pk=task.task_id)
    snapshot = record.profile_snapshot
    assert submitted.status == Task.StatusChoices.SUBMITTED
    assert record.submitted_at is not None
    local_submission_date = timezone.localdate(record.submitted_at)
    expected_age = local_submission_date.year - 1990
    assert record.updated_by == f"user:{user.pk}"
    assert snapshot == {
        "name": "测试用户",
        "gender": 2,
        "birth_date": "1990-01-01",
        "age_years": expected_age,
        "height_cm": "168.50",
        "weight_kg": "61.25",
        "medical_history": "既往情况",
        "history_json": {"is_diabetic": False},
    }
    assert "is_hypertensive" not in snapshot["history_json"]
    assert Profile.objects.filter(user_id=user.pk).exists()
    assert TaskInputFile.objects.get(pk=uploaded.file_id).is_selected is True

    update_profile(
        user.pk,
        {
            "name": "后续修改",
            "birth_date": date(2000, 1, 1),
            "history_json": {"is_diabetic": True, "is_hypertensive": True},
        },
    )
    record.refresh_from_db()
    assert record.profile_snapshot == snapshot


@pytest.mark.django_db
def test_submission_accepts_empty_profile_but_rejects_missing_or_unreadable_input(
    tmp_path,
):
    """资料缺席时提交保留未知值；缺失或已丢失文件不冻结快照。"""
    empty_user, _model, _version, empty_task = _create_draft(
        username="submit-empty-profile"
    )
    missing_user, _model, _version, missing_task = _create_draft(
        username="submit-missing-input"
    )
    unreadable_user, _model, _version, unreadable_task = _create_draft(
        username="submit-unreadable-input"
    )
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        _upload_ppg(user_id=empty_user.pk, task_id=empty_task.task_id)
        unreadable = _upload_ppg(
            user_id=unreadable_user.pk,
            task_id=unreadable_task.task_id,
        )
        storage = LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts")
        storage.resolve_file(
            StorageArea.TASK_DATA,
            TaskInputFile.objects.get(pk=unreadable.file_id).storage_key,
        ).unlink()

        with pytest.raises(TaskInputInvalid):
            submit_user_task(
                task_id=missing_task.task_id,
                user_id=missing_user.pk,
                channel="ios-cn",
            )
        with pytest.raises(TaskInputInvalid):
            submit_user_task(
                task_id=unreadable_task.task_id,
                user_id=unreadable_user.pk,
                channel="ios-cn",
            )
        submit_user_task(
            task_id=empty_task.task_id,
            user_id=empty_user.pk,
            channel="ios-cn",
        )

    assert not Profile.objects.filter(user_id=empty_user.pk).exists()
    empty_record = Task.objects.get(pk=empty_task.task_id)
    assert empty_record.status == Task.StatusChoices.SUBMITTED
    assert empty_record.profile_snapshot == {
        "name": None,
        "gender": 0,
        "birth_date": None,
        "age_years": None,
        "height_cm": None,
        "weight_kg": None,
        "medical_history": None,
        "history_json": None,
    }
    for task_id in (missing_task.task_id, unreadable_task.task_id):
        record = Task.objects.get(pk=task_id)
        assert record.status == Task.StatusChoices.DRAFT
        assert record.submitted_at is None
        assert record.profile_snapshot is None


@pytest.mark.django_db(transaction=True)
def test_submission_and_final_upload_are_serialized_by_task_lock(tmp_path):
    """并发最终上传与提交只接受已完整登记的输入集合。"""
    user, _model, _version, task = _create_draft(username="submit-upload-race")
    barrier = Barrier(2)

    def upload_concurrently() -> str:
        """在独立数据库连接上完成最后一个输入上传。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            _upload_ppg(user_id=user.pk, task_id=task.task_id)
            return "uploaded"
        finally:
            _close_worker_database_connection()

    def submit_concurrently() -> str:
        """在独立数据库连接上尝试冻结任务输入。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                submit_user_task(
                    task_id=task.task_id,
                    user_id=user.pk,
                    channel="ios-cn",
                )
            except TaskInputInvalid:
                return "input-not-yet-registered"
            return "submitted"
        finally:
            _close_worker_database_connection()

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ), ThreadPoolExecutor(max_workers=2) as executor:
        upload_future = executor.submit(upload_concurrently)
        submit_future = executor.submit(submit_concurrently)
        assert upload_future.result(timeout=20) == "uploaded"
        submission_result = submit_future.result(timeout=20)

    record = Task.objects.get(pk=task.task_id)
    selected = tuple(
        TaskInputFile.objects.filter(task_id=task.task_id, is_selected=True)
    )
    assert len(selected) == 1
    assert submission_result in {"submitted", "input-not-yet-registered"}
    if submission_result == "submitted":
        assert record.status == Task.StatusChoices.SUBMITTED
        assert record.profile_snapshot is not None
    else:
        assert record.status == Task.StatusChoices.DRAFT
        assert record.profile_snapshot is None
        assert record.submitted_at is None


@pytest.mark.django_db(transaction=True)
def test_submission_and_version_switch_preserve_serialized_state(tmp_path):
    """并发版本切换与提交不会留下已提交旧版本的草稿。"""
    user, model, old_version, task = _create_draft(username="submit-switch-race")
    next_draft = create_model_version(
        model_id=model.model_id,
        version="2.0",
        algorithm_key="hr_br.example",
        params=None,
    )
    next_version = publish_model_version(version_id=next_draft.version_id)
    barrier = Barrier(2)

    def submit_concurrently() -> str:
        """在独立数据库连接上提交固定版本草稿。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                submit_user_task(
                    task_id=task.task_id,
                    user_id=user.pk,
                    channel="ios-cn",
                )
            except TaskConflict:
                return "conflict"
            return "submitted"
        finally:
            _close_worker_database_connection()

    def switch_concurrently() -> None:
        """在独立数据库连接上切换当前发布版本。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            select_channel_model_version(
                channel="ios-cn",
                model_id=model.model_id,
                version_id=next_version.version_id,
            )
        finally:
            _close_worker_database_connection()

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ), ThreadPoolExecutor(max_workers=2) as executor:
        _upload_ppg(user_id=user.pk, task_id=task.task_id)
        submit_future = executor.submit(submit_concurrently)
        switch_future = executor.submit(switch_concurrently)
        result = submit_future.result(timeout=20)
        switch_future.result(timeout=20)

    record = Task.objects.get(pk=task.task_id)
    selection = ChannelModelSelection.objects.get(
        channel__label="ios-cn",
        model_id=model.model_id,
    )
    assert selection.current_version_id == next_version.version_id
    if result == "submitted":
        assert record.status == Task.StatusChoices.SUBMITTED
        assert record.model_version_id == old_version.version_id
        assert record.profile_snapshot is not None
    else:
        assert record.status == Task.StatusChoices.FAILED
        assert record.failure_code == "TASK.MODEL_SWITCHED"
        assert record.profile_snapshot is None
    assert record.status != Task.StatusChoices.DRAFT


@pytest.mark.django_db(transaction=True)
def test_concurrent_submissions_transition_draft_at_most_once(tmp_path):
    """两个并发提交只有一个冻结快照并转换状态。"""
    user, _model, _version, task = _create_draft(username="submit-same-task-race")
    barrier = Barrier(2)

    def submit_concurrently() -> str:
        """在独立 PostgreSQL 连接上提交同一草稿。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                submit_user_task(
                    task_id=task.task_id,
                    user_id=user.pk,
                    channel="ios-cn",
                )
            except TaskConflict:
                return "conflict"
            return "submitted"
        finally:
            _close_worker_database_connection()

    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ), ThreadPoolExecutor(max_workers=2) as executor:
        _upload_ppg(user_id=user.pk, task_id=task.task_id)
        futures = tuple(executor.submit(submit_concurrently) for _ in range(2))
        outcomes = tuple(future.result(timeout=20) for future in futures)

    assert outcomes.count("submitted") == 1
    assert outcomes.count("conflict") == 1
    record = Task.objects.get(pk=task.task_id)
    original_snapshot = record.profile_snapshot
    original_submitted_at = record.submitted_at
    assert record.status == Task.StatusChoices.SUBMITTED
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ), pytest.raises(TaskConflict):
        submit_user_task(
            task_id=task.task_id,
            user_id=user.pk,
            channel="ios-cn",
        )
    record.refresh_from_db()
    assert record.profile_snapshot == original_snapshot
    assert record.submitted_at == original_submitted_at


@pytest.mark.django_db
def test_failed_task_cannot_be_resubmitted_or_have_snapshot_replaced(tmp_path):
    """用户重提失败任务时原终态、快照和提交时间保持不变。"""
    user, _model, _version, task = _create_draft(username="submit-failed-terminal")
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024,
    ):
        _upload_ppg(user_id=user.pk, task_id=task.task_id)
        submitted_at = timezone.now()
        snapshot = {"name": "保留值", "age_years": None, "history_json": None}
        Task.objects.filter(pk=task.task_id).update(
            status=Task.StatusChoices.FAILED,
            profile_snapshot=snapshot,
            submitted_at=submitted_at,
            finished_at=timezone.now(),
            failure_code="INFERENCE.INVALID_INPUT",
            failure_message="输入格式无效",
        )
        with pytest.raises(TaskConflict):
            submit_user_task(
                task_id=task.task_id,
                user_id=user.pk,
                channel="ios-cn",
            )

    record = Task.objects.get(pk=task.task_id)
    assert record.status == Task.StatusChoices.FAILED
    assert record.profile_snapshot == snapshot
    assert record.submitted_at == submitted_at
    assert record.failure_code == "INFERENCE.INVALID_INPUT"
