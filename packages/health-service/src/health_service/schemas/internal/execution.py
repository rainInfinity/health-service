"""定义推理执行输入及结果的内部数据结构。"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskExecutionContextData:
    """保存有效租约任务的固定版本、资料快照、可选元数据和输入键。"""

    task_id: int  # 任务主键。
    model_version_id: int  # 提交时固定的模型版本主键。
    profile_snapshot: Mapping[str, object] | None  # 提交时冻结的资料快照。
    client_metadata: Mapping[str, object] | None  # 可选客户端元数据；未提供时为空。
    input_storage_keys: Mapping[str, str]  # 已选输入逻辑名到任务存储相对键。


@dataclass(frozen=True, slots=True)
class PreparedInferenceResult:
    """保存已验证算法结果及处理后文件的受控存储键。"""

    payload: Mapping[str, object]  # 算法返回的完整权威结果，不包含文件系统路径。
    metrics: Mapping[str, int | float | bool]  # 已按算法契约校验的实际指标值。
    output_files: Mapping[str, str]  # 输出逻辑名到任务存储相对键。


@dataclass(frozen=True, slots=True)
class ProjectedTaskMetricData:
    """保存一个通过固定版本选择的实际指标投影。"""

    metric_id: int  # 数据库中的稳定指标定义主键。
    value: int | float | bool  # 实际输出值；布尔值与数值保持不同类型。
