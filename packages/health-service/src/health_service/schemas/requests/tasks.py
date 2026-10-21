"""定义普通用户任务草稿创建的外部请求契约。"""

from ninja import Schema
from pydantic import ConfigDict, Field, StrictInt


class CreateTaskRequest(Schema):
    """只接收目标模型，不接受客户端指定用户或版本。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: StrictInt = Field(alias="modelId", gt=0, description="当前渠道已配置模型主键")


class SubmitTaskRequest(Schema):
    """接受推理任务最终提交时可选的客户端元数据。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    client_metadata: dict[str, object] | None = Field(
        default=None,
        alias="clientMetadata",
        description="可选客户端元数据；缺省或 null 表示未提供",
    )
