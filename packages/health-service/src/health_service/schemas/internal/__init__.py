"""定义应用内部使用的已求值数据结构。"""

from .accounts import AccountSummary
from .catalog import (
    AvailableModelData,
    AvailableModelPage,
    CatalogModelData,
    MetricDefinitionData,
    ModelArtifactData,
    ModelVersionData,
)
from .profiles import ProfileData
from .tasks import (
    TaskDetailData,
    TaskInputFileData,
    TaskInputFileRecordData,
    TaskRecordData,
    TaskSummaryData,
)

__all__ = [
    "AccountSummary",
    "AvailableModelData",
    "AvailableModelPage",
    "CatalogModelData",
    "MetricDefinitionData",
    "ModelArtifactData",
    "ModelVersionData",
    "ProfileData",
    "TaskDetailData",
    "TaskInputFileData",
    "TaskInputFileRecordData",
    "TaskRecordData",
    "TaskSummaryData",
]
