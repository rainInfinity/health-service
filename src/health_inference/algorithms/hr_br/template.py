"""描述未来正式 HR/BR 算法的接口，不注册或伪造执行器。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlgorithmTemplate:
    """记录未来算法接入所需的输入和结果边界。"""

    key: str  # 预留给未来正式执行器的稳定标识。
    required_inputs: tuple[str, ...]  # 正式算法声明的输入逻辑名。
    required_artifacts: tuple[str, ...]  # 参考契约不要求模型权重制品。
    timestamp_column: str  # CSV 时间戳列名，单位为毫秒。
    rgb_columns: tuple[str, ...]  # 可选 RGB 输入通道列名。
    yuv_columns: tuple[str, ...]  # 可选 YUV 输入通道列名。
    result_units: tuple[tuple[str, str], ...]  # 未来正式 HR/BR 字段的业务单位。
    executable: bool  # 模板恒为 False，避免被误当成正式算法。


HR_BR_TEMPLATE = AlgorithmTemplate(
    key="hr_br",
    required_inputs=("ppg",),
    required_artifacts=(),
    timestamp_column="timestampFrame",
    rgb_columns=("avgR", "avgG", "avgB"),
    yuv_columns=("avgY", "avgU", "avgV"),
    result_units=(("hr", "beats_per_minute"), ("br", "breaths_per_minute")),
    executable=False,
)
