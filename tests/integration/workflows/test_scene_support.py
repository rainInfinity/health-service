"""复用既有模型及波形帮助器，准备固定场景的真实工作流资源。"""

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from health_inference import InferenceInput, VersionResources, get_algorithm
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
    upload_model_artifact,
)
from health_service.application.execution import claim_task_for_execution
from health_service.application.tasks import create_task_draft, submit_user_task, upload_task_input
from health_service.infrastructure.inference_adapter import (
    clear_version_runtime_cache,
    run_task_inference,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.models import Channel, ModelArtifact, Task, TaskInputFile
from redis import Redis

from tests.integration.workflows.test_bp_inference_consumer import _model_content
from tests.integration.workflows.test_diabetes_inference_consumer import _onnx_model
from tests.integration.workflows.test_spo2_inference_consumer import (
    _FEATURE_NAMES,
    _synthetic_model_bytes,
)

CHANNEL = "ios-cn"
PASSWORD = "Scene-workflow-test-2026!"
PPG_SCENE = "ppg.active.offline.all.v1"
BP_SCENE = "ppg_acc.active.offline.all.v1"


@dataclass(frozen=True)
class SceneEnvironment:
    """保存单个用例独占的存储、账号及消息流。"""

    root: Path  # 本用例临时文件的共同父目录。
    storage: LocalFileStorage  # 本用例独占的任务与制品存储。
    owner: object  # 场景任务所属账号。
    other: object  # 用于验证越权读取被隐藏的另一账号。
    redis: Redis  # 操作本用例消息流的连接。
    stream: str  # 本用例唯一的 Redis Stream 名称。
    group: str  # 本用例唯一的消费者组名称。


@pytest.fixture
def scene_environment(tmp_path):
    """使用测试库、临时存储和唯一 Redis 流，不接触现有业务配置。"""
    stream = f"test:inference:scene:{uuid4().hex}"
    group = f"test:health-inference:scene:{uuid4().hex}"
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    clear_version_runtime_cache()
    try:
        with override_settings(
            TASK_DATA_ROOT=tmp_path / "tasks",
            MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
            MAX_UPLOAD_BYTES=2 * 1024 * 1024,
            INFERENCE_STREAM=stream,
            INFERENCE_GROUP=group,
        ):
            for channel in (CHANNEL, "wxapp"):
                Channel.objects.get_or_create(label=channel)
            for key in (PPG_SCENE, BP_SCENE):
                for contract in get_algorithm(key).metric_contracts:
                    create_metric_definition(
                        code=contract.code,
                        description="固定场景工作流测试指标",
                        value_type=contract.value_type,
                        unit=contract.unit,
                    )
            users = get_user_model()
            owner = users.objects.create_user(
                username=f"scene-owner-{uuid4().hex}", password=PASSWORD
            )
            other = users.objects.create_user(
                username=f"scene-other-{uuid4().hex}", password=PASSWORD
            )
            yield SceneEnvironment(
                root=tmp_path,
                storage=LocalFileStorage(tmp_path / "tasks", tmp_path / "artifacts"),
                owner=owner,
                other=other,
                redis=client,
                stream=stream,
                group=group,
            )
    finally:
        clear_version_runtime_cache()
        client.delete(stream)
        client.close()


def create_scene_version(
    environment,
    *,
    model=None,
    algorithm_key=PPG_SCENE,
    bias=92.0,
    publish=True,
    logit=-1.0,
    selected_metrics=None,
):
    """通过正式发布用例绑定真实可执行制品，不伪造成员输出。"""
    if model is None:
        model = create_model(
            code=f"scene-{uuid4().hex}",
            name="统一PPG场景" if algorithm_key == PPG_SCENE else "统一PPG+ACC场景",
            description=None,
        )
    draft = create_model_version(
        model_id=model.model_id,
        version=f"test-{uuid4().hex[:12]}",
        algorithm_key=algorithm_key,
        params=None,
    )
    if algorithm_key == PPG_SCENE:
        artifacts = {
            "spo2": ("spo2.onnx", _synthetic_model_bytes(bias)),
            "feature_names": ("feature_names.json", json.dumps(_FEATURE_NAMES).encode()),
            "stable_features": ("stable_features.json", json.dumps(_FEATURE_NAMES).encode()),
            "diabetes": ("diabetes.onnx", _onnx_model(logit)),
        }
    else:
        source_root = environment.root / f"bp-source-{draft.version_id}"
        source_root.mkdir()
        artifacts = {
            "sbp": ("sbp.joblib", _model_content(source_root / "sbp.joblib", 120.0)),
            "dbp": ("dbp.joblib", _model_content(source_root / "dbp.joblib", 80.0)),
            "normalize": (
                "normalize.csv",
                b",X1,age,pr,bmi,height\nmiu,0,0,0,0,0\nsegma,1,1,1,1,1\n",
            ),
        }
    for logical_name, (filename, content) in artifacts.items():
        upload_model_artifact(
            version_id=draft.version_id,
            logical_name=logical_name,
            source_filename=filename,
            source=BytesIO(content),
        )
    if selected_metrics is None:
        selected_metrics = tuple(c.code for c in get_algorithm(algorithm_key).metric_contracts)
    set_model_version_metrics(version_id=draft.version_id, metric_codes=selected_metrics)
    version = publish_model_version(version_id=draft.version_id) if publish else draft
    return model, version


def submit_scene_task(environment, model, version, *, ppg_content, acc_content=None, metadata=None):
    """用既有任务入口上传一次原始数据并冻结当前资料。"""
    select_channel_model_version(
        channel=CHANNEL, model_id=model.model_id, version_id=version.version_id
    )
    draft = create_task_draft(user_id=environment.owner.pk, channel=CHANNEL, model_id=model.model_id)
    contents = {"ppg": ppg_content}
    if acc_content is not None:
        contents["acc"] = acc_content
    for logical_name, content in contents.items():
        filename = f"{logical_name}.csv"
        upload_task_input(
            task_id=draft.task_id,
            user_id=environment.owner.pk,
            channel=CHANNEL,
            logical_name=logical_name,
            source_filename=filename,
            source=SimpleUploadedFile(filename, content.encode(), content_type="text/csv"),
        )
    submit_user_task(
        task_id=draft.task_id,
        user_id=environment.owner.pk,
        channel=CHANNEL,
        client_metadata=metadata,
    )
    return Task.objects.get(pk=draft.task_id)


def prepare_scene_result(task):
    """实际领取租约并执行适配器，返回可提交的真实完整汇总。"""
    token = claim_task_for_execution(task_id=task.pk)
    assert token is not None
    result = run_task_inference(task_id=task.pk, execution_token=token)
    assert result is not None
    return token, result


def artifact_path(environment, version, logical_name):
    """定位当前测试版本实际登记的制品文件。"""
    artifact = ModelArtifact.objects.get(
        model_version_id=version.version_id, logical_name=logical_name
    )
    return environment.storage.resolve_file(StorageArea.MODEL_ARTIFACT, artifact.storage_key)


def standalone_member_output(environment, task, member_name, algorithm_key):
    """用同输入、同冻结资料及同场景资源运行原成员，作为独立数值与文件对照。"""
    definition = get_algorithm(algorithm_key)
    files = {
        item.logical_name: environment.storage.resolve_file(StorageArea.TASK_DATA, item.storage_key)
        for item in TaskInputFile.objects.filter(task=task, is_selected=True)
    }
    artifacts = {
        item.logical_name: environment.storage.resolve_file(
            StorageArea.MODEL_ARTIFACT, item.storage_key
        )
        for item in ModelArtifact.objects.filter(model_version_id=task.model_version_id)
        if item.logical_name in definition.required_artifacts
    }
    output_dir = environment.root / "standalone" / str(task.pk) / member_name
    output_dir.mkdir(parents=True)
    runtime = definition.create_runtime(VersionResources(params={}, artifact_paths=artifacts))
    return runtime.run(
        InferenceInput(
            files=files,
            artifacts=artifacts,
            profile=dict(task.profile_snapshot or {}),
            params={},
            output_dir=output_dir,
            client_metadata=task.client_metadata,
        )
    )


def run_scene_consumer():
    """启动一次正式消费者，实际计算由既有隔离工作进程完成。"""
    call_command("run_inference", consumer=f"scene-{uuid4().hex[:12]}", once=True, verbosity=0)
