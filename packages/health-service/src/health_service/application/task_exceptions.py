"""定义普通任务用例的稳定资源错误。"""


class TaskNotFound(Exception):
    """表示任务不属于当前账号与渠道，或渠道没有可用模型。"""


class InvalidTaskPagination(Exception):
    """表示任务列表分页越界或参数类型无效。"""


class TaskConflict(Exception):
    """表示任务状态或固定版本不允许当前操作。"""


class TaskInputInvalid(Exception):
    """表示输入逻辑名或上传文件不符合任务契约。"""
