"""实现草稿创建和本人任务摘要／详情的具体 ORM 查询。"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce, Now
from django.utils import timezone

from health_service.models import (
    Channel,
    ChannelModelSelection,
    ModelVersion,
    Task,
    TaskInputFile,
    TaskMetric,
)
from health_service.schemas.internal.execution import (
    ProjectedTaskMetricData,
    TaskExecutionContextData,
)
from health_service.schemas.internal.profiles import ProfileData
from health_service.schemas.internal.tasks import (
    TaskInputFileData,
    TaskInputFileRecordData,
    TaskRecordData,
)

from .exceptions import TaskRecordConflictError, TaskRecordNotFoundError
from .profiles import lock_account, lock_profile

_TASK_VERSION_FIELDS = (
    "model_version__id",
    "model_version__model_id",
    "model_version__version",
    "model_version__algorithm_key",
    "model_version__model__id",
    "model_version__model__code",
    "model_version__model__name",
)
_SELECTION_VERSION_FIELDS = (
    "current_version__id",
    "current_version__model_id",
    "current_version__version",
    "current_version__algorithm_key",
    "current_version__published_at",
    "current_version__model__id",
    "current_version__model__code",
    "current_version__model__name",
)
_TASK_SUMMARY_FIELDS = (
    "id",
    "model_version_id",
    "status",
    "created_at",
    "submitted_at",
    "started_at",
    "finished_at",
    *_TASK_VERSION_FIELDS,
)
_TASK_DETAIL_FIELDS = (
    *_TASK_SUMMARY_FIELDS,
    "result_payload",
    "failure_code",
    "failure_message",
    "failure_detail",
)


def create_task_draft(*, user_id: int, channel_label: str, model_id: int) -> TaskRecordData:
    """按渠道行、当前选择行顺序加锁并固定已发布版本。"""
    with transaction.atomic():
        channel = (
            Channel.objects.select_for_update(of=("self",))
            .only("id")
            .filter(label=channel_label)
            .first()
        )
        if channel is None:
            raise TaskRecordNotFoundError
        selection = (
            ChannelModelSelection.objects.select_for_update(of=("self",))
            .select_related("current_version__model")
            .only(
                "id",
                "model_id",
                "current_version_id",
                *_SELECTION_VERSION_FIELDS,
            )
            .filter(channel_id=channel.pk, model_id=model_id)
            .first()
        )
        if selection is None:
            raise TaskRecordNotFoundError
        version = selection.current_version
        if version.model_id != model_id or version.published_at is None:
            raise TaskRecordNotFoundError
        task = Task.objects.create(
            user_id=user_id,
            channel_id=channel.pk,
            model_version_id=version.pk,
        )
        return _task_record_data(task, version)


def lock_draft_task_for_input_upload(
    *, task_id: int, user_id: int, channel_label: str
) -> str | None:
    """锁定本人当前渠道草稿并返回其固定算法标识。"""
    task = (
        Task.objects.select_for_update(of=("self",))
        .select_related("model_version")
        .only("id", "status", "model_version__algorithm_key")
        .filter(
            pk=task_id,
            user_id=user_id,
            channel__label=channel_label,
        )
        .first()
    )
    if task is None:
        return None
    if task.status != Task.StatusChoices.DRAFT:
        raise TaskRecordConflictError
    return task.model_version.algorithm_key


def register_selected_task_input(
    *,
    task_id: int,
    logical_name: str,
    storage_key: str,
    size_bytes: int,
) -> TaskInputFileData:
    """将完整文件登记为同名输入的唯一当前选择并保留旧记录。"""
    uploaded_at = timezone.now()
    TaskInputFile.objects.filter(
        task_id=task_id,
        logical_name=logical_name,
        is_selected=True,
    ).update(is_selected=False, updated_at=uploaded_at)
    input_file = TaskInputFile.objects.create(
        task_id=task_id,
        logical_name=logical_name,
        storage_key=storage_key,
        size_bytes=size_bytes,
        uploaded_at=uploaded_at,
        is_selected=True,
    )
    return TaskInputFileData(
        file_id=input_file.pk,
        logical_name=input_file.logical_name,
        size_bytes=input_file.size_bytes,
        uploaded_at=input_file.uploaded_at,
        is_selected=input_file.is_selected,
    )


def get_user_task_submission_target(
    *, task_id: int, user_id: int, channel_label: str
) -> tuple[int, int, str] | None:
    """按归属读取任务固定模型、版本及算法，不锁定任何行。"""
    return (
        Task.objects.filter(
            pk=task_id,
            user_id=user_id,
            channel__label=channel_label,
        )
        .values_list(
            "model_version__model_id",
            "model_version_id",
            "model_version__algorithm_key",
        )
        .first()
    )


def lock_channel_model_selection_for_submission(*, channel_label: str, model_id: int) -> int | None:
    """依次锁定渠道和当前版本选择，返回所选发布版本 ID。"""
    channel = (
        Channel.objects.select_for_update(of=("self",))
        .only("id")
        .filter(label=channel_label)
        .first()
    )
    if channel is None:
        return None
    return (
        ChannelModelSelection.objects.select_for_update(of=("self",))
        .filter(channel_id=channel.pk, model_id=model_id)
        .values_list("current_version_id", flat=True)
        .first()
    )


def lock_user_task_for_submission(
    *, task_id: int, user_id: int, channel_label: str
) -> TaskRecordData | None:
    """在渠道选择锁之后锁定本人任务行并返回当前摘要。"""
    task = (
        Task.objects.select_for_update(of=("self",))
        .select_related("model_version__model")
        .only(*_TASK_SUMMARY_FIELDS)
        .filter(
            pk=task_id,
            user_id=user_id,
            channel__label=channel_label,
        )
        .first()
    )
    if task is None:
        return None
    if task.status != Task.StatusChoices.DRAFT:
        raise TaskRecordConflictError
    return _task_record_data(task, task.model_version)


def list_selected_task_input_records(*, task_id: int) -> tuple[TaskInputFileRecordData, ...]:
    """读取草稿当前选中输入的内部相对键与大小。"""
    return tuple(
        TaskInputFileRecordData(
            file_id=item["id"],
            logical_name=item["logical_name"],
            storage_key=item["storage_key"],
            size_bytes=item["size_bytes"],
        )
        for item in TaskInputFile.objects.filter(
            task_id=task_id,
            is_selected=True,
        )
        .order_by("logical_name", "id")
        .values("id", "logical_name", "storage_key", "size_bytes")
    )


def lock_task_owner_profile(*, user_id: int) -> ProfileData | None:
    """按既有用户后资料锁顺序冻结账号当前资料。"""
    lock_account(user_id=user_id)
    return lock_profile(user_id=user_id)


def mark_task_submitted(
    *,
    task_id: int,
    profile_snapshot: dict[str, object],
    submitted_at: datetime,
    client_metadata: dict[str, object] | None,
    user_id: int,
) -> TaskRecordData:
    """原子冻结草稿资料并转换为已提交状态。"""
    updated = Task.objects.filter(
        pk=task_id,
        status=Task.StatusChoices.DRAFT,
    ).update(
        status=Task.StatusChoices.SUBMITTED,
        profile_snapshot=profile_snapshot,
        submitted_at=submitted_at,
        client_metadata=client_metadata,
        updated_at=submitted_at,
        updated_by=f"user:{user_id}",
    )
    if updated != 1:
        raise TaskRecordConflictError
    task = (
        Task.objects.select_related("model_version__model")
        .only(*_TASK_SUMMARY_FIELDS)
        .get(pk=task_id)
    )
    return _task_record_data(task, task.model_version)


def mark_task_dispatched(*, task_id: int) -> bool:
    """仅为仍可派发的任务写入数据库时钟及稳定系统操作人。"""
    updated = (
        Task.objects.filter(pk=task_id)
        .filter(
            Q(status=Task.StatusChoices.SUBMITTED)
            | Q(
                status=Task.StatusChoices.RUNNING,
                lease_expires_at__lte=Now(),
            )
        )
        .update(
            last_dispatched_at=Now(),
            updated_at=Now(),
            updated_by="system:dispatcher",
        )
    )
    return updated == 1


def list_dispatchable_task_ids(*, limit: int, dispatch_interval_seconds: int) -> tuple[int, ...]:
    """按稳定主键顺序有界读取待派发及租约过期任务。"""
    dispatch_cutoff = timezone.now() - timedelta(seconds=dispatch_interval_seconds)
    task_ids = (
        Task.objects.filter(
            (
                Q(status=Task.StatusChoices.SUBMITTED)
                & (Q(last_dispatched_at__isnull=True) | Q(last_dispatched_at__lte=dispatch_cutoff))
            )
            | Q(
                status=Task.StatusChoices.RUNNING,
                lease_expires_at__lte=Now(),
            )
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    return tuple(task_ids)


def claim_task_for_execution(*, task_id: int, lease_seconds: int, max_attempts: int) -> UUID | None:
    """仅领取待执行或按数据库时间已过期的任务并增加一次有效尝试。"""
    claimable = Q(status=Task.StatusChoices.SUBMITTED) | Q(
        status=Task.StatusChoices.RUNNING,
        lease_expires_at__lte=Now(),
    )
    with transaction.atomic():
        task = (
            Task.objects.select_for_update(of=("self",))
            .filter(pk=task_id)
            .filter(claimable)
            .only("pk", "status", "attempt_count")
            .first()
        )
        if task is None:
            return None
        if task.status == Task.StatusChoices.RUNNING and task.attempt_count >= max_attempts:
            Task.objects.filter(
                pk=task.pk,
                status=Task.StatusChoices.RUNNING,
                attempt_count__gte=max_attempts,
                lease_expires_at__lte=Now(),
            ).update(
                status=Task.StatusChoices.FAILED,
                failure_code="TASK.RETRY_EXHAUSTED",
                failure_message="任务执行次数已耗尽，无法继续重试。",
                failure_detail=None,
                finished_at=Now(),
                result_payload=None,
                execution_token=None,
                lease_expires_at=None,
                updated_at=Now(),
                updated_by="system:worker",
            )
            return None
        if task.attempt_count >= max_attempts:
            return None
        execution_token = uuid4()
        updated = (
            Task.objects.filter(pk=task.pk)
            .filter(claimable)
            .filter(attempt_count__lt=max_attempts)
            .update(
                status=Task.StatusChoices.RUNNING,
                execution_token=execution_token,
                lease_expires_at=Now() + timedelta(seconds=lease_seconds),
                attempt_count=F("attempt_count") + 1,
                started_at=Coalesce("started_at", Now()),
                updated_at=Now(),
                updated_by="system:worker",
            )
        )
        return execution_token if updated == 1 else None


def renew_task_execution_lease(*, task_id: int, execution_token: UUID, lease_seconds: int) -> bool:
    """仅以匹配且仍有效的执行令牌续约，不复活过期租约。"""
    updated = Task.objects.filter(
        pk=task_id,
        status=Task.StatusChoices.RUNNING,
        execution_token=execution_token,
        lease_expires_at__gt=Now(),
    ).update(
        lease_expires_at=Now() + timedelta(seconds=lease_seconds),
        updated_at=Now(),
        updated_by="system:worker",
    )
    return updated == 1


def get_active_task_execution_context(
    *, task_id: int, execution_token: UUID
) -> TaskExecutionContextData | None:
    """只读取仍持有有效数据库租约任务的固定版本、快照和选中输入。"""
    task = (
        Task.objects.filter(
            pk=task_id,
            status=Task.StatusChoices.RUNNING,
            execution_token=execution_token,
            lease_expires_at__gt=Now(),
        )
        .values("id", "model_version_id", "profile_snapshot", "client_metadata")
        .first()
    )
    if task is None:
        return None
    input_storage_keys = dict(
        TaskInputFile.objects.filter(
            task_id=task_id,
            is_selected=True,
        )
        .order_by("logical_name", "pk")
        .values_list("logical_name", "storage_key")
    )
    return TaskExecutionContextData(
        task_id=task["id"],
        model_version_id=task["model_version_id"],
        profile_snapshot=task["profile_snapshot"],
        client_metadata=task["client_metadata"],
        input_storage_keys=input_storage_keys,
    )


def save_task_execution_success(
    *,
    task_id: int,
    execution_token: UUID,
    result_payload: dict[str, object],
    metrics: tuple[ProjectedTaskMetricData, ...],
) -> bool:
    """在当前有效租约内原子保存权威结果、终态与实际指标投影。"""
    with transaction.atomic():
        task = (
            Task.objects.select_for_update(of=("self",))
            .filter(
                pk=task_id,
                status=Task.StatusChoices.RUNNING,
                execution_token=execution_token,
                lease_expires_at__gt=Now(),
            )
            .only("pk")
            .first()
        )
        if task is None:
            return False
        updated = Task.objects.filter(
            pk=task.pk,
            status=Task.StatusChoices.RUNNING,
            execution_token=execution_token,
            lease_expires_at__gt=Now(),
        ).update(
            status=Task.StatusChoices.SUCCEEDED,
            result_payload=result_payload,
            failure_code=None,
            failure_message=None,
            failure_detail=None,
            finished_at=Now(),
            execution_token=None,
            lease_expires_at=None,
            updated_at=Now(),
            updated_by="system:worker",
        )
        if updated != 1:
            return False
        recorded_at = timezone.now()
        TaskMetric.objects.bulk_create(
            [
                TaskMetric(
                    task_id=task_id,
                    metric_id=metric.metric_id,
                    numeric_value=(metric.value if type(metric.value) is not bool else None),
                    boolean_value=(metric.value if type(metric.value) is bool else None),
                    created_at=recorded_at,
                    updated_at=recorded_at,
                    created_by="system:worker",
                    updated_by="system:worker",
                )
                for metric in metrics
            ]
        )
        return True


def record_task_execution_failure(
    *,
    task_id: int,
    execution_token: UUID,
    failure_code: str,
    failure_message: str,
    failure_detail: dict[str, object] | None,
) -> bool:
    """仅在匹配且未过期租约内原子终结任务并保存受控失败明细。"""
    with transaction.atomic():
        task = (
            Task.objects.select_for_update(of=("self",))
            .filter(
                pk=task_id,
                status=Task.StatusChoices.RUNNING,
                execution_token=execution_token,
                lease_expires_at__gt=Now(),
            )
            .only("pk")
            .first()
        )
        if task is None:
            return False
        updated = Task.objects.filter(
            pk=task.pk,
            status=Task.StatusChoices.RUNNING,
            execution_token=execution_token,
            lease_expires_at__gt=Now(),
        ).update(
            status=Task.StatusChoices.FAILED,
            failure_code=failure_code,
            failure_message=failure_message,
            failure_detail=failure_detail,
            finished_at=Now(),
            execution_token=None,
            lease_expires_at=None,
            updated_at=Now(),
            updated_by="system:worker",
        )
        return updated == 1


def list_user_task_records(
    *, user_id: int, channel_label: str, offset: int, limit: int
) -> tuple[int, tuple[TaskRecordData, ...]]:
    """按创建时间与主键倒序读取不含快照或完整结果的摘要页。"""
    tasks = (
        Task.objects.filter(user_id=user_id, channel__label=channel_label)
        .select_related("model_version__model")
        .only(*_TASK_SUMMARY_FIELDS)
        .order_by("-created_at", "-pk")
    )
    total = tasks.count()
    page = tasks[offset : offset + limit]
    return total, tuple(_task_record_data(task, task.model_version) for task in page)


def get_user_task_record(
    *, task_id: int, user_id: int, channel_label: str
) -> TaskRecordData | None:
    """同时按账号、来源渠道和任务主键读取受限详情字段。"""
    task = (
        Task.objects.filter(
            pk=task_id,
            user_id=user_id,
            channel__label=channel_label,
        )
        .select_related("model_version__model")
        .only(*_TASK_DETAIL_FIELDS)
        .first()
    )
    if task is None:
        return None
    return _task_record_data(task, task.model_version, include_detail=True)


def get_user_task_input_file_storage_key(
    *,
    task_id: int,
    file_id: int,
    user_id: int,
    channel_label: str,
) -> str | None:
    """仅按任务、账号和来源渠道读取输入文件的受控存储键。"""
    return (
        TaskInputFile.objects.filter(
            pk=file_id,
            task_id=task_id,
            task__user_id=user_id,
            task__channel__label=channel_label,
        )
        .values_list("storage_key", flat=True)
        .first()
    )


def _task_record_data(
    task: Task,
    version: ModelVersion,
    *,
    include_detail: bool = False,
) -> TaskRecordData:
    """投影已加载任务及固定版本，不触碰健康资料快照。"""
    owner = version.model
    return TaskRecordData(
        task_id=task.pk,
        model_id=version.model_id,
        model_code=owner.code,
        model_name=owner.name,
        version_id=version.pk,
        version=version.version,
        algorithm_key=version.algorithm_key,
        status=task.status,
        created_at=task.created_at,
        submitted_at=task.submitted_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result_payload=task.result_payload if include_detail else None,
        failure_code=task.failure_code if include_detail else None,
        failure_message=task.failure_message if include_detail else None,
        failure_detail=task.failure_detail if include_detail else None,
    )
