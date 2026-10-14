"""导入并注册仓库内置的纯 Python 示例算法。"""

from health_inference.algorithms.hr_br.example import HR_BR_EXAMPLE
from health_inference.registry import register_algorithm

register_algorithm(HR_BR_EXAMPLE)
