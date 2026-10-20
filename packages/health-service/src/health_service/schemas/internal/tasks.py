"""定义不含惰性 ORM 关系的任务查询结果。"""

from datetime import datetime

from health_inference import QualityRejection
from pydantic import BaseModel, ConfigDict, Field


class TaskRecordData(BaseModel):
    """保存持久化层返回的任务及固定模型版本字段。"""

    model_config = ConfigDict(frozen=True)

    task_id: int = Field(description="任务主键")
    model_id: int = Field(description="固定模型主键")
    model_code: str = Field(description="固定模型编码")
    model_name: str = Field(description="固定模型名称")
    version_id: int = Field(description="固定模型版本主键")
    version: str = Field(description="固定模型版本号")
    algorithm_key: str = Field(description="固定算法标识，仅供内部查询")
    status: str = Field(description="任务当前状态")
    created_at: datetime = Field(description="任务创建时间")
    submitted_at: datetime | None = Field(description="提交时间；未提交时为 null")
    started_at: datetime | None = Field(description="首次开始时间；未开始时为 null")
    finished_at: datetime | None = Field(description="终结时间；未终结时为 null")
    result_payload: dict[str, object] | None = Field(
        default=None,
        description="权威完整结果；列表摘要不载入",
    )
    failure_code: str | None = Field(default=None, description="安全失败代码")
    failure_message: str | None = Field(default=None, description="安全失败摘要")
    failure_detail: object | None = Field(
        default=None,
        description="仅任务详情加载的私有失败 JSON",
    )


class TaskSummaryData(BaseModel):
    """保存普通渠道可见的任务摘要与示例标志。"""

    model_config = ConfigDict(frozen=True)

    task_id: int = Field(description="任务主键")
    model_id: int = Field(description="固定模型主键")
    model_code: str = Field(description="固定模型编码")
    model_name: str = Field(description="固定模型名称")
    version_id: int = Field(description="固定模型版本主键")
    version: str = Field(description="固定模型版本号")
    status: str = Field(description="任务当前状态")
    is_example: bool = Field(description="固定算法是否为非临床示例")
    created_at: datetime = Field(description="任务创建时间")
    submitted_at: datetime | None = Field(description="提交时间；未提交时为 null")
    started_at: datetime | None = Field(description="首次开始时间；未开始时为 null")
    finished_at: datetime | None = Field(description="终结时间；未终结时为 null")


class TaskDetailData(TaskSummaryData):
    """保存本人任务的权威结果、安全摘要或类型化质量拒绝。"""

    result_payload: dict[str, object] | None = Field(
        default=None,
        description="成功任务权威完整结果",
    )
    failure_code: str | None = Field(default=None, description="安全失败代码")
    failure_message: str | None = Field(default=None, description="安全失败摘要")
    quality_failure: QualityRejection | None = Field(
        default=None,
        description="仅符合质量拒绝契约时允许公开的类型化信息",
    )


class TaskInputFileData(BaseModel):
    """保存已完整登记的任务输入文件摘要。"""

    model_config = ConfigDict(frozen=True)

    file_id: int = Field(description="输入文件记录主键")
    logical_name: str = Field(description="算法声明的输入逻辑名")
    size_bytes: int = Field(description="文件大小，单位字节")
    uploaded_at: datetime = Field(description="完整上传登记时间")
    is_selected: bool = Field(description="是否为当前选中的同名输入")


class TaskInputFileRecordData(BaseModel):
    """保存提交校验所需的选中输入相对存储键。"""

    model_config = ConfigDict(frozen=True)

    file_id: int = Field(description="输入文件记录主键")
    logical_name: str = Field(description="算法声明的输入逻辑名")
    storage_key: str = Field(description="仅内部使用的受控相对存储键")
    size_bytes: int = Field(description="文件大小，单位字节")
