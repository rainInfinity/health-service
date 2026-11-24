"""验证固定场景的真实版本资源复用、任务隔离及安全制品准备。"""

from datetime import date

import pytest
from health_inference import InferenceError
from health_service.application.catalog import (
    CatalogConfigurationInvalid,
    create_model,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.execution import (
    claim_task_for_execution,
    save_task_execution_success,
)
from health_service.application.profiles import update_profile
from health_service.infrastructure.inference_adapter import run_task_inference
from health_service.models import ModelArtifact, ModelVersion, TaskMetric

from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.integration.workflows.test_scene_support import (
    CHANNEL,
    artifact_path,
    create_scene_version,
    prepare_scene_result,
    standalone_member_output,
    submit_scene_task,
)
from tests.integration.workflows.test_scene_support import (
    scene_environment as scene_environment,
)
from tests.integration.workflows.test_spo2_inference_consumer import _create_published_version

pytestmark = pytest.mark.django_db(transaction=True)


def test_scene_versions_a_b_a_use_frozen_inputs_profiles_and_own_models(scene_environment):
    """真实成员结果逐项对照独立调用，A/B/A 交错不读实时资料或独立模型指针。"""
    environment = scene_environment
    model, version_a = create_scene_version(environment, bias=92.0, logit=-1.0)
    _, version_b = create_scene_version(environment, model=model, bias=96.0, logit=1.0)
    independent = create_model(
        code=f"independent-{model.model_id}", name="独立血氧", description=None
    )
    independent_a = _create_published_version(model_id=independent.model_id, label="a", bias=85.0)
    independent_b = _create_published_version(model_id=independent.model_id, label="b", bias=99.0)
    tasks = []
    for index, (version, noise) in enumerate(
        ((version_a, 0.0), (version_b, 0.01), (version_a, 0.02))
    ):
        update_profile(
            environment.owner.pk,
            {"name": f"冻结资料-{index}", "gender": 1 + index % 2, "birth_date": date(1990 + index, 1, 1)},
        )
        task = submit_scene_task(
            environment,
            model,
            version,
            ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8, noise_amplitude=noise),
            metadata={"device_model": f"device-{index}"},
        )
        tasks.append((task, version, dict(task.profile_snapshot), dict(task.client_metadata)))
    update_profile(environment.owner.pk, {"name": "不得回填的实时资料", "birth_date": date(2000, 1, 1)})
    outputs = []
    for index, (task, version, snapshot, metadata) in enumerate(tasks):
        independent_version = independent_a if index == 1 else independent_b
        select_channel_model_version(
            channel=CHANNEL,
            model_id=independent.model_id,
            version_id=independent_version.version_id,
        )
        token, result = prepare_scene_result(task)
        assert result.payload["outcome"] == "ALL_SUCCEEDED"
        expected_metrics = {}
        for member_name, member in result.payload["results"].items():
            expected = standalone_member_output(environment, task, member_name, member["algorithmKey"])
            assert member["result"] == expected.payload
            expected_metrics.update(expected.metrics)
            for logical_name, path in expected.output_files.items():
                actual_path = environment.storage.task_data_root / result.output_files[
                    f"{member_name}_{logical_name}"
                ]
                assert actual_path.read_bytes() == path.read_bytes()
        assert result.metrics == expected_metrics
        assert save_task_execution_success(task_id=task.pk, execution_token=token, result=result)
        task.refresh_from_db()
        assert task.model_version_id == version.version_id
        assert task.profile_snapshot == snapshot
        assert task.client_metadata == metadata
        assert task.profile_snapshot["name"] == f"冻结资料-{index}"
        assert {
            item.metric.code: item.numeric_value
            for item in TaskMetric.objects.filter(task=task).select_related("metric")
        } == expected_metrics
        outputs.append(result)
    assert outputs[0].payload["results"]["diabetes"]["result"]["prob"] < 0.5
    assert outputs[1].payload["results"]["diabetes"]["result"]["prob"] > 0.5
    assert outputs[2].payload["results"]["diabetes"]["result"]["prob"] < 0.5
    assert outputs[0].payload["results"]["hr"]["result"] != outputs[2].payload["results"]["hr"]["result"]
    assert set(outputs[0].output_files.values()).isdisjoint(outputs[2].output_files.values())


@pytest.mark.parametrize("unavailable", ["missing", "corrupt", "directory"])
def test_member_initialization_failure_is_isolated_and_new_task_recovers(
    scene_environment, unavailable
):
    """发布后首次初始化失败仅影响 diabetes，恢复资源后同一版本新任务重新尝试。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    path = artifact_path(environment, version, "diabetes")
    original = path.read_bytes()
    if unavailable == "corrupt":
        path.write_bytes(b"PRIVATE-invalid-onnx-resource")
    else:
        path.unlink()
        if unavailable == "directory":
            path.mkdir()
    first = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    first_token, first_result = prepare_scene_result(first)
    assert first_result.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    members = first_result.payload["results"]
    assert members["hr"]["status"] == members["br"]["status"] == members["spo2"]["status"] == "SUCCEEDED"
    assert members["diabetes"]["status"] == "FAILED"
    assert members["diabetes"]["failureCode"] == "INFERENCE.INVALID_ARTIFACT"
    assert members["diabetes"]["result"] is None
    assert "PRIVATE" not in str(first_result.payload)
    assert set(first_result.metrics) == {"hr", "br", "spo2"}
    assert "diabetes_processed_ppg" not in first_result.output_files
    assert save_task_execution_success(task_id=first.pk, execution_token=first_token, result=first_result)
    if unavailable == "directory":
        path.rmdir()
    path.write_bytes(original)
    second = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8, noise_amplitude=0.01)
    )
    second_token, second_result = prepare_scene_result(second)
    assert second_result.payload["outcome"] == "ALL_SUCCEEDED"
    assert second_result.payload["results"]["diabetes"]["failureCode"] is None
    expected = standalone_member_output(
        environment, second, "diabetes", "ppg.active.offline.diabetes.v1"
    )
    assert second_result.payload["results"]["diabetes"]["result"] == expected.payload
    assert second_result.metrics["diabetes_probability"] == expected.metrics["diabetes_probability"]
    assert save_task_execution_success(task_id=second.pk, execution_token=second_token, result=second_result)
    first.refresh_from_db()
    assert first.result_payload["results"]["diabetes"]["status"] == "FAILED"


@pytest.mark.parametrize("unsafe_kind", ["traversal", "symlink"])
def test_scene_candidate_rejects_unsafe_artifact_before_members(scene_environment, unsafe_kind):
    """成员隔离不能把越界制品键或已有逃逸链接降级为可成功汇总的成员错误。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    artifact = ModelArtifact.objects.get(model_version_id=version.version_id, logical_name="diabetes")
    if unsafe_kind == "traversal":
        artifact.storage_key = "../PRIVATE.onnx"
        artifact.save(update_fields=["storage_key"])
    else:
        path = artifact_path(environment, version, "diabetes")
        outside = environment.root / "PRIVATE.onnx"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    token = claim_task_for_execution(task_id=task.pk)
    with pytest.raises(InferenceError) as caught:
        run_task_inference(task_id=task.pk, execution_token=token)
    assert caught.value.code == "INFERENCE.INVALID_ARTIFACT"
    assert not (environment.storage.task_data_root / ".executions" / str(task.pk)).exists()


def test_ordinary_algorithm_keeps_strict_artifact_preparation(scene_environment):
    """独立血氧缺失制品仍在任务准备时失败，不启用场景延迟语义。"""
    environment = scene_environment
    model = create_model(code="ordinary-spo2", name="独立血氧", description=None)
    version = _create_published_version(model_id=model.model_id, label="strict", bias=92.0)
    artifact_path(environment, version, "spo2").unlink()
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    token = claim_task_for_execution(task_id=task.pk)
    with pytest.raises(InferenceError) as caught:
        run_task_inference(task_id=task.pk, execution_token=token)
    assert caught.value.code == "INFERENCE.INVALID_ARTIFACT"
    assert not (environment.storage.task_data_root / ".executions" / str(task.pk)).exists()


@pytest.mark.parametrize("invalid_case", ["missing", "directory", "incomplete", "parameters"])
def test_scene_publication_remains_strict(scene_environment, invalid_case):
    """尚未发布的场景必须具有空参数、完整登记和现存普通制品文件。"""
    environment = scene_environment
    _, version = create_scene_version(environment, publish=False)
    path = artifact_path(environment, version, "diabetes")
    if invalid_case in {"missing", "directory"}:
        path.unlink()
        if invalid_case == "directory":
            path.mkdir()
    elif invalid_case == "incomplete":
        ModelArtifact.objects.filter(model_version_id=version.version_id, logical_name="diabetes").delete()
    else:
        ModelVersion.objects.filter(pk=version.version_id).update(params={"members": ["hr"]})
    with pytest.raises(CatalogConfigurationInvalid):
        publish_model_version(version_id=version.version_id)
    assert ModelVersion.objects.get(pk=version.version_id).published_at is None
