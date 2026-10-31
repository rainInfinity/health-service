"""验证 HR/BR v1 注册身份、固定元数据和惰性计算依赖。"""

import subprocess
import sys

import pytest
from health_inference import (
    PPG_ACTIVE_OFFLINE_HR_BR_V1,
    get_algorithm,
)
from health_inference.algorithms.ppg_active_offline_hr_br_v1.algorithm import ALGORITHM_KEY
from health_inference.exceptions import InferenceError, UnknownAlgorithmError


def test_formal_algorithm_is_registered_with_fixed_contract():
    """正式定义声明非示例身份、零制品资料和固定 HR/BR 单位。"""
    definition = get_algorithm(ALGORITHM_KEY)

    assert definition is PPG_ACTIVE_OFFLINE_HR_BR_V1
    assert definition.key == "ppg.active.offline.hr_br.v1"
    assert definition.is_example is False
    assert definition.required_inputs == ("ppg",)
    assert definition.required_artifacts == ()
    assert definition.required_profile_fields == ()
    assert tuple(
        (contract.code, contract.value_type, contract.unit)
        for contract in definition.metric_contracts
    ) == (
        ("hr", "number", "beats_per_minute"),
        ("br", "number", "breaths_per_minute"),
    )


def test_formal_algorithm_accepts_only_empty_fixed_parameters():
    """固定版本接受空参数并拒绝任何非空参数映射。"""
    PPG_ACTIVE_OFFLINE_HR_BR_V1.validate_params({})

    with pytest.raises(InferenceError) as error:
        PPG_ACTIVE_OFFLINE_HR_BR_V1.validate_params({"filter_order": 3})

    assert error.value.code == "INFERENCE.INVALID_PARAMETERS"


@pytest.mark.parametrize("alias", ["hr_br", "ppg.active.offline.hr_br"])
def test_unversioned_algorithm_aliases_are_not_registered(alias):
    """旧模板名和无修订号正式名均不能解析为算法定义。"""
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
                "'ppg.active.offline.hr_br.v1').is_example is False; "
                "assert not {'pandas', 'numpy', 'scipy', 'neurokit2'} & sys.modules.keys()"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
