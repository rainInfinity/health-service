"""定义可安全跨越推理边界的稳定异常。"""

from enum import StrEnum

from .quality import QualityRejection


class InputErrorReason(StrEnum):
    """列出可安全映射给用户的受控输入失败原因。"""

    CSV_FORMAT = "CSV_FORMAT"  # CSV 编码、表头或记录结构错误。
    MISSING_COLUMN = "MISSING_COLUMN"  # 缺少必需输入列。
    INVALID_CHANNELS = "INVALID_CHANNELS"  # RGB/YUV 通道组缺失或冲突。
    INVALID_NUMERIC_VALUE = "INVALID_NUMERIC_VALUE"  # 通道值无法解析为数值。
    NON_FINITE_VALUE = "NON_FINITE_VALUE"  # 输入数值为 NaN 或无穷值。
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"  # 毫秒时间戳无效或未严格递增。
    UNSUPPORTED_BLOCK_LAYOUT = "UNSUPPORTED_BLOCK_LAYOUT"  # 输入不是单块布局。
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # 样本不足或无法标准化。
    INVALID_SAMPLING_RATE = "INVALID_SAMPLING_RATE"  # 采样率不满足滤波前提。


class InferenceError(Exception):
    """保存固定失败代码与可安全公开的摘要，不携带原始数据或异常正文。"""

    def __init__(self, code: str, safe_message: str) -> None:
        """创建不含输入路径和敏感内容的计算错误。"""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class InputError(InferenceError):
    """携带白名单输入原因并保留统一输入失败代码。"""

    def __init__(self, reason: InputErrorReason) -> None:
        """创建仅含受控原因的输入错误。"""
        if type(reason) is not InputErrorReason:
            raise TypeError("输入错误必须使用受控原因")
        self.reason = reason
        super().__init__("INFERENCE.INVALID_INPUT", "推理输入无效")


class UnknownAlgorithmError(InferenceError):
    """表示请求的算法标识没有受控可执行定义。"""

    def __init__(self) -> None:
        """创建固定摘要的未知算法错误。"""
        super().__init__("TASK.UNKNOWN_ALGORITHM", "算法不可用")


class QualityRejectedError(InferenceError):
    """携带经过校验的质量拒绝信息并使用稳定错误代码。"""

    def __init__(self, rejection: QualityRejection) -> None:
        """创建固定安全摘要且不信任算法自定义异常正文的拒绝。"""
        if not isinstance(rejection, QualityRejection):
            raise TypeError("质量拒绝异常必须携带受控拒绝数据")
        self.rejection = QualityRejection(rejection.reason, rejection.details)
        super().__init__("INFERENCE.QUALITY_REJECTED", "质量要求未达到")
