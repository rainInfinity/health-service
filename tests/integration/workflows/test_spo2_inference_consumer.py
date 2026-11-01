"""验证血氧 v1 在真实消费者中的版本隔离、结果投影与失败清理。"""

import asyncio
import csv
import json
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4

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
from health_inference.algorithms.ppg_active_offline_spo2_v1.load_input import load_spo2_ppg
from health_inference.algorithms.ppg_active_offline_spo2_v1.preprocessing import preprocess_ppg
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
    upload_model_artifact,
)
from health_service.application.tasks import create_task_draft, submit_user_task, upload_task_input
from health_service.models import Channel, ModelVersion, Task, TaskMetric
from onnx import TensorProto, helper, numpy_helper
from redis import Redis

from tests.integration.ppg_inputs import synthetic_ppg_csv

_CHANNEL = "ios-cn"
_FEATURE_NAMES = ("r_mean", "r_std", "g_mean", "b_mean")
_PASSWORD = "Spo2-workflow-test-2026!"


@pytest.fixture
def spo2_stream():
    """为每个工作流创建独立 Redis Stream 和消费组。"""
    stream = f"test:inference:spo2-workflow:{uuid4().hex}"
    group = f"test:health-inference:spo2-workflow:{uuid4().hex}"
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        with override_settings(INFERENCE_STREAM=stream, INFERENCE_GROUP=group):
            yield client, stream, group
    finally:
        client.delete(stream)
        client.close()


def _synthetic_model_bytes(bias: float) -> bytes:
    """生成仅供集成测试使用、按每窗特征均值输出的 ONNX 模型。"""
    nodes = [
        helper.make_node("ReduceMean", ["features"], ["means"], axes=[1], keepdims=0),
        helper.make_node("Mul", ["means", "scale"], ["scaled"]),
        helper.make_node("Add", ["scaled", "bias"], ["predictions"]),
    ]
    graph = helper.make_graph(
        nodes,
        "synthetic-spo2-integration",
        [
            helper.make_tensor_value_info(
                "features", TensorProto.FLOAT, ["batch", len(_FEATURE_NAMES)]
            )
        ],
        [helper.make_tensor_value_info("predictions", TensorProto.FLOAT, ["batch"])],
        [
            numpy_helper.from_array(np.asarray(1.0, dtype=np.float32), name="scale"),
            numpy_helper.from_array(np.asarray(bias, dtype=np.float32), name="bias"),
        ],
    )
    model = helper.make_model(
        graph,
        opset_imports=[helper.make_opsetid("", 13)],
        producer_name="health-service-integration-test",
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


def _synthetic_input(heart_rate_hz: float, noise_amplitude: float = 0.0) -> str:
    """生成并截取确定性 RGB 合成信号，形成三个有效窗口。"""
    rows = synthetic_ppg_csv(
        heart_rate_hz=heart_rate_hz,
        noise_amplitude=noise_amplitude,
    ).splitlines()
    return "\n".join(rows[:601]) + "\n"


def _create_published_version(*, model_id: int, label: str, bias: float) -> ModelVersion:
    """上传合成 ONNX 与 JSON 特征清单并发布完整 SPO2 版本。"""
    draft = create_model_version(
        model_id=model_id,
        version=f"{label}-{uuid4().hex[:12]}",
        algorithm_key="ppg.active.offline.spo2.v1",
        params=None,
    )
    artifacts = {
        "spo2": _synthetic_model_bytes(bias),
        "feature_names": json.dumps(_FEATURE_NAMES).encode("utf-8"),
        "stable_features": json.dumps(_FEATURE_NAMES).encode("utf-8"),
    }
    for logical_name, content in artifacts.items():
        upload_model_artifact(
            version_id=draft.version_id,
            logical_name=logical_name,
            source_filename={
                "spo2": "spo2.onnx",
                "feature_names": "feature_names.json",
                "stable_features": "stable_features.json",
            }[logical_name],
            source=BytesIO(content),
        )
    set_model_version_metrics(version_id=draft.version_id, metric_codes=("spo2",))
    return publish_model_version(version_id=draft.version_id)


def _submit_task(
    *,
    user_id: int,
    model_id: int,
    version_id: int,
    heart_rate_hz: float,
    input_dir: Path,
    noise_amplitude: float = 0.0,
) -> tuple[int, Path]:
    """固定当前版本并经任务业务接口上传、提交一份合成 PPG。"""
    select_channel_model_version(
        channel=_CHANNEL,
        model_id=model_id,
        version_id=version_id,
    )
    draft = create_task_draft(user_id=user_id, channel=_CHANNEL, model_id=model_id)
    content = _synthetic_input(heart_rate_hz, noise_amplitude)
    input_path = input_dir / f"{draft.task_id}.csv"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_text(content, encoding="utf-8")
    upload_task_input(
        task_id=draft.task_id,
        user_id=user_id,
        channel=_CHANNEL,
        logical_name="ppg",
        source_filename=input_path.name,
        source=SimpleUploadedFile(
            input_path.name,
            content.encode("utf-8"),
            content_type="text/csv",
        ),
    )
    submitted = submit_user_task(
        task_id=draft.task_id,
        user_id=user_id,
        channel=_CHANNEL,
    )
    assert submitted.status == Task.StatusChoices.SUBMITTED
    return draft.task_id, input_path


def _run_once(consumer: str) -> None:
    """通过正式 Django 命令入口实际执行一轮 Redis 消费。"""
    call_command("run_inference", consumer=consumer, once=True, verbosity=0)


async def _response_body(response) -> bytes:
    """读取普通或流式 HTTP 响应的字节。"""
    if not response.streaming:
        return response.content
    content = response.streaming_content
    if hasattr(content, "__aiter__"):
        return b"".join([chunk async for chunk in content])
    return b"".join(content)


@pytest.mark.django_db(transaction=True)
def test_spo2_versions_isolate_interleaved_tasks_and_downloads(tmp_path, spo2_stream):
    """交错执行两个发布版本，验证历史绑定、指标投影和输出下载隔离。"""
    redis_client, stream, group = spo2_stream
    task_root = tmp_path / "tasks"
    artifact_root = tmp_path / "artifacts"
    input_dir = tmp_path / "expected-inputs"
    input_dir.mkdir()
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=artifact_root,
        MAX_UPLOAD_BYTES=1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        create_metric_definition(
            code="spo2",
            description="集成测试血氧百分比",
            value_type="number",
            unit="percent",
        )
        model = create_model(
            code=f"spo2-workflow-{uuid4().hex}",
            name="SPO2 workflow isolation",
            description=None,
        )
        versions = {
            "a": _create_published_version(model_id=model.model_id, label="version-a", bias=92.0),
            "b": _create_published_version(model_id=model.model_id, label="version-b", bias=96.0),
        }
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            username=f"spo2-workflow-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = user_model.objects.create_user(
            username=f"spo2-workflow-other-{uuid4().hex}",
            password=_PASSWORD,
        )

        task_specs = (("a", 0.0), ("b", 0.01), ("a", 0.02), ("b", 0.03))
        tasks: list[tuple[int, str]] = []
        input_paths: dict[int, Path] = {}
        for version_name, noise_amplitude in task_specs:
            task_id, input_path = _submit_task(
                user_id=owner.pk,
                model_id=model.model_id,
                version_id=versions[version_name].version_id,
                heart_rate_hz=1.8,
                noise_amplitude=noise_amplitude,
                input_dir=input_dir,
            )
            tasks.append((task_id, version_name))
            input_paths[task_id] = input_path

        published_at = {
            name: version.published_at for name, version in versions.items()
        }
        _run_once(f"spo2-isolation-{uuid4().hex[:12]}")
        queued_task_ids = [int(fields["task_id"]) for _, fields in redis_client.xrange(stream)]
        assert queued_task_ids == [task_id for task_id, _ in tasks]

        completed_tasks = {task_id: Task.objects.get(pk=task_id) for task_id, _ in tasks}
        output_keys = []
        for task_id, version_name in tasks:
            task = completed_tasks[task_id]
            expected_version = versions[version_name]
            assert task.status == Task.StatusChoices.SUCCEEDED
            assert task.model_version_id == expected_version.version_id
            assert task.result_payload["quality"]["score"] >= 70.0
            assert task.result_payload["quality"]["threshold"] == 70
            assert len(task.result_payload["window_predictions"]) == 3
            value = task.result_payload["spo2"]
            if version_name == "a":
                assert 92.0 <= value <= 93.0
            else:
                assert 96.0 <= value <= 97.0
            projected = {
                item.metric.code: item.numeric_value
                for item in TaskMetric.objects.filter(task=task).select_related("metric")
            }
            assert projected == {"spo2": value}
            output_key = task.result_payload["output_files"]["processed_ppg"]
            assert isinstance(output_key, str) and output_key
            output_keys.append(output_key)
        assert len(set(output_keys)) == len(tasks)

        current = Channel.objects.get(label=_CHANNEL).model_selections.get(
            model_id=model.model_id
        )
        assert current.current_version_id == versions["b"].version_id
        for name, version in versions.items():
            assert ModelVersion.objects.get(pk=version.version_id).published_at == published_at[name]

        async def download_outputs():
            """通过任务 HTTP API 下载所有者输出并验证其他用户被隐藏。"""
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
            owner_headers = {
                "Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"
            }
            other_headers = {
                "Authorization": f"Bearer {other_login.json()['data']['accessToken']}"
            }
            bodies = {}
            for task_id, _ in tasks:
                response = await client.get(
                    f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/processed_ppg/download",
                    headers=owner_headers,
                )
                assert response.status_code == 200
                bodies[task_id] = await _response_body(response)
            denied = await client.get(
                f"/api/v1/{_CHANNEL}/tasks/{tasks[0][0]}/outputs/processed_ppg/download",
                headers=other_headers,
            )
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return bodies, denied.status_code

        output_bodies, denied_status = asyncio.run(download_outputs())
        assert denied_status == 404
        assert len(set(output_bodies.values())) == len(tasks)
        for task_id, body in output_bodies.items():
            prepared = preprocess_ppg(load_spo2_ppg(input_paths[task_id]))
            reader = csv.DictReader(StringIO(body.decode("utf-8")))
            rows = list(reader)
            assert reader.fieldnames == ["timestampFrame", "ppg"]
            assert len(rows) == len(prepared.ppg_signal) == 600
            downloaded_timestamps = np.asarray(
                [float(row["timestampFrame"]) for row in rows], dtype=np.float64
            )
            downloaded_ppg = np.asarray([float(row["ppg"]) for row in rows], dtype=np.float64)
            assert np.allclose(
                downloaded_timestamps,
                prepared_input_timestamps(input_paths[task_id]),
            )
            assert np.allclose(downloaded_ppg, prepared.ppg_signal, rtol=0.0, atol=1e-9)
        assert redis_client.xpending(stream, group)["pending"] == 0


def prepared_input_timestamps(path: Path) -> tuple[float, ...]:
    """读取合成测试输入中用于输出核对的 PPG 时间轴。"""
    return load_spo2_ppg(path).timestamps_ms


@pytest.mark.django_db(transaction=True)
def test_spo2_invalid_window_prediction_has_no_success_projection(tmp_path, spo2_stream):
    """非有限 ONNX 预测使任务失败，且不保存指标或处理后文件。"""
    redis_client, stream, group = spo2_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        create_metric_definition(
            code="spo2",
            description="集成测试血氧百分比",
            value_type="number",
            unit="percent",
        )
        model = create_model(
            code=f"spo2-invalid-output-{uuid4().hex}",
            name="SPO2 invalid output workflow",
            description=None,
        )
        version = _create_published_version(
            model_id=model.model_id,
            label="invalid-output",
            bias=float("nan"),
        )
        user = get_user_model().objects.create_user(
            username=f"spo2-invalid-output-{uuid4().hex}",
            password=_PASSWORD,
        )
        task_id, _ = _submit_task(
            user_id=user.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            heart_rate_hz=1.8,
            input_dir=tmp_path / "expected-inputs",
        )
        _run_once(f"spo2-invalid-{uuid4().hex[:12]}")

        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.FAILED
        assert task.model_version_id == version.version_id
        assert task.failure_code == "RESULT.INVALID_OUTPUT"
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        output_root = task_root / "outputs" / str(task.pk)
        assert not tuple(output_root.rglob("ppg-processed.csv"))
        assert redis_client.xpending(stream, group)["pending"] == 0
