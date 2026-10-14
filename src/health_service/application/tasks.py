"""实现普通渠道草稿创建、输入上传及本人任务列表与详情读取。"""

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from health_inference.registry import get_algorithm
from health_service.application.dispatch import dispatch_task
from health_service.application.task_exceptions import (
    InvalidTaskPagination,
    TaskConflict,
    TaskInputInvalid,
    TaskNotFound,
)
from health_service.domain.channels import ORDINARY_CHANNELS
from health_service.infrastructure.persistence import tasks as persistence
from health_service.infrastructure.persistence.exceptions import (
    TaskRecordConflictError,
    TaskRecordNotFoundError,
)
from health_service.infrastructure.storage import (
    EmptyUploadError,
    InvalidStorageKey,
    LocalFileStorage,
    StorageArea,
    StorageError,
    StorageFileNotFound,
    UploadTooLargeError,
)
from health_service.schemas.internal.profiles import ProfileData
from health_service.schemas.internal.tasks import (
    TaskDetailData,
    TaskInputFileData,
    TaskRecordData,
    TaskSummaryData,
)


def create_task_draft(
    *, user_id: int, channel: str, model_id: int
) -> TaskSummaryData:
    """固定请求渠道已发布的当前版本并创建未提交草稿。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    try:
        record = persistence.create_task_draft(
            user_id=user_id,
            channel_label=channel,
            model_id=model_id,
        )
    except TaskRecordNotFoundError as error:
        raise TaskNotFound from error
    return _task_summary(record)


def upload_task_input(
    *,
    task_id: int,
    user_id: int,
    channel: str,
    logical_name: str,
    source: object,
) -> TaskInputFileData:
    """在草稿行锁内完整保存并登记算法声明的输入文件。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    storage: LocalFileStorage | None = None
    stored_file = None
    try:
        with transaction.atomic():
            try:
                algorithm_key = persistence.lock_draft_task_for_input_upload(
                    task_id=task_id,
                    user_id=user_id,
                    channel_label=channel,
                )
            except TaskRecordConflictError as error:
                raise TaskConflict from error
            if algorithm_key is None:
                raise TaskNotFound
            algorithm = get_algorithm(algorithm_key)
            if logical_name not in algorithm.required_inputs:
                raise TaskInputInvalid
            storage = _task_storage()
            try:
                stored_file = storage.save_upload(
                    source,
                    area=StorageArea.TASK_DATA,
                    max_bytes=settings.MAX_UPLOAD_BYTES,
                )
            except (EmptyUploadError, UploadTooLargeError) as error:
                raise TaskInputInvalid from error
            return persistence.register_selected_task_input(
                task_id=task_id,
                logical_name=logical_name,
                storage_key=stored_file.storage_key,
                size_bytes=stored_file.size_bytes,
            )
    except BaseException:
        if stored_file is not None and storage is not None:
            try:
                storage.delete_file(StorageArea.TASK_DATA, stored_file.storage_key)
            except StorageError:
                pass
        raise


def _task_storage() -> LocalFileStorage:
    """按进程配置构造受保护的任务与制品存储。"""
    return LocalFileStorage(
        Path(settings.TASK_DATA_ROOT),
        Path(settings.MODEL_ARTIFACT_ROOT),
    )


def submit_user_task(
    *, task_id: int, user_id: int, channel: str
) -> TaskSummaryData:
    """校验当前版本与选中文件后冻结资料并提交本人草稿。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    target = persistence.get_user_task_submission_target(
        task_id=task_id,
        user_id=user_id,
        channel_label=channel,
    )
    if target is None:
        raise TaskNotFound
    model_id, version_id, algorithm_key = target
    algorithm = get_algorithm(algorithm_key)
    storage = _task_storage()
    with transaction.atomic():
        current_version_id = persistence.lock_channel_model_selection_for_submission(
            channel_label=channel,
            model_id=model_id,
        )
        if current_version_id is None:
            raise TaskConflict
        try:
            task = persistence.lock_user_task_for_submission(
                task_id=task_id,
                user_id=user_id,
                channel_label=channel,
            )
        except TaskRecordConflictError as error:
            raise TaskConflict from error
        if task is None:
            raise TaskNotFound
        if (
            task.model_id != model_id
            or task.version_id != version_id
            or task.version_id != current_version_id
            or task.algorithm_key != algorithm_key
        ):
            raise TaskConflict
        input_records = persistence.list_selected_task_input_records(task_id=task_id)
        files_by_name = {item.logical_name: item for item in input_records}
        if not set(algorithm.required_inputs).issubset(files_by_name):
            raise TaskInputInvalid
        try:
            for logical_name in algorithm.required_inputs:
                storage.resolve_file(
                    StorageArea.TASK_DATA,
                    files_by_name[logical_name].storage_key,
                )
        except (InvalidStorageKey, StorageFileNotFound) as error:
            raise TaskInputInvalid from error
        profile = persistence.lock_task_owner_profile(user_id=user_id)
        submitted_at = timezone.now()
        profile_snapshot = _profile_snapshot(
            profile,
            submitted_at=submitted_at,
        )
        try:
            submitted_record = persistence.mark_task_submitted(
                task_id=task_id,
                profile_snapshot=profile_snapshot,
                submitted_at=submitted_at,
                user_id=user_id,
            )
        except TaskRecordConflictError as error:
            raise TaskConflict from error
    return _task_summary(submitted_record)


def submit_and_dispatch_user_task(
    *, task_id: int, user_id: int, channel: str
) -> TaskSummaryData:
    """提交草稿并仅在数据库事务提交后立即尝试派发消息。"""
    with transaction.atomic():
        task = submit_user_task(
            task_id=task_id,
            user_id=user_id,
            channel=channel,
        )
        transaction.on_commit(lambda: dispatch_task(task.task_id))
    return task


def list_user_tasks(
    *, user_id: int, channel: str, offset: int, limit: int
) -> tuple[int, tuple[TaskSummaryData, ...]]:
    """按固定倒序分页读取本人当前渠道任务摘要。"""
    _validate_pagination(offset=offset, limit=limit)
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    total, records = persistence.list_user_task_records(
        user_id=user_id,
        channel_label=channel,
        offset=offset,
        limit=limit,
    )
    summaries = tuple(_task_summary(record) for record in records)
    return total, summaries


def read_user_task(
    *, task_id: int, user_id: int, channel: str
) -> TaskDetailData:
    """只按账号与来源渠道读取任务详情及允许公开的结果字段。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    record = persistence.get_user_task_record(
        task_id=task_id,
        user_id=user_id,
        channel_label=channel,
    )
    if record is None:
        raise TaskNotFound
    summary = _task_summary(record)
    return TaskDetailData(
        **summary.model_dump(),
        result_payload=(
            record.result_payload
            if record.status == "SUCCEEDED"
            else None
        ),
        failure_code=record.failure_code if record.status == "FAILED" else None,
        failure_message=(
            record.failure_message
            if record.status == "FAILED"
            else None
        ),
    )


def open_user_task_input_file(
    *,
    task_id: int,
    file_id: int,
    user_id: int,
    channel: str,
) -> BinaryIO:
    """按本人任务和来源渠道授权打开当前或历史输入文件。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    storage_key = persistence.get_user_task_input_file_storage_key(
        task_id=task_id,
        file_id=file_id,
        user_id=user_id,
        channel_label=channel,
    )
    if storage_key is None:
        raise TaskNotFound
    return _open_task_file(storage_key)


def open_user_task_output_file(
    *,
    task_id: int,
    logical_name: str,
    user_id: int,
    channel: str,
) -> BinaryIO:
    """只打开本人成功结果明确登记的处理后文件。"""
    if channel not in ORDINARY_CHANNELS:
        raise TaskNotFound
    record = persistence.get_user_task_record(
        task_id=task_id,
        user_id=user_id,
        channel_label=channel,
    )
    if (
        record is None
        or record.status != "SUCCEEDED"
        or record.result_payload is None
    ):
        raise TaskNotFound
    output_files = record.result_payload.get("output_files")
    if not isinstance(output_files, Mapping):
        raise TaskNotFound
    storage_key = output_files.get(logical_name)
    if not isinstance(storage_key, str) or not storage_key:
        raise TaskNotFound
    return _open_task_file(storage_key)


def _open_task_file(storage_key: str) -> BinaryIO:
    """打开根目录内的登记文件并将存储错误折叠为资源不存在。"""
    try:
        path = _task_storage().resolve_file(StorageArea.TASK_DATA, storage_key)
        return path.open("rb")
    except (OSError, StorageError) as error:
        raise TaskNotFound from error


def _profile_snapshot(
    profile: ProfileData | None,
    *,
    submitted_at: datetime,
) -> dict[str, object]:
    """将提交时完整资料与明确单位冻结为不可变任务值。"""
    birth_date = None if profile is None else profile.birth_date
    submission_date = timezone.localdate(submitted_at)
    return {
        "name": None if profile is None else profile.name,
        "gender": 0 if profile is None else profile.gender,
        "birth_date": None if birth_date is None else birth_date.isoformat(),
        "age_years": _age_on(birth_date, submission_date),
        "height_cm": (
            None if profile is None or profile.height_cm is None else str(profile.height_cm)
        ),
        "weight_kg": (
            None if profile is None or profile.weight_kg is None else str(profile.weight_kg)
        ),
        "medical_history": None if profile is None else profile.medical_history,
        "history_json": None if profile is None else profile.history_json,
    }


def _age_on(birth_date: date | None, on_date: date) -> int | None:
    """按提交日和生日计算周岁，缺少出生日期时保持未知。"""
    if birth_date is None:
        return None
    age = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def _task_summary(record: TaskRecordData) -> TaskSummaryData:
    """将固定算法定义的示例身份加入已求值任务摘要。"""
    algorithm = get_algorithm(record.algorithm_key)
    return TaskSummaryData(
        task_id=record.task_id,
        model_id=record.model_id,
        model_code=record.model_code,
        model_name=record.model_name,
        version_id=record.version_id,
        version=record.version,
        status=record.status,
        is_example=algorithm.is_example,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _validate_pagination(*, offset: int, limit: int) -> None:
    """拒绝负偏移、非正页长和超过 100 的任务列表页长。"""
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise InvalidTaskPagination
