"""维护进程内显式注册的纯 Python 算法定义。"""

import re

from .exceptions import UnknownAlgorithmError
from .types import AlgorithmDefinition

_ALGORITHM_KEY = re.compile(r"[a-z][a-z0-9_.-]*\Z")
_ALGORITHMS: dict[str, AlgorithmDefinition] = {}


def register_algorithm(definition: AlgorithmDefinition) -> None:
    """注册代码显式提供的算法定义，拒绝无效或重复标识。"""
    if not _ALGORITHM_KEY.fullmatch(definition.key):
        raise ValueError("算法标识格式无效")
    if definition.key in _ALGORITHMS:
        raise ValueError("算法标识已注册")
    _ALGORITHMS[definition.key] = definition


def get_algorithm(key: str) -> AlgorithmDefinition:
    """根据稳定标识查找已注册算法，不从数据库导入代码。"""
    try:
        return _ALGORITHMS[key]
    except KeyError as error:
        raise UnknownAlgorithmError() from error


def list_algorithms() -> tuple[AlgorithmDefinition, ...]:
    """返回按稳定标识排序的已注册算法定义。"""
    return tuple(_ALGORITHMS[key] for key in sorted(_ALGORITHMS))
