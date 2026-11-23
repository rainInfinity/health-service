"""定义普通用户任务草稿创建的外部请求契约。"""

from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field, StrictInt


class CreateTaskRequest(Schema):
    """只接收目标模型，不接受客户端指定用户或版本。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: StrictInt = Field(alias="modelId", gt=0, description="当前渠道已配置模型主键")


class SubmitMeasurementRequest(Schema):
    """只接收完整的客户端自报采集属性，不接收时间或归属。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    capture_method: Literal["fingertip", "fingertip_chest"] = Field(
        alias="captureMethod", description="指尖或指尖加胸部采集方式"
    )
    capture_mode: Literal["active"] = Field(
        alias="captureMode", description="采集来源，当前仅支持主动采集"
    )
    duration_ms: StrictInt = Field(
        alias="durationMs",
        gt=0,
        le=2**63 - 1,
        description="实际采集时长，严格正整数毫秒，不超过有符号 64 位上限",
    )


class SubmitTaskRequest(Schema):
    """接受最终提交时彼此独立的采集属性和私有客户端元数据。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    client_metadata: dict[str, object] | None = Field(
        default=None,
        alias="clientMetadata",
        description="可选客户端元数据；缺省或 null 表示未提供",
    )
    measurement: SubmitMeasurementRequest | None = Field(
        default=None,
        description="可选完整采集属性；缺省或 null 表示未知，不推断默认值",
    )
