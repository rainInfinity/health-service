"""验证真实成员计算、故障继续、版本资源恢复和单任务数据隔离。"""

import math
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace

import pytest
from health_inference import (
    PPG_ACC_ACTIVE_OFFLINE_ALL_V1,
    PPG_ACTIVE_OFFLINE_ALL_V2,
    InferenceInput,
    VersionResources,
)
from health_inference.algorithms.ppg_active_offline_br_v1.algorithm import _PIPELINE as BR_PIPELINE
from health_inference.algorithms.ppg_active_offline_hr_v1.algorithm import _PIPELINE as HR_PIPELINE
from health_inference.scenes import PPG_ACC_SCENE, PPG_SCENE


@pytest.mark.parametrize("bp", [False, True])
def test_scene_matches_independent_members_and_original_file_bytes(tmp_path, scene_case, bp):
    """同输入、同资源的场景输出逐成员等于独立完整调用，原表头和字节不改。"""
    scene = PPG_ACC_SCENE if bp else PPG_SCENE
    definition = PPG_ACC_ACTIVE_OFFLINE_ALL_V1 if bp else PPG_ACTIVE_OFFLINE_ALL_V2
    resources = scene_case.resources(bp=bp)
    source = scene_case.input(tmp_path / "scene", bp=bp)
    output = definition.create_runtime(resources).run(source)

    assert output.payload["outcome"] == "ALL_SUCCEEDED"
    assert set(output.payload["results"]) == {member.name for member in scene.members}
    expected_metrics = {}
    for member in scene.members:
        member_resources = VersionResources({}, {
            name: resources.artifact_paths[name] for name in member.definition.required_artifacts
        })
        independent_dir = tmp_path / f"independent-{member.name}"
        independent_dir.mkdir()
        independent = member.definition.create_runtime(member_resources).run(InferenceInput(
            files={name: source.files[name] for name in member.definition.required_inputs},
            artifacts=member_resources.artifact_paths,
            profile=deepcopy(source.profile),
            params={},
            output_dir=independent_dir,
            client_metadata=deepcopy(source.client_metadata),
        ))
        result = output.payload["results"][member.name]
        assert result["result"] == independent.payload
        assert result["status"] == "SUCCEEDED"
        expected_metrics.update(independent.metrics)
        for name, path in independent.output_files.items():
            combined = output.output_files[f"{member.name}_{name}"]
            assert combined.read_bytes() == path.read_bytes()
            assert combined.parent == source.output_dir / member.name
    assert output.metrics == expected_metrics


def test_middle_member_execution_failure_continues_and_cleans_written_files(
    tmp_path, scene_case, scene_factory,
):
    """中间成员完成计算后抛错，后续仍实际计算且失败产物不留在发布树。"""
    original = PPG_SCENE.members[2].definition.resource_factory

    def create(resources):
        """在真实血氧输出写成后注入执行故障。"""
        executor = original(resources)

        def fail_after_output(value):
            """先写真实处理文件，再模拟不可公开的底层异常。"""
            executor(value)
            raise RuntimeError("PRIVATE_RAW_DATA /private/server/model.onnx")

        return fail_after_output

    definition = scene_factory(spo2=create)
    source = scene_case.input(tmp_path / "execution")
    output = definition.create_runtime(scene_case.resources()).run(source)

    assert output.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert output.payload["results"]["diabetes"]["status"] == "SUCCEEDED"
    assert output.metrics["diabetes_probability"] == output.payload["results"]["diabetes"]["result"]["prob"]
    assert output.payload["results"]["spo2"]["result"] is None
    assert output.payload["results"]["spo2"]["failureCode"] == "INFERENCE.EXECUTION_ERROR"
    assert "PRIVATE_RAW_DATA" not in repr(output.payload)
    assert "spo2" not in output.metrics
    assert not (source.output_dir / "spo2").exists()
    assert {path.name for path in source.output_dir.iterdir()} == {"hr", "br", "diabetes"}


def test_missing_member_initialization_recovers_on_next_task_and_success_is_cached(tmp_path, scene_case):
    """缺失制品只隔离对应成员，恢复后不继承失败，已加载资源不重复读盘。"""
    diabetes_path = scene_case.ppg_artifacts["diabetes"]
    saved_model = diabetes_path.read_bytes()
    diabetes_path.unlink()
    runtime = PPG_ACTIVE_OFFLINE_ALL_V2.create_runtime(scene_case.resources())

    first = runtime.run(scene_case.input(tmp_path / "first"))
    assert first.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert first.payload["results"]["diabetes"]["failureCode"] == "INFERENCE.INVALID_ARTIFACT"
    assert set(first.metrics) == {"hr", "br", "spo2"}

    diabetes_path.write_bytes(saved_model)
    second = runtime.run(scene_case.input(tmp_path / "second"))
    assert second.payload["outcome"] == "ALL_SUCCEEDED"
    diabetes_path.unlink()
    third = runtime.run(scene_case.input(tmp_path / "third"))
    assert third.payload == second.payload
    assert third.metrics == second.metrics
    assert all(path.parent.parent == tmp_path / "third" for path in third.output_files.values())


@pytest.mark.parametrize("bp", [False, True])
def test_all_failed_contains_no_metrics_files_or_residual_directories(tmp_path, scene_case, bp):
    """真实 CSV 解析拒绝形成完整全失败汇总，不生成默认指标或文件。"""
    invalid = tmp_path / "invalid.csv"
    invalid.write_text("private_input_column\nPRIVATE_HEALTH_DATA\n", encoding="utf-8")
    definition = PPG_ACC_ACTIVE_OFFLINE_ALL_V1 if bp else PPG_ACTIVE_OFFLINE_ALL_V2
    source = scene_case.input(tmp_path / "execution", bp=bp, ppg=invalid)
    output = definition.create_runtime(scene_case.resources(bp=bp)).run(source)

    assert output.payload["outcome"] == "ALL_FAILED"
    assert output.metrics == {}
    assert output.output_files == {}
    assert list(source.output_dir.iterdir()) == []
    for result in output.payload["results"].values():
        assert result["status"] == "FAILED"
        assert result["result"] is None
        assert result["failureCode"] == "INFERENCE.INVALID_INPUT"
    assert "PRIVATE_HEALTH_DATA" not in repr(output.payload)


@pytest.mark.parametrize("member,pipeline,survivor,file_name", [
    ("hr", HR_PIPELINE, "br", "br_processed_rsp"),
    ("br", BR_PIPELINE, "hr", "hr_processed_ppg"),
])
def test_non_finite_rate_preserves_other_successful_rate_member(
    tmp_path, scene_case, scene_factory, member, pipeline, survivor, file_name,
):
    """真实门禁后某项速率非有限只丢弃该成员，另一速率数值及处理文件保留。"""
    invalid_pipeline = replace(pipeline, predict=lambda prepared: math.inf)
    definition = scene_factory(**{member: lambda resources: invalid_pipeline})
    source = scene_case.input(tmp_path / "execution")
    output = definition.create_runtime(scene_case.resources()).run(source)

    assert output.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert output.payload["results"][member]["failureCode"] == "RESULT.INVALID_OUTPUT"
    assert output.payload["results"][member]["result"] is None
    independent = output.payload["results"][survivor]
    assert independent["status"] == "SUCCEEDED"
    assert independent["result"][survivor] == output.metrics[survivor]
    assert output.output_files[file_name].is_file()
    assert set(output.metrics) == {survivor, "spo2", "diabetes_probability"}
    assert not (source.output_dir / member).exists()


def test_member_input_mutation_cannot_contaminate_later_members_or_source(tmp_path, scene_case, scene_factory):
    """成员只得到任务快照的独立副本，后续真实计算不能继承前项修改。"""
    original = PPG_SCENE.members[0].definition.resource_factory
    observed = []

    def create(resources):
        """对成员输入施加破坏后继续原完整算法。"""
        executor = original(resources)

        def mutate(value):
            """每次调用先确认独立快照，再修改嵌套元数据。"""
            observed.append(deepcopy(value.client_metadata))
            value.client_metadata["nested"]["value"] = "changed"
            value.profile["age_years"] = 999
            return executor(value)

        return mutate

    runtime = scene_factory(hr=create).create_runtime(scene_case.resources())
    first_input = scene_case.input(tmp_path / "first")
    first = runtime.run(first_input)
    second = runtime.run(scene_case.input(tmp_path / "second"))
    assert first.payload["outcome"] == second.payload["outcome"] == "ALL_SUCCEEDED"
    assert first.payload == second.payload
    assert first_input.profile["age_years"] == 24
    assert first_input.client_metadata == {"nested": {"value": "original"}}
    assert observed == [{"nested": {"value": "original"}}] * 2


def test_process_termination_is_not_captured_as_member_failure(tmp_path, scene_case, scene_factory):
    """进程级中断必须越过成员边界，不制造合法完成汇总。"""
    def terminate(resources):
        """模拟整体终止，而非普通计算错误。"""
        raise KeyboardInterrupt

    runtime = scene_factory(hr=terminate).create_runtime(scene_case.resources())
    with pytest.raises(KeyboardInterrupt):
        runtime.run(scene_case.input(tmp_path / "execution"))


def test_registry_import_keeps_computation_dependencies_lazy(tmp_path):
    """两个新增注册路径不能让 Web 启动加载计算或模型运行库。"""
    process = subprocess.run([
        sys.executable, "-c",
        (
            "import sys; import health_inference; "
            "assert not {'pandas','numpy','scipy','neurokit2','onnxruntime','joblib'} & sys.modules.keys()"
        ),
    ], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
