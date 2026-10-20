"""定义普通渠道可见的任务分页摘要与详情字段。"""

from typing import Annotated, Literal

from health_inference import (
    LowScoreDetails,
    QualityRejection,
    QualityRejectionReason,
)
from ninja import Schema
from pydantic import ConfigDict, Field

from health_service.schemas.internal.tasks import (
    TaskDetailData,
    TaskInputFileData,
    TaskSummaryData,
)
from health_service.schemas.responses.types import BeijingDateTime


class LowScoreQualityFailureDetailsResponse(Schema):
    """公开低分拒绝实际比较的分数与阈值。"""

    score: int | float = Field(description="本次计算实际得到的质量分数")
    threshold: int | float = Field(description="本次计算实际使用的拒绝阈值")


class LowScoreQualityFailureResponse(Schema):
    """公开带实际数值明细的低分拒绝变体。"""

    reason: Literal["LOW_SCORE"] = Field(description="稳定质量拒绝原因")
    details: LowScoreQualityFailureDetailsResponse = Field(
        description="本次低分比较的原始数值",
    )


class NoValidSignalQualityFailureResponse(Schema):
    """公开无可用信号且不包含数值明细的拒绝变体。"""

    reason: Literal["NO_VALID_SIGNAL"] = Field(description="稳定质量拒绝原因")
    details: None = Field(description="无可用评分时固定为 null")


QualityFailureResponse = Annotated[
    LowScoreQualityFailureResponse | NoValidSignalQualityFailureResponse,
    Field(discriminator="reason"),
]


class TaskSummaryResponse(Schema):
    """公开任务状态、固定版本和业务时间，不含快照或大结果。"""

    model_config = ConfigDict(populate_by_name=True)

    task_id: int = Field(alias="taskId", description="任务主键")
    model_id: int = Field(alias="modelId", description="固定模型主键")
    model_code: str = Field(alias="modelCode", description="固定模型编码")
    model_name: str = Field(alias="modelName", description="固定模型名称")
    version_id: int = Field(alias="versionId", description="固定模型版本主键")
    version: str = Field(description="固定模型版本号")
    status: str = Field(description="任务状态")
    is_example: bool = Field(alias="isExample", description="是否为非临床示例算法")
    created_at: BeijingDateTime = Field(alias="createdAt", description="任务创建时间（Asia/Shanghai）")
    submitted_at: BeijingDateTime | None = Field(alias="submittedAt", description="提交时间（Asia/Shanghai）")
    started_at: BeijingDateTime | None = Field(alias="startedAt", description="首次开始时间（Asia/Shanghai）")
    finished_at: BeijingDateTime | None = Field(alias="finishedAt", description="终结时间（Asia/Shanghai）")


class TaskPageResponse(Schema):
    """封装稳定倒序的任务摘要分页结果。"""

    model_config = ConfigDict(populate_by_name=True)

    items: tuple[TaskSummaryResponse, ...] = Field(description="当前页任务摘要")
    total: int = Field(description="符合条件的任务总数")
    offset: int = Field(description="本页起始偏移")
    limit: int = Field(description="本页条数上限")


class TaskDetailResponse(TaskSummaryResponse):
    """公开权威成功结果或安全失败摘要，不暴露内部诊断。"""

    result_payload: dict[str, object] | None = Field(
        default=None,
        alias="result",
        description="成功任务的权威完整结果",
    )
    failure_code: str | None = Field(default=None, alias="failureCode", description="安全失败代码")
    failure_message: str | None = Field(
        default=None,
        alias="failureMessage",
        description="安全失败摘要",
    )
    quality_failure: QualityFailureResponse | None = Field(
        default=None,
        alias="qualityFailure",
        description="仅质量拒绝时提供的原因判别字段与白名单明细",
    )


class TaskInputFileResponse(Schema):
    """返回已选中输入文件的元数据，不公开存储相对键。"""

    model_config = ConfigDict(populate_by_name=True)

    file_id: int = Field(alias="fileId", description="输入文件记录主键")
    logical_name: str = Field(alias="logicalName", description="算法声明的输入逻辑名")
    size_bytes: int = Field(alias="sizeBytes", description="文件大小，单位字节")
    uploaded_at: BeijingDateTime = Field(alias="uploadedAt", description="完整上传登记时间（Asia/Shanghai）")
    is_selected: bool = Field(alias="isSelected", description="是否为当前选中的输入")


def to_task_summary_response(task: TaskSummaryData) -> TaskSummaryResponse:
    """将已求值任务摘要转换为普通渠道响应。"""
    return TaskSummaryResponse(
        task_id=task.task_id,
        model_id=task.model_id,
        model_code=task.model_code,
        model_name=task.model_name,
        version_id=task.version_id,
        version=task.version,
        status=task.status,
        is_example=task.is_example,
        created_at=task.created_at,
        submitted_at=task.submitted_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def to_task_page_response(
    tasks: tuple[TaskSummaryData, ...],
    *,
    total: int,
    offset: int,
    limit: int,
) -> TaskPageResponse:
    """将任务摘要按持久化顺序转换为分页响应。"""
    return TaskPageResponse(
        items=tuple(to_task_summary_response(task) for task in tasks),
        total=total,
        offset=offset,
        limit=limit,
    )


def to_task_detail_response(task: TaskDetailData) -> TaskDetailResponse:
    """将权威结果或安全失败摘要转换为普通渠道详情响应。"""
    return TaskDetailResponse(
        task_id=task.task_id,
        model_id=task.model_id,
        model_code=task.model_code,
        model_name=task.model_name,
        version_id=task.version_id,
        version=task.version,
        status=task.status,
        is_example=task.is_example,
        created_at=task.created_at,
        submitted_at=task.submitted_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result_payload=task.result_payload,
        failure_code=task.failure_code,
        failure_message=task.failure_message,
        quality_failure=_to_quality_failure_response(task.quality_failure),
    )


def _to_quality_failure_response(
    rejection: QualityRejection | None,
) -> QualityFailureResponse | None:
    """按受控原因将内部拒绝明细转换为公开白名单变体。"""
    if rejection is None:
        return None
    if rejection.reason is QualityRejectionReason.LOW_SCORE and isinstance(
        rejection.details, LowScoreDetails
    ):
        return LowScoreQualityFailureResponse(
            reason=QualityRejectionReason.LOW_SCORE.value,
            details=LowScoreQualityFailureDetailsResponse(
                score=rejection.details.score,
                threshold=rejection.details.threshold,
            ),
        )
    if rejection.reason is QualityRejectionReason.NO_VALID_SIGNAL and rejection.details is None:
        return NoValidSignalQualityFailureResponse(
            reason=QualityRejectionReason.NO_VALID_SIGNAL.value,
            details=None,
        )
    return None


def to_task_input_file_response(file: TaskInputFileData) -> TaskInputFileResponse:
    """将已登记输入文件摘要转换为普通渠道响应。"""
    return TaskInputFileResponse(
        file_id=file.file_id,
        logical_name=file.logical_name,
        size_bytes=file.size_bytes,
        uploaded_at=file.uploaded_at,
        is_selected=file.is_selected,
    )
