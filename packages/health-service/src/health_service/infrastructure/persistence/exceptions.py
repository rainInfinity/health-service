"""定义持久化层识别的具体数据库约束异常。"""


class UsernameConflictError(Exception):
    """表示 PostgreSQL 用户名唯一约束冲突。"""


class ModelCodeConflictError(Exception):
    """表示模型编码唯一约束冲突。"""


class ModelVersionConflictError(Exception):
    """表示同一模型内版本号唯一约束冲突。"""


class ModelArtifactConflictError(Exception):
    """表示版本制品逻辑名或全局存储键冲突。"""


class MetricCodeConflictError(Exception):
    """表示指标代码唯一约束冲突。"""


class ChannelModelSelectionConflictError(Exception):
    """表示渠道模型选择唯一约束冲突。"""


class ModelRecordNotFoundError(Exception):
    """表示创建版本时所属模型不存在。"""


class ModelVersionRecordNotFoundError(Exception):
    """表示需要锁定的模型版本不存在。"""


class TaskRecordNotFoundError(Exception):
    """表示渠道没有可用版本或任务不属于当前账号与渠道。"""


class TaskRecordConflictError(Exception):
    """表示任务持久化状态与请求操作冲突。"""
