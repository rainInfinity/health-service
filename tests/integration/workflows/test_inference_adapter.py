"""验证固定版本推理输入、执行目录隔离与制品生命周期。"""

from collections.abc import Mapping
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from health_inference import (
    AlgorithmDefinition,
    InferenceError,
    InferenceInput,
    InferenceOutput,
    register_algorithm,
)
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    upload_model_artifact,
)
from health_service.infrastructure.inference_adapter import (
    clear_version_runtime_cache,
    run_task_inference,
)
from health_service.infrastructure.storage import (
    LocalFileStorage,
    StorageArea,
)
from health_service.models import Channel, Profile, Task, TaskInputFile


def _accept_empty_parameters(params: Mapping[str, object]) -> None:
    """为测试算法声明不接受版本参数。"""
    if params:
        raise ValueError("参数必须为空")


def _accept_result(output: InferenceOutput) -> None:
    """接受测试算法构造的固定结果。"""


def _empty_result(_: InferenceInput) -> InferenceOutput:
    """返回不含健康预测的测试结果。"""
    return InferenceOutput(payload={"ok": True}, metrics={}, output_files={})


def _unexpected_profile_runner(_: InferenceInput) -> InferenceOutput:
    """若适配器以实时资料补值则立即失败。"""
    raise AssertionError("缺失快照不得执行算法")


def _artifact_size_runner(inference_input: InferenceInput) -> InferenceOutput:
    """返回实际读取的测试制品大小。"""
    return InferenceOutput(
        payload={"artifact_size": inference_input.artifacts["weights"].stat().st_size},
        metrics={},
        output_files={},
    )


def _optional_device_model_runner(inference_input: InferenceInput) -> InferenceOutput:
    """仅在算法契约声明时读取可选设备型号键。"""
    metadata = inference_input.client_metadata
    return InferenceOutput(
        payload={
            "has_metadata": metadata is not None,
            "device_model": metadata.get("deviceModel") if metadata is not None else None,
        },
        metrics={},
        output_files={},
    )


def _register_test_algorithm(
    *,
    required_artifacts: tuple[str, ...] = (),
    required_profile_fields: tuple[str, ...] = (),
    runner=_empty_result,
) -> str:
    """注册只用于本次集成场景的唯一纯 Python 算法。"""
    key = f"test.adapter.{uuid4().hex}"
    register_algorithm(
        AlgorithmDefinition(
            key=key,
            is_example=False,
            required_inputs=(),
            required_artifacts=required_artifacts,
            required_profile_fields=required_profile_fields,
            parameter_validator=_accept_empty_parameters,
            result_validator=_accept_result,
            runner=runner,
            metric_contracts=(),
        )
    )
    return key


def _create_published_version(*, algorithm_key: str) -> tuple[int, int]:
    """创建并选中单个无制品测试模型版本。"""
    model = create_model(
        code=f"adapter-{uuid4().hex}",
        name="Adapter",
        description=None,
    )
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=algorithm_key,
        params=None,
    )
    version = publish_model_version(version_id=draft.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    return model.model_id, version.version_id


def _create_running_task(
    *,
    user,
    version_id: int,
    profile_snapshot: dict[str, object],
    client_metadata: Mapping[str, object] | None = None,
) -> tuple[Task, object]:
    """创建已取得有效执行权且固定版本的任务。"""
    now = timezone.now()
    token = uuid4()
    task = Task.objects.create(
        user=user,
        channel=Channel.objects.get(label="ios-cn"),
        model_version_id=version_id,
        status=Task.StatusChoices.RUNNING,
        profile_snapshot=profile_snapshot,
        submitted_at=now,
        started_at=now,
        execution_token=token,
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=1,
        client_metadata=client_metadata,
    )
    return task, token


def _store_ppg(storage: LocalFileStorage, task: Task, csv_content: str) -> None:
    """保存并登记任务唯一选中的 PPG 输入文件。"""
    stored = storage.save_upload(
        BytesIO(csv_content.encode()),
        area=StorageArea.TASK_DATA,
        max_bytes=4096,
    )
    TaskInputFile.objects.create(
        task=task,
        logical_name="ppg",
        storage_key=stored.storage_key,
        size_bytes=stored.size_bytes,
        uploaded_at=timezone.now(),
        is_selected=True,
    )


@pytest.mark.django_db(transaction=True)
def test_same_version_tasks_use_own_inputs_after_channel_switch(tmp_path):
    """固定旧版本继续执行且同版本各任务只读取自己的输入和输出目录。"""
    clear_version_runtime_cache()
    try:
        with override_settings(
            TASK_DATA_ROOT=tmp_path / "tasks",
            MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        ):
            storage = LocalFileStorage(
                Path(settings.TASK_DATA_ROOT),
                Path(settings.MODEL_ARTIFACT_ROOT),
            )
            model = create_model(
                code=f"adapter-shared-{uuid4().hex}",
                name="Shared adapter",
                description=None,
            )
            first_draft = create_model_version(
                model_id=model.model_id,
                version="1.0",
                algorithm_key="hr_br.example",
                params=None,
            )
            first_version = publish_model_version(version_id=first_draft.version_id)
            second_draft = create_model_version(
                model_id=model.model_id,
                version="2.0",
                algorithm_key="hr_br.example",
                params=None,
            )
            second_version = publish_model_version(version_id=second_draft.version_id)
            select_channel_model_version(
                channel="ios-cn",
                model_id=model.model_id,
                version_id=first_version.version_id,
            )
            user = get_user_model().objects.create_user(
                username=f"adapter-owner-{uuid4().hex}",
                password="Dawn-valley-483!strong",
            )
            first_task, first_token = _create_running_task(
                user=user,
                version_id=first_version.version_id,
                profile_snapshot={},
            )
            second_task, second_token = _create_running_task(
                user=user,
                version_id=first_version.version_id,
                profile_snapshot={},
            )
            _store_ppg(
                storage,
                first_task,
                "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n500,1,2,3\n1000,1,2,3\n",
            )
            _store_ppg(
                storage,
                second_task,
                "timestampFrame,avgR,avgG,avgB\n0,1,2,3\n250,1,2,3\n500,1,2,3\n750,1,2,3\n",
            )
            select_channel_model_version(
                channel="ios-cn",
                model_id=model.model_id,
                version_id=second_version.version_id,
            )

            first_result = run_task_inference(
                task_id=first_task.pk,
                execution_token=first_token,
            )
            second_result = run_task_inference(
                task_id=second_task.pk,
                execution_token=second_token,
            )

            assert first_result is not None
            assert second_result is not None
            assert first_task.model_version_id == first_version.version_id
            assert first_result.payload["sample_count"] == 3
            assert second_result.payload["sample_count"] == 4
            assert first_result.metrics["example.ppg.sample_count"] == 3
            assert second_result.metrics["example.ppg.sample_count"] == 4
            assert first_result.output_files == {}
            assert second_result.output_files == {}
            assert (
                storage.task_data_root / "outputs" / str(first_task.pk) / first_token.hex
            ).is_dir()
            assert (
                storage.task_data_root / "outputs" / str(second_task.pk) / second_token.hex
            ).is_dir()
    finally:
        clear_version_runtime_cache()


@pytest.mark.django_db(transaction=True)
def test_missing_snapshot_field_does_not_fall_back_to_current_profile(tmp_path):
    """必需资料缺失时明确失败，即使账号当前资料已有该字段。"""
    algorithm_key = _register_test_algorithm(
        required_profile_fields=("name",),
        runner=_unexpected_profile_runner,
    )
    _, version_id = _create_published_version(algorithm_key=algorithm_key)
    user = get_user_model().objects.create_user(
        username=f"adapter-profile-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    Profile.objects.create(user=user, name="当前资料不应被读取")
    task, token = _create_running_task(
        user=user,
        version_id=version_id,
        profile_snapshot={},
    )
    clear_version_runtime_cache()
    try:
        with (
            override_settings(
                TASK_DATA_ROOT=tmp_path / "tasks",
                MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
            ),
            pytest.raises(InferenceError) as error,
        ):
            run_task_inference(task_id=task.pk, execution_token=token)
    finally:
        clear_version_runtime_cache()

    assert error.value.code == "INFERENCE.MISSING_PROFILE"
    assert Profile.objects.filter(user=user, name="当前资料不应被读取").exists()


@pytest.mark.django_db(transaction=True)
def test_releasing_version_cache_does_not_delete_model_artifact(tmp_path):
    """释放进程内版本缓存不会删除仍登记的模型制品文件。"""
    algorithm_key = _register_test_algorithm(
        required_artifacts=("weights",),
        runner=_artifact_size_runner,
    )
    clear_version_runtime_cache()
    try:
        with override_settings(
            TASK_DATA_ROOT=tmp_path / "tasks",
            MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
            MAX_UPLOAD_BYTES=4096,
        ):
            storage = LocalFileStorage(
                Path(settings.TASK_DATA_ROOT),
                Path(settings.MODEL_ARTIFACT_ROOT),
            )
            model = create_model(
                code=f"adapter-artifact-{uuid4().hex}",
                name="Artifact adapter",
                description=None,
            )
            draft = create_model_version(
                model_id=model.model_id,
                version="1.0",
                algorithm_key=algorithm_key,
                params=None,
            )
            artifact = upload_model_artifact(
                version_id=draft.version_id,
                logical_name="weights",
                source=BytesIO(b"immutable model artifact"),
            )
            version = publish_model_version(version_id=draft.version_id)
            select_channel_model_version(
                channel="ios-cn",
                model_id=model.model_id,
                version_id=version.version_id,
            )
            user = get_user_model().objects.create_user(
                username=f"adapter-artifact-{uuid4().hex}",
                password="Dawn-valley-483!strong",
            )
            task, token = _create_running_task(
                user=user,
                version_id=version.version_id,
                profile_snapshot={},
            )

            result = run_task_inference(task_id=task.pk, execution_token=token)
            assert result is not None
            assert result.payload["artifact_size"] == len(b"immutable model artifact")

            clear_version_runtime_cache()
            artifact_path = storage.resolve_file(
                StorageArea.MODEL_ARTIFACT,
                artifact.storage_key,
            )
            assert artifact_path.read_bytes() == b"immutable model artifact"
    finally:
        clear_version_runtime_cache()


@pytest.mark.django_db(transaction=True)
def test_algorithm_can_consume_optional_client_metadata_keys(tmp_path):
    """算法可读取设备型号，也可处理键或整份元数据缺失。"""
    clear_version_runtime_cache()
    try:
        with override_settings(
            TASK_DATA_ROOT=tmp_path / "tasks",
            MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        ):
            algorithm_key = _register_test_algorithm(
                runner=_optional_device_model_runner,
            )
            _, version_id = _create_published_version(algorithm_key=algorithm_key)
            user = get_user_model().objects.create_user(
                username=f"adapter-metadata-{uuid4().hex}",
                password="Dawn-valley-483!strong",
            )
            input_cases = (
                (
                    {"deviceModel": "Pixel 9"},
                    {"has_metadata": True, "device_model": "Pixel 9"},
                ),
                ({}, {"has_metadata": True, "device_model": None}),
                (None, {"has_metadata": False, "device_model": None}),
            )
            actual_payloads = []
            for client_metadata, _ in input_cases:
                task, token = _create_running_task(
                    user=user,
                    version_id=version_id,
                    profile_snapshot={},
                    client_metadata=client_metadata,
                )
                result = run_task_inference(
                    task_id=task.pk,
                    execution_token=token,
                )
                assert result is not None
                actual_payloads.append(dict(result.payload))
    finally:
        clear_version_runtime_cache()

    assert actual_payloads == [expected for _, expected in input_cases]
