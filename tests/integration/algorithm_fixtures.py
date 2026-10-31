"""提供仅供集成测试使用的最小算法定义。"""

from collections.abc import Mapping

from health_inference import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
    register_algorithm,
)

TEST_ALGORITHM_KEY = "test.integration.completed"
TEST_METRIC_ALGORITHM_KEY = "test.integration.completed.metric"
TEST_METRIC_CODE = "test.integration.completed_count"


def _allow_parameters(_: Mapping[str, object]) -> None:
    """允许目录生命周期测试保存任意版本参数。"""


def _validate_result(_: InferenceOutput) -> None:
    """由通用输出契约检查测试结果结构。"""


def _run_completed(_: InferenceInput) -> InferenceOutput:
    """返回用于验证任务生命周期的固定完成结果。"""
    return InferenceOutput(payload={"completed": True}, metrics={}, output_files={})


def _run_completed_with_metric(_: InferenceInput) -> InferenceOutput:
    """返回一个测试专用的可投影完成计数。"""
    return InferenceOutput(
        payload={"completed": True},
        metrics={TEST_METRIC_CODE: 1.0},
        output_files={},
    )


register_algorithm(
    AlgorithmDefinition(
        key=TEST_ALGORITHM_KEY,
        is_example=False,
        required_inputs=("ppg",),
        required_artifacts=(),
        required_profile_fields=(),
        parameter_validator=_allow_parameters,
        result_validator=_validate_result,
        runner=_run_completed,
        metric_contracts=(),
    )
)
register_algorithm(
    AlgorithmDefinition(
        key=TEST_METRIC_ALGORITHM_KEY,
        is_example=False,
        required_inputs=("ppg",),
        required_artifacts=(),
        required_profile_fields=(),
        parameter_validator=_allow_parameters,
        result_validator=_validate_result,
        runner=_run_completed_with_metric,
        metric_contracts=(MetricContract(TEST_METRIC_CODE, "number", "count"),),
    )
)
