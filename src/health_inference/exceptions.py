"""定义可安全跨越推理边界的稳定异常。"""


class InferenceError(Exception):
    """保存固定失败代码与可安全公开的摘要，不携带原始数据或异常正文。"""

    def __init__(self, code: str, safe_message: str) -> None:
        """创建不含输入路径和敏感内容的计算错误。"""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UnknownAlgorithmError(InferenceError):
    """表示请求的算法标识没有受控可执行定义。"""

    def __init__(self) -> None:
        """创建固定摘要的未知算法错误。"""
        super().__init__("TASK.UNKNOWN_ALGORITHM", "算法不可用")
