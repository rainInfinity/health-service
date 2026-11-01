"""验证血氧 v1 的正式注册标识和固定参数契约。"""

import pytest
from health_inference import (
    PPG_ACTIVE_OFFLINE_SPO2_V1,
    get_algorithm,
    list_algorithms,
)
from health_inference.algorithms.ppg_active_offline_spo2_v1.algorithm import (
    ALGORITHM_KEY,
)
from health_inference.exceptions import InferenceError


def test_spo2_v1_is_registered_only_with_its_full_version_key():
    """验证正式算法使用完整版本标识并声明输入、制品和指标契约。"""
    assert ALGORITHM_KEY == "ppg.active.offline.spo2.v1"
    assert get_algorithm(ALGORITHM_KEY) is PPG_ACTIVE_OFFLINE_SPO2_V1
    assert PPG_ACTIVE_OFFLINE_SPO2_V1.is_example is False
    assert PPG_ACTIVE_OFFLINE_SPO2_V1.required_inputs == ("ppg",)
    assert PPG_ACTIVE_OFFLINE_SPO2_V1.required_artifacts == (
        "spo2",
        "feature_names",
        "stable_features",
    )
    assert PPG_ACTIVE_OFFLINE_SPO2_V1.metric_contracts[0].code == "spo2"
    assert PPG_ACTIVE_OFFLINE_SPO2_V1.metric_contracts[0].unit == "percent"
    assert all(definition.key != "ppg.active.offline.spo2" for definition in list_algorithms())


def test_spo2_v1_rejects_non_empty_parameters():
    """验证发布参数不能改变固定的血氧计算规则。"""
    PPG_ACTIVE_OFFLINE_SPO2_V1.validate_params({})

    with pytest.raises(InferenceError) as invalid_parameters:
        PPG_ACTIVE_OFFLINE_SPO2_V1.validate_params({"window_size": 300})

    assert invalid_parameters.value.code == "INFERENCE.INVALID_PARAMETERS"
