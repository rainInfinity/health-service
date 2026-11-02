"""导入并注册仓库内置的正式算法。"""

from health_inference.algorithms.ppg_acc_active_offline_bp_v1 import (
    PPG_ACC_ACTIVE_OFFLINE_BP_V1,
)
from health_inference.algorithms.ppg_active_offline_diabetes_v1 import (
    PPG_ACTIVE_OFFLINE_DIABETES_V1,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1 import (
    PPG_ACTIVE_OFFLINE_HR_BR_V1,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1 import (
    PPG_ACTIVE_OFFLINE_SPO2_V1,
)
from health_inference.registry import register_algorithm

register_algorithm(PPG_ACTIVE_OFFLINE_HR_BR_V1)
register_algorithm(PPG_ACC_ACTIVE_OFFLINE_BP_V1)
register_algorithm(PPG_ACTIVE_OFFLINE_DIABETES_V1)
register_algorithm(PPG_ACTIVE_OFFLINE_SPO2_V1)
