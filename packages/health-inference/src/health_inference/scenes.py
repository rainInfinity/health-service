"""编排两个固定离线场景，不扩展为可配置工作流。"""

import shutil
import stat
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .algorithms.ppg_acc_active_offline_bp_v1 import PPG_ACC_ACTIVE_OFFLINE_BP_V1
from .algorithms.ppg_active_offline_diabetes_v1 import PPG_ACTIVE_OFFLINE_DIABETES_V1
from .algorithms.ppg_active_offline_hr_v1 import PPG_ACTIVE_OFFLINE_HR_V1
from .algorithms.ppg_active_offline_br_v1 import PPG_ACTIVE_OFFLINE_BR_V1
from .algorithms.ppg_active_offline_spo2_v1 import PPG_ACTIVE_OFFLINE_SPO2_V1
from .exceptions import InferenceError
from .failures import is_safe_failure, safe_failure_fields
from .types import (
    AlgorithmDefinition,
    AlgorithmRuntime,
    InferenceExecutor,
    InferenceInput,
    InferenceOutput,
    VersionResources,
)

_MEMBER_FIELDS = frozenset({
    "algorithmKey", "status", "result", "failureCode", "failureMessage", "qualityFailure",
})


def is_scene_algorithm(key: str) -> bool:
    """只识别本次显式支持的两个场景标识。"""
    return key in (PPG_SCENE.key, PPG_ACC_SCENE.key)


def _invalid_output() -> InferenceError:
    """统一报告场景结构或文件边界错误。"""
    return InferenceError("RESULT.INVALID_OUTPUT", "场景结果格式无效")


def _empty_parameters(params: Mapping[str, object]) -> None:
    """固定场景不接受自定义参数。"""
    if params:
        raise InferenceError("INFERENCE.INVALID_PARAMETERS", "该算法版本不接受参数")


def _plain_directory(path: Path) -> None:
    """拒绝目录及其祖先中的符号链接，不解析后再丢弃原路径。"""
    if not path.is_absolute() or ".." in path.parts:
        raise _invalid_output()
    for ancestor in (path, *path.parents):
        if not stat.S_ISDIR(ancestor.lstat().st_mode):
            raise _invalid_output()


def _directory_identity(path: Path) -> tuple[int, int]:
    """保留本次创建目录的物理身份，拒绝清理被替换的目录。"""
    _plain_directory(path)
    identity = path.lstat()
    return identity.st_dev, identity.st_ino


def _member_files(files: Mapping[str, Path], directory: Path) -> None:
    """只接受该成员目录中本次生成的普通、非链接文件。"""
    try:
        _plain_directory(directory)
        for path in files.values():
            if not isinstance(path, Path) or path.parent != directory or ".." in path.parts:
                raise _invalid_output()
            file_stat = path.lstat()
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise _invalid_output()
        if set(directory.iterdir()) != set(files.values()):
            raise _invalid_output()
    except OSError as error:
        raise _invalid_output() from error


def _clean_failed_directory(
    directory: Path,
    root: Path,
    directory_identity: tuple[int, int],
    root_identity: tuple[int, int],
) -> None:
    """只清理已由本次场景创建且仍可证明隔离的成员目录。"""
    try:
        if (
            directory.parent != root
            or _directory_identity(root) != root_identity
            or _directory_identity(directory) != directory_identity
        ):
            raise _invalid_output()
        # 不追随任何链接，也不依据成员返回的路径决定删除范围。
        for descendant in directory.rglob("*"):
            mode = descendant.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise _invalid_output()
        shutil.rmtree(directory)
        if directory.exists() or directory.is_symlink():
            raise _invalid_output()
    except Exception as error:
        raise _invalid_output() from error


def _outcome(success_count: int, total: int) -> str:
    """仅从成员终态推导测量结局。"""
    if success_count == total:
        return "ALL_SUCCEEDED"
    return "PARTIALLY_SUCCEEDED" if success_count else "ALL_FAILED"


@dataclass(frozen=True, slots=True)
class SceneMember:
    """固定成员名和完整实现修订。"""

    name: str  # 固定成员名，同时限定输出子目录和逻辑名前缀。
    definition: AlgorithmDefinition  # 固定的完整成员算法修订及其原契约。


@dataclass(frozen=True, slots=True)
class OfflineScene:
    """只保存固定声明，所有任务中间数据均保留在调用栈内。"""

    key: str  # 场景完整算法标识，不随渠道选择改变。
    members: tuple[SceneMember, ...]  # 按实际调用顺序冻结的成员集合。

    def algorithm_definition(self) -> AlgorithmDefinition:
        """复用成员契约构造并集，拒绝制品、指标及输入格式冲突。"""
        inputs: dict[str, frozenset[str]] = {}
        artifacts: dict[str, frozenset[str]] = {}
        metrics = {}
        names: set[str] = set()
        for member in self.members:
            definition = member.definition
            if (
                not member.name.isidentifier()
                or member.name in names
                or definition.required_profile_fields
            ):
                raise ValueError("场景成员声明冲突")
            names.add(member.name)
            definition.validate_params({})
            for name, extensions in definition.input_extensions.items():
                if name in inputs and inputs[name] != extensions:
                    raise ValueError("场景输入格式冲突")
                inputs[name] = extensions
            for name, extensions in definition.artifact_extensions.items():
                if name in artifacts:
                    raise ValueError("场景制品逻辑名冲突")
                artifacts[name] = extensions
            for contract in definition.metric_contracts:
                if contract.code in metrics:
                    raise ValueError("场景指标代码冲突")
                metrics[contract.code] = contract
        if not self.members:
            raise ValueError("场景成员不能为空")
        return AlgorithmDefinition(
            key=self.key,
            is_example=False,
            required_inputs=tuple(inputs),
            required_artifacts=tuple(artifacts),
            input_extensions=inputs,
            artifact_extensions=artifacts,
            required_profile_fields=(),
            parameter_validator=_empty_parameters,
            result_validator=self.validate_output,
            resource_factory=self.create_executor,
            metric_contracts=tuple(metrics.values()),
        )

    def create_executor(self, resources: VersionResources) -> InferenceExecutor:
        """仅缓存成功初始化的成员资源；失败后由下一任务重新准备。"""
        runtimes: dict[str, AlgorithmRuntime] = {}

        def execute(inference_input: InferenceInput) -> InferenceOutput:
            """顺序执行完整成员运行时，并隔离成员错误与失败文件。"""
            root = inference_input.output_dir.absolute()
            root_identity = _directory_identity(root)
            if any(root.iterdir()):
                raise _invalid_output()
            results = {}
            metrics = {}
            files = {}
            successful_directories: dict[Path, tuple[int, int]] = {}
            for member in self.members:
                directory = root / member.name
                # 存储准备失败属于场景级错误；不清理并非本次创建的目录。
                directory.mkdir()
                directory_identity = _directory_identity(directory)
                try:
                    definition = member.definition
                    runtime = runtimes.get(member.name)
                    if runtime is None:
                        runtime = definition.create_runtime(VersionResources(
                            params={},
                            artifact_paths={
                                name: resources.artifact_paths[name]
                                for name in definition.required_artifacts
                            },
                        ))
                        runtimes[member.name] = runtime
                    member_input = InferenceInput(
                        files={name: inference_input.files[name] for name in definition.required_inputs},
                        artifacts=dict(runtime.resources.artifact_paths),
                        profile=deepcopy(dict(inference_input.profile)),
                        params={},
                        output_dir=directory,
                        client_metadata=(
                            deepcopy(dict(inference_input.client_metadata))
                            if inference_input.client_metadata is not None else None
                        ),
                    )
                    output = runtime.run(member_input)
                    _member_files(output.output_files, directory)
                except Exception as error:  # noqa: BLE001  # 成员错误须安全化后继续，不捕获进程级中断。
                    _clean_failed_directory(directory, root, directory_identity, root_identity)
                    code, message, detail = safe_failure_fields(
                        error if isinstance(error, InferenceError) else None,
                    )
                    results[member.name] = {
                        "algorithmKey": member.definition.key,
                        "status": "FAILED",
                        "result": None,
                        "failureCode": code,
                        "failureMessage": message,
                        "qualityFailure": detail["quality"] if detail is not None else None,
                    }
                    continue
                results[member.name] = {
                    "algorithmKey": member.definition.key,
                    "status": "SUCCEEDED",
                    "result": output.payload,
                    "failureCode": None,
                    "failureMessage": None,
                    "qualityFailure": None,
                }
                metrics.update(output.metrics)
                files.update({f"{member.name}_{name}": path for name, path in output.output_files.items()})
                successful_directories[directory] = directory_identity
            # 正式发布链接整棵树，不能只验证返回字典而放过额外产物。
            if (
                _directory_identity(root) != root_identity
                or set(root.iterdir()) != set(successful_directories)
                or any(
                    _directory_identity(path) != identity
                    for path, identity in successful_directories.items()
                )
            ):
                raise _invalid_output()
            output = InferenceOutput(
                payload={
                    "outcome": _outcome(len(successful_directories), len(self.members)),
                    "results": results,
                },
                metrics=metrics,
                output_files=files,
            )
            self.validate_output(output)
            return output

        return execute

    def validate_output(self, output: InferenceOutput) -> None:
        """重建原成员输出校验完整汇总；此处错误不得再降级为成员失败。"""
        try:
            self._validate_output(output)
        except Exception as error:
            raise _invalid_output() from error

    def _validate_output(self, output: InferenceOutput) -> None:
        """核对六字段、成员载荷、精确指标及实际文件集合。"""
        if (
            not isinstance(output.payload, Mapping)
            or set(output.payload) != {"outcome", "results"}
            or not isinstance(output.metrics, Mapping)
            or not isinstance(output.output_files, Mapping)
        ):
            raise _invalid_output()
        results = output.payload["results"]
        if not isinstance(results, Mapping) or set(results) != {member.name for member in self.members}:
            raise _invalid_output()
        expected_metrics = set()
        expected_files = set()
        directories = set()
        roots = set()
        success_count = 0
        for member in self.members:
            result = results[member.name]
            if (
                not isinstance(result, Mapping)
                or set(result) != _MEMBER_FIELDS
                or result["algorithmKey"] != member.definition.key
            ):
                raise _invalid_output()
            if result["status"] == "FAILED":
                if result["result"] is not None or not is_safe_failure(
                    result["failureCode"], result["failureMessage"], result["qualityFailure"],
                ):
                    raise _invalid_output()
                continue
            if (
                result["status"] != "SUCCEEDED"
                or not isinstance(result["result"], Mapping)
                or "output_files" in result["result"]
                or any(result[name] is not None for name in ("failureCode", "failureMessage", "qualityFailure"))
            ):
                raise _invalid_output()
            success_count += 1
            codes = {contract.code for contract in member.definition.metric_contracts}
            member_metrics = {code: output.metrics[code] for code in codes}
            prefix = f"{member.name}_"
            member_files = {
                name[len(prefix):]: path
                for name, path in output.output_files.items()
                if isinstance(name, str) and name.startswith(prefix)
            }
            member.definition.result_validator(InferenceOutput(
                payload=result["result"], metrics=member_metrics, output_files=member_files,
            ))
            member_directories = {path.parent for path in member_files.values()}
            if len(member_directories) != 1:
                raise _invalid_output()
            directory = member_directories.pop()
            if directory.name != member.name:
                raise _invalid_output()
            _member_files(member_files, directory)
            directories.add(directory)
            roots.add(directory.parent)
            expected_metrics.update(codes)
            expected_files.update(f"{prefix}{name}" for name in member_files)
        if (
            output.payload["outcome"] != _outcome(success_count, len(self.members))
            or set(output.metrics) != expected_metrics
            or set(output.output_files) != expected_files
            or len(roots) > 1
        ):
            raise _invalid_output()
        if roots:
            root = roots.pop()
            _plain_directory(root)
            if set(root.iterdir()) != directories:
                raise _invalid_output()


PPG_SCENE = OfflineScene("ppg.active.offline.all.v2", (
    SceneMember("hr", PPG_ACTIVE_OFFLINE_HR_V1),
    SceneMember("br", PPG_ACTIVE_OFFLINE_BR_V1),
    SceneMember("spo2", PPG_ACTIVE_OFFLINE_SPO2_V1),
    SceneMember("diabetes", PPG_ACTIVE_OFFLINE_DIABETES_V1),
))
PPG_ACC_SCENE = OfflineScene("ppg_acc.active.offline.all.v1", (
    SceneMember("bp", PPG_ACC_ACTIVE_OFFLINE_BP_V1),
))
