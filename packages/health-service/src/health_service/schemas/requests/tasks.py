"""定义普通用户任务草稿创建的外部请求契约。"""

from ninja import Schema
from pydantic import ConfigDict, Field, StrictInt


class CreateTaskRequest(Schema):
    """只接收目标模型，不接受客户端指定用户或版本。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: StrictInt = Field(alias="modelId", gt=0, description="当前渠道已配置模型主键")
