"""定义模型目录用例可映射为稳定 API 错误的异常。"""


class CatalogNotFound(Exception):
    """表示模型、版本或渠道资源不存在。"""


class CatalogConflict(Exception):
    """表示唯一身份冲突或已发布配置不可修改。"""


class CatalogConfigurationInvalid(Exception):
    """表示版本配置不符合受控算法、制品或指标契约。"""
