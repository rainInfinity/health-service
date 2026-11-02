"""提供仅供集成测试使用的最小算法定义。"""

import os
import signal
from collections.abc import Mapping
from pathlib import Path
from time import sleep
from uuid import UUID

from health_inference import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
    QualityRejectedError,
    QualityRejection,
    register_algorithm,
)
from health_service.infrastructure.inference_adapter import (
    run_task_inference as _run_task_inference,
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
        input_extensions={"ppg": frozenset({"csv"})},
        artifact_extensions={},
        required_profile_fields=(),
        parameter_validator=_allow_parameters,
        result_validator=_validate_result,
        resource_factory=lambda _: _run_completed,
        metric_contracts=(),
    )
)
register_algorithm(
    AlgorithmDefinition(
        key=TEST_METRIC_ALGORITHM_KEY,
        is_example=False,
        required_inputs=("ppg",),
        required_artifacts=(),
        input_extensions={"ppg": frozenset({"csv"})},
        artifact_extensions={},
        required_profile_fields=(),
        parameter_validator=_allow_parameters,
        result_validator=_validate_result,
        resource_factory=lambda _: _run_completed_with_metric,
        metric_contracts=(MetricContract(TEST_METRIC_CODE, "number", "count"),),
    )
)


def run_test_task_inference(*, task_id: int, execution_token: UUID):
    """在隔离子进程中调用真实任务适配器。"""
    return _run_task_inference(task_id=task_id, execution_token=execution_token)


def run_delayed_test_task_inference(
    delay_seconds: float,
    *,
    task_id: int,
    execution_token: UUID,
):
    """延迟指定时间后调用真实适配器。"""
    sleep(delay_seconds)
    return _run_task_inference(task_id=task_id, execution_token=execution_token)


def run_test_task_inference_after_parent_signal(
    *,
    task_id: int,
    execution_token: UUID,
):
    """向父消费者发送 SIGTERM 后执行当前任务。"""
    os.kill(os.getppid(), signal.SIGTERM)
    return _run_task_inference(task_id=task_id, execution_token=execution_token)


def run_test_task_inference_recording_pid(
    process_ids,
    *,
    task_id: int,
    execution_token: UUID,
):
    """记录执行子进程标识后调用真实适配器。"""
    process_ids.put(os.getpid())
    return _run_task_inference(task_id=task_id, execution_token=execution_token)


def run_test_task_inference_until_timeout(
    task_root: Path,
    marker: Path,
    *,
    task_id: int,
    execution_token: UUID,
) -> None:
    """写入隔离部分文件后持续等待，由父进程硬终止。"""
    execution_dir = (
        task_root / ".executions" / str(task_id) / execution_token.hex
    )
    execution_dir.mkdir(parents=True)
    (execution_dir / "partial-output.csv").write_text("partial", encoding="utf-8")
    marker.write_text(
        f"{os.getpid()} {execution_token.hex}",
        encoding="ascii",
    )
    while True:
        sleep(0.02)


def raise_test_quality_rejection(
    rejection: QualityRejection,
    **_kwargs: object,
) -> None:
    """抛出受控质量拒绝并注入仅用于泄露检查的私有哨兵。"""
    error = QualityRejectedError(rejection)
    error.args = ("PRIVATE-QUALITY-SENTINEL",)
    error.safe_message = "PRIVATE-QUALITY-SENTINEL"
    raise error


def raise_test_quality_rejection_after_delay(
    delay_seconds: float,
    rejection: QualityRejection,
    **_kwargs: object,
) -> None:
    """延迟指定时间后抛出受控质量拒绝。"""
    sleep(delay_seconds)
    raise_test_quality_rejection(rejection)


def raise_test_unexpected_inference(**_kwargs: object) -> None:
    """标记终态恢复路径错误地启动了算法执行。"""
    raise AssertionError("终态任务不得重新执行")
