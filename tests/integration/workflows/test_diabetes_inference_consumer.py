"""验证糖尿病 v1 的真实模型版本、Redis 消费、结果投影与文件下载。"""

import asyncio
import csv
import json
import math
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import onnx
import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections
from django.test import AsyncClient, override_settings
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
    upload_model_artifact,
)
from health_service.application.tasks import (
    create_task_draft,
    submit_user_task,
    upload_task_input,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.models import Channel, Task, TaskMetric
from onnx import TensorProto, helper, numpy_helper
from redis import Redis

from tests.integration.ppg_inputs import synthetic_ppg_csv

_CHANNEL = "ios-cn"
_PASSWORD = "Diabetes-workflow-test-2026!"
_ALGORITHM_KEY = "ppg.active.offline.diabetes.v1"


@pytest.fixture
def diabetes_stream():
    """为每个糖尿病消费者工作流创建独立 Redis Stream。"""
    stream = f"test:inference:diabetes-workflow:{uuid4().hex}"
    group = f"test:health-inference:diabetes-workflow:{uuid4().hex}"
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        with override_settings(INFERENCE_STREAM=stream, INFERENCE_GROUP=group):
            yield client, stream, group
    finally:
        client.delete(stream)
        client.close()


def _onnx_model(logit: float, *, probability_width: int = 2) -> bytes:
    """生成真实可执行且输出契约可控的 918 维 ONNX 测试模型。"""
    input_value = helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, 918])
    label_value = helper.make_tensor_value_info("label", TensorProto.INT64, [None])
    probabilities_value = helper.make_tensor_value_info(
        "probabilities",
        TensorProto.FLOAT,
        [None, probability_width],
    )
    initializers = [
        numpy_helper.from_array(np.asarray(0.0, dtype=np.float32), name="zero"),
        numpy_helper.from_array(np.asarray(logit, dtype=np.float32), name="logit"),
        numpy_helper.from_array(np.asarray(1.0, dtype=np.float32), name="one"),
        numpy_helper.from_array(np.asarray(0.5, dtype=np.float32), name="half"),
        numpy_helper.from_array(np.asarray([0], dtype=np.int64), name="first_index"),
        numpy_helper.from_array(np.asarray([1], dtype=np.int64), name="axes"),
    ]
    nodes = [
        helper.make_node("Gather", ["input", "first_index"], ["first_feature"], axis=1),
        helper.make_node("ReduceMean", ["first_feature"], ["mean"], axes=[1], keepdims=0),
        helper.make_node("Mul", ["mean", "zero"], ["zero_mean"]),
        helper.make_node("Add", ["zero_mean", "logit"], ["logits"]),
        helper.make_node("Sigmoid", ["logits"], ["positive"]),
        helper.make_node("Sub", ["one", "positive"], ["negative"]),
        helper.make_node("Unsqueeze", ["negative", "axes"], ["negative_column"]),
        helper.make_node("Unsqueeze", ["positive", "axes"], ["positive_column"]),
        helper.make_node("Concat", ["negative_column", "positive_column"], ["first_two"], axis=1),
    ]
    if probability_width == 2:
        nodes.append(
            helper.make_node(
                "Greater",
                ["positive", "half"],
                ["positive_label"],
            )
        )
        nodes.append(
            helper.make_node(
                "Cast",
                ["positive_label"],
                ["label"],
                to=TensorProto.INT64,
            )
        )
        nodes.append(helper.make_node("Identity", ["first_two"], ["probabilities"]))
    else:
        nodes.extend(
            (
                helper.make_node("Mul", ["positive", "zero"], ["third_probability"]),
                helper.make_node(
                    "Unsqueeze",
                    ["third_probability", "axes"],
                    ["third_column"],
                ),
                helper.make_node(
                    "Concat",
                    ["first_two", "third_column"],
                    ["probabilities"],
                    axis=1,
                ),
                helper.make_node(
                    "Greater",
                    ["positive", "half"],
                    ["positive_label"],
                ),
                helper.make_node(
                    "Cast",
                    ["positive_label"],
                    ["label"],
                    to=TensorProto.INT64,
                ),
            )
        )
    graph = helper.make_graph(
        nodes,
        "diabetes-workflow-test",
        [input_value],
        [label_value, probabilities_value],
        initializer=initializers,
    )
    model = helper.make_model(
        graph,
        opset_imports=[helper.make_opsetid("", 13)],
        producer_name="health-service-test",
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


def _create_diabetes_version(
    model_id: int,
    *,
    label: str,
    logit: float,
    probability_width: int = 2,
):
    """上传并发布带独立 ONNX 内容的糖尿病算法版本。"""
    draft = create_model_version(
        model_id=model_id,
        version=f"{label}-{uuid4().hex[:12]}",
        algorithm_key=_ALGORITHM_KEY,
        params=None,
    )
    upload_model_artifact(
        version_id=draft.version_id,
        logical_name="diabetes",
        source_filename="diabetes.onnx",
        source=BytesIO(_onnx_model(logit, probability_width=probability_width)),
    )
    set_model_version_metrics(
        version_id=draft.version_id,
        metric_codes=("diabetes_probability",),
    )
    return publish_model_version(version_id=draft.version_id)


def _submit_diabetes_task(*, user_id: int, model_id: int, version_id: int, ppg_content: str) -> int:
    """固定当前已发布版本并通过真实上传接口提交单个 PPG 任务。"""
    select_channel_model_version(
        channel=_CHANNEL,
        model_id=model_id,
        version_id=version_id,
    )
    draft = create_task_draft(user_id=user_id, channel=_CHANNEL, model_id=model_id)
    upload_task_input(
        task_id=draft.task_id,
        user_id=user_id,
        channel=_CHANNEL,
        logical_name="ppg",
        source_filename="ppg.csv",
        source=SimpleUploadedFile(
            "ppg.csv",
            ppg_content.encode("utf-8"),
            content_type="text/csv",
        ),
    )
    submitted = submit_user_task(
        task_id=draft.task_id,
        user_id=user_id,
        channel=_CHANNEL,
    )
    assert submitted.status == Task.StatusChoices.SUBMITTED
    return draft.task_id


def _run_once(consumer: str) -> None:
    """通过正式管理命令消费当前测试 Stream 中的任务。"""
    call_command("run_inference", consumer=consumer, once=True, verbosity=0)


def _task_output_root(task_root: Path, task: Task) -> Path:
    """按任务创建日定位正式输出目录入口。"""
    date_part = task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    return task_root / task.channel.label / date_part / "outputs" / str(task.pk)


async def _download_output(owner, other, task_id: int) -> tuple[bytes, int]:
    """验证所有者可下载处理后 PPG，其他账号看不到该输出。"""
    client = AsyncClient()
    owner_login = await client.post(
        f"/api/v1/{_CHANNEL}/auth/login",
        data=json.dumps({"username": owner.username, "password": _PASSWORD}),
        content_type="application/json",
    )
    other_login = await client.post(
        f"/api/v1/{_CHANNEL}/auth/login",
        data=json.dumps({"username": other.username, "password": _PASSWORD}),
        content_type="application/json",
    )
    assert owner_login.status_code == other_login.status_code == 200
    owner_headers = {"Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"}
    other_headers = {"Authorization": f"Bearer {other_login.json()['data']['accessToken']}"}
    response = await client.get(
        f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/processed_ppg/download",
        headers=owner_headers,
    )
    assert response.status_code == 200
    if response.streaming:
        content = response.streaming_content
        body = (
            b"".join([chunk async for chunk in content])
            if hasattr(content, "__aiter__")
            else b"".join(content)
        )
    else:
        body = response.content
    denied = await client.get(
        f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/processed_ppg/download",
        headers=other_headers,
    )
    await sync_to_async(connections.close_all, thread_sensitive=True)()
    return body, denied.status_code


@pytest.mark.django_db(transaction=True)
def test_diabetes_consumer_freezes_versions_projects_metrics_and_protects_outputs(
    tmp_path: Path,
    diabetes_stream,
):
    """相邻任务绑定各自模型版本，真实消费成功并安全下载完整 PPG。"""
    redis_client, stream, group = diabetes_stream
    task_root = tmp_path / "tasks"
    artifact_root = tmp_path / "artifacts"
    storage = LocalFileStorage(task_root, artifact_root)
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=artifact_root,
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        create_metric_definition(
            code="diabetes_probability",
            description="糖尿病风险概率",
            value_type="number",
            unit="ratio",
        )
        model = create_model(
            code=f"diabetes-workflow-{uuid4().hex}",
            name="Diabetes workflow",
            description=None,
        )
        version_a = _create_diabetes_version(
            model.model_id,
            label="low",
            logit=-1.0,
        )
        version_b = _create_diabetes_version(
            model.model_id,
            label="high",
            logit=1.0,
        )
        invalid_version = _create_diabetes_version(
            model.model_id,
            label="invalid",
            logit=1.0,
            probability_width=3,
        )
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            username=f"diabetes-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = user_model.objects.create_user(
            username=f"diabetes-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        ppg_a = synthetic_ppg_csv(heart_rate_hz=1.2)
        ppg_b = synthetic_ppg_csv(heart_rate_hz=1.35)
        task_a = _submit_diabetes_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version_a.version_id,
            ppg_content=ppg_a,
        )
        task_b = _submit_diabetes_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version_b.version_id,
            ppg_content=ppg_b,
        )
        task_invalid = _submit_diabetes_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=invalid_version.version_id,
            ppg_content=ppg_a,
        )

        _run_once(f"diabetes-workflow-{uuid4().hex[:12]}")

        tasks = {
            task_id: Task.objects.get(pk=task_id) for task_id in (task_a, task_b, task_invalid)
        }
        assert redis_client.xpending(stream, group)["pending"] == 0
        output_paths = []
        for task_id, version in ((task_a, version_a), (task_b, version_b)):
            task = tasks[task_id]
            assert task.status == Task.StatusChoices.SUCCEEDED
            assert task.model_version_id == version.version_id
            assert task.result_payload["quality"]["score"] >= 70.0
            assert task.result_payload["quality"]["threshold"] == 70
            assert math.isfinite(task.result_payload["prob"])
            metric = TaskMetric.objects.select_related("metric").get(task=task)
            assert metric.metric.code == "diabetes_probability"
            assert metric.numeric_value == task.result_payload["prob"]
            assert task.result_payload["output_files"].keys() == {"processed_ppg"}
            storage_key = task.result_payload["output_files"]["processed_ppg"]
            assert Path(storage_key).parts[:2] == (".executions", str(task_id))
            assert Path(storage_key).name == "ppg-processed.csv"
            output_path = storage.resolve_file(StorageArea.TASK_DATA, storage_key)
            output_paths.append(output_path)
            assert output_path.is_file()
            with output_path.open(encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                assert reader.fieldnames == ["timestampFrame", "ppg"]
                rows = list(reader)
            assert len(rows) == 1800
            assert _task_output_root(task_root, task).is_symlink()
            assert _task_output_root(task_root, task).resolve() == output_path.parent.resolve()
        assert output_paths[0].read_bytes() != output_paths[1].read_bytes()
        assert tasks[task_a].result_payload["prob"] == pytest.approx(1.0 / (1.0 + math.exp(1.0)))
        assert tasks[task_b].result_payload["prob"] == pytest.approx(1.0 / (1.0 + math.exp(-1.0)))
        assert tasks[task_a].result_payload["risk_level"] == 0
        assert tasks[task_b].result_payload["risk_level"] == 1

        failed = tasks[task_invalid]
        assert failed.status == Task.StatusChoices.FAILED
        assert failed.model_version_id == invalid_version.version_id
        assert failed.failure_code == "INFERENCE.INVALID_ARTIFACT"
        assert failed.result_payload is None
        assert not TaskMetric.objects.filter(task=failed).exists()
        assert not _task_output_root(task_root, failed).exists()
        assert not _task_output_root(task_root, failed).is_symlink()

        downloaded, denied_status = asyncio.run(_download_output(owner, other, task_a))
        output_key = tasks[task_a].result_payload["output_files"]["processed_ppg"]
        assert downloaded == storage.resolve_file(StorageArea.TASK_DATA, output_key).read_bytes()
        assert denied_status == 404
