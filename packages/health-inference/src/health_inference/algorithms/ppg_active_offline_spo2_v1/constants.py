"""定义血氧 v1 的固定窗口、门禁和滤波参数。"""

WINDOW_SIZE = 450  # 单个滑动窗口的样本数。
WINDOW_STEP = 75  # 相邻窗口起点间的样本数。
APPLY_DIFF = False  # 固定关闭窗口差分。
QUALITY_THRESHOLD = 70  # 实际质量分数低于此值时拒绝。
PPG_FILTER_LOW_HZ = 0.75  # 负红 PPG 带通低频截止。
PPG_FILTER_HIGH_HZ = 4.0  # 负红 PPG 带通高频截止。
