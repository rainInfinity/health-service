"""定义与 Django 无关的计算输入、输出和算法元数据。"""

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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
    """保存任务文件、资料快照、可选客户端元数据及固定参数。"""

    files: Mapping[str, Path]  # 按算法逻辑名索引的任务输入文件。
    artifacts: Mapping[str, Path]  # 按逻辑名索引的模型制品文件。
    profile: Mapping[str, object]  # 提交时冻结的健康资料，不得读取实时资料替代。
    params: Mapping[str, object]  # 已发布模型版本固定的参数。
    output_dir: Path  # 当前任务与执行令牌专属的输出目录。
    client_metadata: Mapping[str, object] | None = None  # 可选客户端自定义信息；未提供时为空。


@dataclass(frozen=True, slots=True)
class InferenceOutput:
    """返回完整权威结果、可投影值和本次计算生成的文件。"""

    payload: Mapping[str, object]  # 完整结果；用于任务详情和历史解释。
    metrics: Mapping[str, int | float | bool]  # 算法声明的可查询指标值。
    output_files: Mapping[str, Path]  # 逻辑输出名到本次执行文件路径的映射。


ParameterValidator = Callable[[Mapping[str, object]], None]
ResultValidator = Callable[[InferenceOutput], None]
InferenceExecutor = Callable[[InferenceInput], InferenceOutput]


@dataclass(frozen=True, slots=True)
class VersionResources:
    """保存单个发布版本固定的参数和已解析制品路径。"""

    params: Mapping[str, object]  # 发布版本不可变参数。
    artifact_paths: Mapping[str, Path]  # 制品逻辑名到已解析文件路径。


VersionResourceFactory = Callable[[VersionResources], InferenceExecutor]


def _freeze_extensions(
    extensions_by_name: Mapping[str, frozenset[str]],
) -> Mapping[str, frozenset[str]]:
    """将算法声明的文件后缀映射冻结为只读结构。"""
    if not isinstance(extensions_by_name, Mapping):
        raise TypeError("算法文件格式声明必须是映射")
    frozen: dict[str, frozenset[str]] = {}
    for logical_name, extensions in extensions_by_name.items():
        if not isinstance(logical_name, str) or isinstance(extensions, str):
            raise TypeError("算法文件格式声明无效")
        try:
            frozen[logical_name] = frozenset(extensions)
        except TypeError as error:
            raise TypeError("算法文件格式声明无效") from error
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class AlgorithmRuntime:
    """绑定固定版本资源并在每次任务调用中校验算法契约。"""

    definition: "AlgorithmDefinition"  # 负责该执行器的稳定契约。
    resources: VersionResources  # 只含版本参数和制品路径，不含任务数据。
    executor: InferenceExecutor  # 可复用执行器，不保存单次任务输入。

    def run(self, inference_input: InferenceInput) -> InferenceOutput:
        """校验绑定版本与本次任务输入，执行并校验完整结果。"""
        if not set(self.definition.required_inputs).issubset(inference_input.files):
            raise InferenceError("INFERENCE.INVALID_INPUT", "缺少算法所需输入文件")
        if inference_input.artifacts != self.resources.artifact_paths:
            raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品与固定版本资源不匹配")
        if inference_input.params != self.resources.params:
            raise InferenceError("INFERENCE.INVALID_PARAMETERS", "任务参数与固定版本资源不匹配")
        if any(
            inference_input.profile.get(field) is None
            for field in self.definition.required_profile_fields
        ):
            raise InferenceError("INFERENCE.MISSING_PROFILE", "计算所需资料缺失")

        self.definition.validate_params(inference_input.params)
        output = self.executor(inference_input)
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
            for candidate in self.definition.metric_contracts:
                if candidate.code == code:
                    contract = candidate
                    break
            if contract is None or not _metric_value_matches(contract, value):
                raise InferenceError("RESULT.INVALID_OUTPUT", "算法指标格式无效")
        self.definition.result_validator(output)
        return output


@dataclass(frozen=True, slots=True)
class AlgorithmDefinition:
    """描述算法稳定契约，并为每个发布版本创建绑定资源。"""

    key: str  # 受控注册表中的稳定算法标识。
    is_example: bool  # 是否为非临床示例，由代码定义而非客户端参数决定。
    required_inputs: tuple[str, ...]  # 执行前必须存在的输入逻辑名。
    required_artifacts: tuple[str, ...]  # 版本必须绑定的制品逻辑名。
    input_extensions: Mapping[str, frozenset[str]]  # 输入逻辑名到允许的小写后缀集合，不含点号。
    artifact_extensions: Mapping[str, frozenset[str]]  # 制品逻辑名到允许的小写后缀集合，不含点号。
    required_profile_fields: tuple[str, ...]  # 快照中必须存在且非空的资料键。
    parameter_validator: ParameterValidator  # 校验发布版本的固定参数。
    result_validator: ResultValidator  # 校验完整输出及指标契约。
    resource_factory: VersionResourceFactory  # 只接收固定参数和已解析制品路径。
    metric_contracts: tuple[MetricContract, ...]  # 允许发布和返回的指标语义。

    def __post_init__(self) -> None:
        """冻结算法输入与制品的后缀声明。"""
        object.__setattr__(self, "input_extensions", _freeze_extensions(self.input_extensions))
        object.__setattr__(self, "artifact_extensions", _freeze_extensions(self.artifact_extensions))

    def validate_params(self, params: Mapping[str, object]) -> None:
        """按算法定义校验发布版本参数。"""
        try:
            self.parameter_validator(params)
        except (TypeError, ValueError) as error:
            raise InferenceError("INFERENCE.INVALID_PARAMETERS", "模型参数无效") from error

    def create_runtime(self, resources: VersionResources) -> AlgorithmRuntime:
        """校验并创建只绑定一个发布版本的可复用执行资源。"""
        if set(resources.artifact_paths) != set(self.required_artifacts):
            raise InferenceError("INFERENCE.INVALID_ARTIFACT", "模型制品配置无效")
        self.validate_params(resources.params)
        fixed_resources = VersionResources(
            params=MappingProxyType(deepcopy(dict(resources.params))),
            artifact_paths=MappingProxyType(dict(resources.artifact_paths)),
        )
        executor = self.resource_factory(fixed_resources)
        return AlgorithmRuntime(
            definition=self,
            resources=fixed_resources,
            executor=executor,
        )


def _metric_value_matches(contract: MetricContract, value: object) -> bool:
    """校验结果指标的 Python 类型及数值有限性。"""
    if contract.value_type == "boolean":
        return type(value) is bool
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False
