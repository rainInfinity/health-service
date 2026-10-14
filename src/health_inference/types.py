"""定义与 Django 无关的计算输入、输出和算法元数据。"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .exceptions import InferenceError

MetricValueType = Literal["number", "boolean"]


@dataclass(frozen=True, slots=True)
class MetricContract:
    """声明单个算法指标稳定的代码、值类型和单位。"""

    code: str  # 全局稳定的指标代码。
    value_type: MetricValueType  # 指标值类型：有限数值或布尔值。
    unit: str | None  # 数值单位；布尔值通常为空。

@dataclass(frozen=True, slots=True)
class InferenceInput:
    """保存单个任务已准备好的文件、资料快照及固定参数。"""

    files: Mapping[str, Path]  # 按算法逻辑名索引的任务输入文件。
    artifacts: Mapping[str, Path]  # 按逻辑名索引的模型制品文件。
    profile: Mapping[str, object]  # 提交时冻结的健康资料，不得读取实时资料替代。
    params: Mapping[str, object]  # 已发布模型版本固定的参数。
    output_dir: Path  # 当前任务与执行令牌专属的输出目录。


@dataclass(frozen=True, slots=True)
class InferenceOutput:
    """返回完整权威结果、可投影值和本次计算生成的文件。"""

    payload: Mapping[str, object]  # 完整结果；用于任务详情和历史解释。
    metrics: Mapping[str, int | float | bool]  # 算法声明的可查询指标值。
    output_files: Mapping[str, Path]  # 逻辑输出名到本次执行文件路径的映射。


ParameterValidator = Callable[[Mapping[str, object]], None]
ResultValidator = Callable[[InferenceOutput], None]
InferenceRunner = Callable[[InferenceInput], InferenceOutput]


@dataclass(frozen=True, slots=True)
class AlgorithmDefinition:
    """描述可执行算法的稳定输入、参数和结果契约。"""

    key: str  # 受控注册表中的稳定算法标识。
    is_example: bool  # 是否为非临床示例，由代码定义而非客户端参数决定。
    required_inputs: tuple[str, ...]  # 执行前必须存在的输入逻辑名。
    required_artifacts: tuple[str, ...]  # 执行前必须存在的制品逻辑名。
    required_profile_fields: tuple[str, ...]  # 快照中必须存在且非空的资料键。
    parameter_validator: ParameterValidator  # 校验发布版本的固定参数。
    result_validator: ResultValidator  # 校验完整输出及指标契约。
    runner: InferenceRunner  # 执行纯计算并返回本任务结果。
    metric_contracts: tuple[MetricContract, ...]  # 允许发布和返回的指标语义。

    def validate_params(self, params: Mapping[str, object]) -> None:
        """按算法定义校验发布版本参数。"""
        self.parameter_validator(params)

    def run(self, inference_input: InferenceInput) -> InferenceOutput:
        """验证输入边界，执行算法并校验完整结果。"""
        if not set(self.required_inputs).issubset(inference_input.files):
            raise InferenceError("INFERENCE.INVALID_INPUT", "缺少算法所需输入文件")
        if not set(self.required_artifacts).issubset(inference_input.artifacts):
            raise InferenceError("INFERENCE.INVALID_ARTIFACT", "缺少算法所需模型制品")
        if any(
            inference_input.profile.get(field) is None
            for field in self.required_profile_fields
        ):
            raise InferenceError("INFERENCE.MISSING_PROFILE", "计算所需资料缺失")

        self.validate_params(inference_input.params)
        output = self.runner(inference_input)
        if not isinstance(output, InferenceOutput):
            raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
        if (
            not isinstance(output.payload, Mapping)
            or not isinstance(output.metrics, Mapping)
            or not isinstance(output.output_files, Mapping)
        ):
            raise InferenceError("RESULT.INVALID_OUTPUT", "算法结果格式无效")
        for code, value in output.metrics.items():
            if not isinstance(code, str):
                raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
            contract = None
            for candidate in self.metric_contracts:
                if candidate.code == code:
                    contract = candidate
                    break
            if contract is None or not _metric_value_matches(contract, value):
                raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
        self.result_validator(output)
        return output


def _metric_value_matches(contract: MetricContract, value: object) -> bool:
    """校验结果指标的 Python 类型及数值有限性。"""
    if contract.value_type == "boolean":
        return type(value) is bool
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False
