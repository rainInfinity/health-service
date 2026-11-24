"""验证独立 HR 与 BR v1 注册身份、固定元数据和惰性计算依赖。"""

import subprocess
import sys

import pytest
from health_inference import (
    PPG_ACTIVE_OFFLINE_HR_V1,
    PPG_ACTIVE_OFFLINE_BR_V1,
    get_algorithm,
)
from health_inference.exceptions import InferenceError, UnknownAlgorithmError


@pytest.mark.parametrize("definition,metric,unit", [
    (PPG_ACTIVE_OFFLINE_HR_V1, "hr", "beats_per_minute"),
    (PPG_ACTIVE_OFFLINE_BR_V1, "br", "breaths_per_minute"),
])
def test_formal_algorithm_is_registered_with_fixed_contract(definition, metric, unit):
    """两项独立定义声明非示例身份、零制品和各自唯一指标。"""
    assert get_algorithm(definition.key) is definition
    assert definition.key == f"ppg.active.offline.{metric}.v1"
    assert definition.is_example is False
    assert definition.required_inputs == ("ppg",)
    assert definition.required_artifacts == ()
    assert definition.required_profile_fields == ()
    assert tuple(
        (contract.code, contract.value_type, contract.unit)
        for contract in definition.metric_contracts
    ) == ((metric, "number", unit),)


@pytest.mark.parametrize("definition", [PPG_ACTIVE_OFFLINE_HR_V1, PPG_ACTIVE_OFFLINE_BR_V1])
def test_formal_algorithm_accepts_only_empty_fixed_parameters(definition):
    """固定版本接受空参数并拒绝任何非空参数映射。"""
    definition.validate_params({})

    with pytest.raises(InferenceError) as error:
        definition.validate_params({"filter_order": 3})

    assert error.value.code == "INFERENCE.INVALID_PARAMETERS"


@pytest.mark.parametrize("alias", [
    "hr_br", "ppg.active.offline.hr_br", "ppg.active.offline.hr_br.v1",
    "ppg.active.offline.all.v1",
])
def test_retired_algorithm_keys_and_aliases_are_not_registered(alias):
    """退役融合版本、旧场景及无版本别名均不能解析为可执行算法。"""
    with pytest.raises(UnknownAlgorithmError):
        get_algorithm(alias)


def test_registry_import_does_not_load_optional_computation_libraries(tmp_path):
    """注册正式算法时不导入 pandas、NumPy、SciPy 或 NeuroKit2。"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import health_inference; "
                "assert health_inference.get_algorithm("
                "'ppg.active.offline.hr.v1').is_example is False; "
                "assert health_inference.get_algorithm('ppg.active.offline.br.v1').is_example is False; "
                "assert not {'pandas', 'numpy', 'scipy', 'neurokit2'} & sys.modules.keys()"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
