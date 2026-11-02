"""验证血压 v1 真实消费者的成功与失败工作流。"""

import asyncio
import csv
import json
import math
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from uuid import uuid4
from zoneinfo import ZoneInfo

import joblib
import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import AsyncClient, override_settings
from django.utils import timezone
from health_service.application import execution as execution_application
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
    upload_model_artifact,
)
from health_service.application.profiles import update_profile
from health_service.application.tasks import (
    create_task_draft,
    submit_user_task,
    upload_task_input,
)
from health_service.infrastructure.persistence import tasks as task_persistence
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.management.commands import run_inference as inference_command
from health_service.models import (
    Channel,
    ModelArtifact,
    Task,
    TaskInputFile,
    TaskMetric,
)
from redis import Redis

from tests.integration.ppg_inputs import synthetic_ppg_csv

_CHANNEL = "ios-cn"
_PASSWORD = "Blood-pressure-workflow-test-2026!"


class _LinearBPTestModel:
    """提供用于消费者资源装配的确定性六维测试模型。"""

    def __init__(self, weights: tuple[float, ...], intercept: float):
        """保存六维权重和偏置。"""
        self.weights = tuple(weights)  # 固定的六维合成模型系数。
        self.intercept = float(intercept)  # 合成模型偏置。

    def predict(self, features):
        """为每个输入行计算有限线性预测。"""
        return [
            self.intercept
            + sum(float(value) * weight for value, weight in zip(row, self.weights, strict=True))
            for row in features
        ]


class _SlowLinearBPTestModel(_LinearBPTestModel):
    """让第二次预测阻塞，以验证消费者能终止超时子进程。"""

    def __init__(self, weights: tuple[float, ...], intercept: float):
        """初始化模型与资源探针调用计数。"""
        super().__init__(weights, intercept)
        self.prediction_calls = 0  # 首次调用仅供资源加载兼容探针使用。

    def predict(self, features):
        """仅在正式预测阶段延迟，避免资源探针影响时限测试。"""
        self.prediction_calls += 1
        if self.prediction_calls > 1:
            time.sleep(5.0)
        return super().predict(features)


@pytest.fixture
def bp_stream():
    """为每个血压消费者工作流创建独立 Redis Stream。"""
    stream = f"test:inference:bp-workflow:{uuid4().hex}"
    group = f"test:health-inference:bp-workflow:{uuid4().hex}"
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        with override_settings(INFERENCE_STREAM=stream, INFERENCE_GROUP=group):
            yield client, stream, group
    finally:
        client.delete(stream)
        client.close()


def _model_content(path: Path, intercept: float, *, slow: bool = False) -> bytes:
    """写入可由血压资源加载器真实反序列化的合成模型。"""
    model_type = _SlowLinearBPTestModel if slow else _LinearBPTestModel
    model = model_type((0.01, 0.02, 0.03, 0.04, 0.05, 0.0), intercept)
    joblib.dump(model, path)
    return path.read_bytes()


def _create_bp_metrics() -> None:
    """创建血压工作流使用的数值指标定义。"""
    for code, description in (("sbp", "收缩压"), ("dbp", "舒张压")):
        create_metric_definition(
            code=code,
            description=description,
            value_type="number",
            unit="mmHg",
        )


def _create_published_bp_version(
    source_root: Path,
    *,
    model=None,
    label: str = "test",
    sbp_intercept: float = 120.0,
    dbp_intercept: float = 80.0,
    slow_models: bool = False,
):
    """上传双模型与归一化制品并发布完整血压 v1 版本。"""
    source_root.mkdir(parents=True, exist_ok=True)
    if model is None:
        model = create_model(
            code=f"bp-workflow-{uuid4().hex}",
            name="Blood pressure workflow",
            description=None,
        )
    draft = create_model_version(
        model_id=model.model_id,
        version=f"{label}-{uuid4().hex[:12]}",
        algorithm_key="ppg_acc.active.offline.bp.v1",
        params=None,
    )
    artifacts = {
        "sbp": (
            "sbp.joblib",
            _model_content(source_root / "sbp.joblib", sbp_intercept, slow=slow_models),
        ),
        "dbp": (
            "dbp.joblib",
            _model_content(source_root / "dbp.joblib", dbp_intercept, slow=slow_models),
        ),
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
    set_model_version_metrics(version_id=draft.version_id, metric_codes=("sbp", "dbp"))
    version = publish_model_version(version_id=draft.version_id)
    return model, version


def _submit_invalid_ppg_task(*, user_id: int, model_id: int) -> int:
    """经真实任务接口上传损坏 PPG 与有效 ACC 后提交。"""
    draft = create_task_draft(user_id=user_id, channel=_CHANNEL, model_id=model_id)
    contents = {
        "ppg": (
            "invalid-ppg.csv",
            "timestampFrame,avgR,avgG\n0,120,128\n500,121,128\n",
        ),
        "acc": (
            "valid-acc.csv",
            "timestampEvent,accX,accY,accZ\n0,0,0,0\n10,0,0,1\n",
        ),
    }
    for logical_name, (filename, content) in contents.items():
        upload_task_input(
            task_id=draft.task_id,
            user_id=user_id,
            channel=_CHANNEL,
            logical_name=logical_name,
            source_filename=filename,
            source=SimpleUploadedFile(
                filename,
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
    return draft.task_id


def _synthetic_acc_csv(
    *,
    heart_rate_hz: float,
    phase: float = 0.0,
    sample_rate_hz: float = 210.0,
    duration_seconds: float = 30.0,
) -> str:
    """生成双轴有限且含心动及高频分量的确定性 ACC 信号。"""
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("timestampEvent", "accX", "accY", "accZ"))
    sample_count = int(sample_rate_hz * duration_seconds)
    for index in range(sample_count):
        seconds = index / sample_rate_hz
        acc_x = 0.1 * math.sin(2.0 * math.pi * 0.5 * seconds)
        acc_y = 0.2 * math.sin(2.0 * math.pi * 0.8 * seconds)
        acc_z = math.sin(2.0 * math.pi * heart_rate_hz * seconds + phase) + 0.4 * math.sin(
            2.0 * math.pi * 12.0 * seconds
        )
        writer.writerow((1000.0 + seconds * 1000.0, acc_x, acc_y, acc_z))
    return output.getvalue()


def _bp_sample_inputs() -> tuple[str, str]:
    """读取仓库内固定旧版双传感器样本供真实流水线验收。"""
    sample_root = Path(__file__).resolve().parents[3] / "var" / "old-assets" / "sample"
    return (
        (sample_root / "20261101_134952-5-2-bp_vlatest-ppg.csv").read_text(encoding="utf-8"),
        (sample_root / "20261101_134952-5-2-bp_vlatest-acc.csv").read_text(encoding="utf-8"),
    )


def _shift_ppg_timestamps(content: str, offset_ms: float) -> str:
    """只平移合法 PPG 的原始毫秒时间轴以构造隔离输入。"""
    reader = csv.DictReader(StringIO(content))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        row["timestampFrame"] = str(float(row["timestampFrame"]) + offset_ms)
        writer.writerow(row)
    return output.getvalue()


def _update_bp_profile(
    user_id: int, *, gender: int, birth_date: date, height_cm: str, weight_kg: str
):
    """使用正式资料用例更新本次血压特征来源。"""
    return update_profile(
        user_id,
        {
            "gender": gender,
            "birth_date": birth_date,
            "height_cm": Decimal(height_cm),
            "weight_kg": Decimal(weight_kg),
        },
    )


def _submit_bp_task(
    *,
    user_id: int,
    model_id: int,
    version_id: int,
    ppg_content: str,
    acc_content: str,
    created_at: datetime | None = None,
) -> int:
    """固定已发布版本并通过真实输入接口上传双 CSV 后提交。"""
    select_channel_model_version(
        channel=_CHANNEL,
        model_id=model_id,
        version_id=version_id,
    )
    draft = create_task_draft(user_id=user_id, channel=_CHANNEL, model_id=model_id)
    if created_at is not None:
        Task.objects.filter(pk=draft.task_id).update(created_at=created_at)
    for logical_name, filename, content in (
        ("ppg", "ppg.csv", ppg_content),
        ("acc", "acc.csv", acc_content),
    ):
        upload_task_input(
            task_id=draft.task_id,
            user_id=user_id,
            channel=_CHANNEL,
            logical_name=logical_name,
            source_filename=filename,
            source=SimpleUploadedFile(
                filename,
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
    return draft.task_id


def _task_output_root(task_root: Path, task: Task) -> Path:
    """按任务创建日定位数据库结果对应的正式目录入口。"""
    date_part = task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    return task_root / task.channel.label / date_part / "outputs" / str(task.pk)


async def _download_bp_outputs(owner, other, task_ids: tuple[int, ...]):
    """按逻辑名下载所有者结果并验证其他账号被隐藏。"""
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
    bodies: dict[tuple[int, str], bytes] = {}
    for task_id in task_ids:
        for logical_name in ("processed_ppg", "processed_scg"):
            response = await client.get(
                f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/{logical_name}/download",
                headers=owner_headers,
            )
            assert response.status_code == 200
            if response.streaming:
                content = response.streaming_content
                if hasattr(content, "__aiter__"):
                    body = b"".join([chunk async for chunk in content])
                else:
                    body = b"".join(content)
            else:
                body = response.content
            bodies[(task_id, logical_name)] = body
    denied = await client.get(
        f"/api/v1/{_CHANNEL}/tasks/{task_ids[0]}/outputs/processed_ppg/download",
        headers=other_headers,
    )
    await sync_to_async(connections.close_all, thread_sensitive=True)()
    return bodies, denied.status_code


async def _download_one_bp_output(owner, task_id: int, logical_name: str):
    """下载单个血压输出并返回 HTTP 状态与响应正文。"""
    client = AsyncClient()
    login = await client.post(
        f"/api/v1/{_CHANNEL}/auth/login",
        data=json.dumps({"username": owner.username, "password": _PASSWORD}),
        content_type="application/json",
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    response = await client.get(
        f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/{logical_name}/download",
        headers=headers,
    )
    body = response.content
    await sync_to_async(connections.close_all, thread_sensitive=True)()
    return response.status_code, body


def _run_once(consumer: str) -> None:
    """通过正式管理命令消费一条推理消息。"""
    call_command("run_inference", consumer=consumer, once=True, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_bp_input_failure_has_no_partial_result_and_protects_output_download(
    tmp_path: Path,
    bp_stream,
):
    """真实血压任务输入失败不生成指标，并拒绝本人及他人下载输出。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    artifact_root = tmp_path / "artifacts"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=artifact_root,
        MAX_UPLOAD_BYTES=1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        select_channel_model_version(
            channel=_CHANNEL,
            model_id=model.model_id,
            version_id=version.version_id,
        )
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            username=f"bp-workflow-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = user_model.objects.create_user(
            username=f"bp-workflow-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        task_id = _submit_invalid_ppg_task(
            user_id=owner.pk,
            model_id=model.model_id,
        )

        _run_once(f"bp-input-failure-{uuid4().hex[:12]}")
        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.INVALID_INPUT"
        assert task.failure_message == "PPG 必须完整提供一组 RGB 或 YUV 通道"
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        assert task.finished_at is not None
        output_root = (
            task_root
            / _CHANNEL
            / task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            / "outputs"
            / str(task.pk)
        )
        assert not output_root.exists()
        assert not output_root.is_symlink()

        async def query_and_download():
            """读取安全失败详情并分别尝试本人和其他账号下载。"""
            client = AsyncClient()
            owner_login = await client.post(
                f"/api/v1/{_CHANNEL}/auth/login",
                data=json.dumps({"username": owner.username, "password": _PASSWORD}),
                content_type="application/json",
            )
            owner_headers = {"Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"}
            detail = await client.get(
                f"/api/v1/{_CHANNEL}/tasks/{task_id}",
                headers=owner_headers,
            )
            owner_download = await client.get(
                f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/processed_ppg/download",
                headers=owner_headers,
            )
            other_login = await client.post(
                f"/api/v1/{_CHANNEL}/auth/login",
                data=json.dumps({"username": other.username, "password": _PASSWORD}),
                content_type="application/json",
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['data']['accessToken']}"}
            other_download = await client.get(
                f"/api/v1/{_CHANNEL}/tasks/{task_id}/outputs/processed_ppg/download",
                headers=other_headers,
            )
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            return detail, owner_download, other_download

        detail, owner_download, other_download = asyncio.run(query_and_download())

    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["status"] == Task.StatusChoices.FAILED
    assert detail_data["failureCode"] == "INFERENCE.INVALID_INPUT"
    assert detail_data["failureMessage"] == "PPG 必须完整提供一组 RGB 或 YUV 通道"
    assert detail_data["result"] is None
    assert not {"failureDetail", "profileSnapshot"}.intersection(detail_data)
    assert str(tmp_path) not in detail.content.decode("utf-8")
    assert owner_download.status_code == 404
    assert other_download.status_code == 404
    assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_bp_versions_freeze_profiles_publish_both_files_and_download_by_key(
    tmp_path: Path,
    bp_stream,
):
    """真实执行双模型血压版本，验证资料冻结、整组发布和权限下载。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    artifact_root = tmp_path / "artifacts"
    storage = LocalFileStorage(task_root, artifact_root)
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=artifact_root,
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version_a = _create_published_bp_version(
            tmp_path / "source-a",
            label="version-a",
            sbp_intercept=120.0,
            dbp_intercept=80.0,
        )
        _, version_b = _create_published_bp_version(
            tmp_path / "source-b",
            model=model,
            label="version-b",
            sbp_intercept=150.0,
            dbp_intercept=60.0,
        )
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            username=f"bp-success-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = user_model.objects.create_user(
            username=f"bp-success-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        profile_a = {
            "gender": 1,
            "birth_date": date(1990, 1, 1),
            "height_cm": "170.00",
            "weight_kg": "68.25",
        }
        profile_b = {
            "gender": 2,
            "birth_date": date(1985, 6, 15),
            "height_cm": "156.50",
            "weight_kg": "58.75",
        }
        ppg_a, acc_a = _bp_sample_inputs()
        old_created_at = timezone.now() - timedelta(days=2)

        _update_bp_profile(owner.pk, **profile_a)
        task_a1 = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version_a.version_id,
            ppg_content=ppg_a,
            acc_content=acc_a,
            created_at=old_created_at,
        )
        frozen_a1 = Task.objects.get(pk=task_a1).profile_snapshot
        _update_bp_profile(owner.pk, **profile_b)
        assert Task.objects.get(pk=task_a1).profile_snapshot == frozen_a1

        _update_bp_profile(owner.pk, **profile_a)
        task_b = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version_b.version_id,
            ppg_content=ppg_a,
            acc_content=acc_a,
        )
        _update_bp_profile(owner.pk, **profile_b)
        task_a2 = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version_a.version_id,
            ppg_content=_shift_ppg_timestamps(ppg_a, 5.0),
            acc_content=acc_a,
        )
        select_channel_model_version(
            channel=_CHANNEL,
            model_id=model.model_id,
            version_id=version_b.version_id,
        )
        _update_bp_profile(
            owner.pk,
            gender=1,
            birth_date=date(2000, 1, 1),
            height_cm="190.00",
            weight_kg="90.00",
        )

        task_ids = (task_a1, task_b, task_a2)
        expected_versions = {
            task_a1: version_a.version_id,
            task_b: version_b.version_id,
            task_a2: version_a.version_id,
        }
        expected_snapshots = {
            task_a1: frozen_a1,
            task_b: Task.objects.get(pk=task_b).profile_snapshot,
            task_a2: Task.objects.get(pk=task_a2).profile_snapshot,
        }
        for task_id in task_ids:
            task = Task.objects.get(pk=task_id)
            assert task.status == Task.StatusChoices.SUBMITTED
            input_files = list(TaskInputFile.objects.filter(task_id=task_id))
            assert {item.logical_name for item in input_files} == {"ppg", "acc"}
            input_date = task.created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            for item in input_files:
                parts = PurePosixPath(item.storage_key).parts
                assert parts[:2] == (_CHANNEL, input_date)
                assert storage.resolve_file(StorageArea.TASK_DATA, item.storage_key).is_file()

        for version in (version_a, version_b):
            artifacts = list(ModelArtifact.objects.filter(model_version_id=version.version_id))
            assert {artifact.logical_name for artifact in artifacts} == {
                "sbp",
                "dbp",
                "normalize",
            }
            for artifact in artifacts:
                parts = PurePosixPath(artifact.storage_key).parts
                assert tuple(unquote(part) for part in parts[:2]) == (
                    model.code,
                    version.version,
                )
                assert parts[-1].endswith(
                    ".csv" if artifact.logical_name == "normalize" else ".joblib"
                )
                assert storage.resolve_file(
                    StorageArea.MODEL_ARTIFACT,
                    artifact.storage_key,
                ).is_file()

        _run_once(f"bp-success-{uuid4().hex[:12]}")

        tasks = {task_id: Task.objects.get(pk=task_id) for task_id in task_ids}
        assert redis_client.xpending(stream, group)["pending"] == 0
        output_paths: dict[tuple[int, str], Path] = {}
        output_keys: set[str] = set()
        for task_id, task in tasks.items():
            assert task.status == Task.StatusChoices.SUCCEEDED
            assert task.model_version_id == expected_versions[task_id]
            assert task.profile_snapshot == expected_snapshots[task_id]
            assert task.result_payload["quality"]["score"] >= 70.0
            assert task.result_payload["quality"]["threshold"] == 70
            assert math.isfinite(task.result_payload["sbp"])
            assert math.isfinite(task.result_payload["dbp"])
            metrics = {
                item.metric.code: item.numeric_value
                for item in TaskMetric.objects.filter(task=task).select_related("metric")
            }
            assert metrics == {
                "sbp": task.result_payload["sbp"],
                "dbp": task.result_payload["dbp"],
            }
            output_files = task.result_payload["output_files"]
            assert set(output_files) == {"processed_ppg", "processed_scg"}
            execution_dirs = set()
            for logical_name, filename in (
                ("processed_ppg", "ppg-processed.csv"),
                ("processed_scg", "scg-processed.csv"),
            ):
                storage_key = output_files[logical_name]
                output_keys.add(storage_key)
                parts = PurePosixPath(storage_key).parts
                assert parts[:2] == (".executions", str(task.pk))
                assert parts[-1] == filename
                output_path = storage.resolve_file(StorageArea.TASK_DATA, storage_key)
                assert output_path.is_file()
                output_paths[(task_id, logical_name)] = output_path
                execution_dirs.add(output_path.parent.resolve())
            assert len(execution_dirs) == 1
            execution_dir = execution_dirs.pop()
            assert {path.name for path in execution_dir.iterdir()} == {
                "ppg-processed.csv",
                "scg-processed.csv",
            }
            output_root = _task_output_root(task_root, task)
            assert output_root.is_symlink()
            assert output_root.resolve() == execution_dir

        assert tasks[task_a1].created_at.astimezone(ZoneInfo("Asia/Shanghai")).date() < (
            tasks[task_a1].finished_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
        old_date_root = _task_output_root(task_root, tasks[task_a1])
        finish_date_root = (
            task_root
            / _CHANNEL
            / tasks[task_a1].finished_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            / "outputs"
            / str(task_a1)
        )
        assert old_date_root.is_symlink()
        assert not finish_date_root.exists()

        assert (
            tasks[task_b].result_payload["sbp"] - tasks[task_a1].result_payload["sbp"]
        ) == pytest.approx(30.0, abs=1e-6)
        assert (
            tasks[task_b].result_payload["dbp"] - tasks[task_a1].result_payload["dbp"]
        ) == pytest.approx(-20.0, abs=1e-6)
        assert (
            output_paths[(task_a1, "processed_ppg")].read_bytes()
            != output_paths[(task_a2, "processed_ppg")].read_bytes()
        )

        bodies, denied_status = asyncio.run(_download_bp_outputs(owner, other, task_ids))
        assert denied_status == 404
        assert len(output_keys) == len(task_ids) * 2
        for task_id in task_ids:
            for logical_name, filename, headers in (
                ("processed_ppg", "ppg-processed.csv", ["timestamp", "ppg"]),
                (
                    "processed_scg",
                    "scg-processed.csv",
                    ["timestamp", "scgX", "scgY", "scgZ"],
                ),
            ):
                body = bodies[(task_id, logical_name)]
                assert body == output_paths[(task_id, logical_name)].read_bytes()
                reader = csv.DictReader(StringIO(body.decode("utf-8")))
                assert reader.fieldnames == headers
                rows = list(reader)
                assert len(rows) > 15
                assert output_paths[(task_id, logical_name)].name == filename


@pytest.mark.django_db(transaction=True)
def test_bp_quality_rejection_does_not_publish_files_or_metrics(
    tmp_path: Path,
    bp_stream,
):
    """真实血压流水线质量拒绝不登记指标或正式处理文件。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        user = get_user_model().objects.create_user(
            username=f"bp-quality-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        task_id = _submit_bp_task(
            user_id=user.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=synthetic_ppg_csv(heart_rate_hz=1.2, noise_amplitude=30.0),
            acc_content=_synthetic_acc_csv(heart_rate_hz=1.2),
        )
        _run_once(f"bp-quality-{uuid4().hex[:12]}")
        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
        assert task.failure_detail["quality"]["reason"] == "LOW_SCORE"
        assert task.failure_detail["quality"]["details"]["score"] < 70.0
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        assert not _task_output_root(task_root, task).exists()
        assert not _task_output_root(task_root, task).is_symlink()
        assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
def test_bp_timeout_has_no_authoritative_outputs(tmp_path: Path, bp_stream):
    """真实血压预测超过硬时限后安全失败且不登记部分结果。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(
            tmp_path / "source",
            slow_models=True,
        )
        user = get_user_model().objects.create_user(
            username=f"bp-timeout-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        ppg_sample, acc_sample = _bp_sample_inputs()
        task_id = _submit_bp_task(
            user_id=user.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=ppg_sample,
            acc_content=acc_sample,
        )
        with override_settings(INFERENCE_EXECUTION_TIMEOUT_SECONDS=1):
            _run_once(f"bp-timeout-{uuid4().hex[:12]}")
        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.FAILED
        assert task.failure_code == "INFERENCE.EXECUTION_TIMEOUT"
        assert task.failure_message == "推理执行超时"
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        assert task.attempt_count == 1
        assert not _task_output_root(task_root, task).exists()
        assert not _task_output_root(task_root, task).is_symlink()
        assert redis_client.xpending(stream, group)["pending"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("recovery_path", ("download", "terminal-message"))
def test_bp_publish_failure_recovers_without_rerunning(
    tmp_path: Path,
    bp_stream,
    monkeypatch: pytest.MonkeyPatch,
    recovery_path: str,
) -> None:
    """血压双文件发布失败后，下载或终态重放可幂等恢复原执行结果。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        owner = get_user_model().objects.create_user(
            username=f"bp-recovery-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = get_user_model().objects.create_user(
            username=f"bp-recovery-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        _update_bp_profile(
            owner.pk,
            gender=1,
            birth_date=date(1990, 1, 1),
            height_cm="170.00",
            weight_kg="68.25",
        )
        ppg_sample, acc_sample = _bp_sample_inputs()
        task_id = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=ppg_sample,
            acc_content=acc_sample,
        )
        storage = LocalFileStorage(task_root, tmp_path / "artifacts")
        actual_replace = os.replace
        replace_attempts = 0

        def fail_first_replace(source, destination):
            """只让首次正式目录发布失败，后续恢复使用真实原子替换。"""
            nonlocal replace_attempts
            replace_attempts += 1
            if replace_attempts == 1:
                raise OSError("PRIVATE-PUBLISH-FAILURE")
            actual_replace(source, destination)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "replace", fail_first_replace)
            _run_once(f"bp-recovery-{uuid4().hex[:12]}")

        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.SUCCEEDED
        assert task.attempt_count == 1
        assert replace_attempts == 1
        output_keys = dict(task.result_payload["output_files"])
        output_paths = {
            logical_name: storage.resolve_file(StorageArea.TASK_DATA, storage_key)
            for logical_name, storage_key in output_keys.items()
        }
        assert set(output_paths) == {"processed_ppg", "processed_scg"}
        assert output_paths["processed_ppg"].parent == output_paths["processed_scg"].parent
        execution_dir = output_paths["processed_ppg"].parent
        output_root = _task_output_root(task_root, task)
        assert not output_root.is_symlink()
        result_snapshot = dict(task.result_payload)
        metric_snapshot = {
            item.metric.code: item.numeric_value
            for item in TaskMetric.objects.filter(task=task).select_related("metric")
        }
        output_snapshot = {
            logical_name: path.read_bytes() for logical_name, path in output_paths.items()
        }

        if recovery_path == "download":
            bodies, denied_status = asyncio.run(_download_bp_outputs(owner, other, (task_id,)))
            assert denied_status == 404
            assert {
                logical_name: bodies[(task_id, logical_name)] for logical_name in output_paths
            } == output_snapshot
            repeated_bodies, repeated_denied = asyncio.run(
                _download_bp_outputs(owner, other, (task_id,))
            )
            assert repeated_denied == 404
            assert {
                logical_name: repeated_bodies[(task_id, logical_name)]
                for logical_name in output_paths
            } == output_snapshot
        else:

            def reject_claim(*, task_id):
                """拒绝终态消息重复领取成功任务。"""
                raise AssertionError("终态消息重放不得重新领取或执行血压任务")

            monkeypatch.setattr(
                inference_command,
                "claim_task_for_execution",
                reject_claim,
            )
            for _ in range(2):
                redis_client.xadd(stream, {"task_id": str(task_id)})
                _run_once(f"bp-terminal-recovery-{uuid4().hex[:12]}")
                assert redis_client.xpending(stream, group)["pending"] == 0

        task.refresh_from_db()
        assert task.status == Task.StatusChoices.SUCCEEDED
        assert task.attempt_count == 1
        assert task.result_payload == result_snapshot
        assert {
            item.metric.code: item.numeric_value
            for item in TaskMetric.objects.filter(task=task).select_related("metric")
        } == metric_snapshot
        assert output_root.is_symlink()
        assert output_root.resolve() == execution_dir.resolve()
        assert {path.name for path in output_root.iterdir()} == {
            "ppg-processed.csv",
            "scg-processed.csv",
        }
        assert {
            logical_name: path.read_bytes() for logical_name, path in output_paths.items()
        } == output_snapshot


@pytest.mark.django_db(transaction=True)
def test_bp_downloads_actual_files_when_recovery_fails_and_hides_missing_output(
    tmp_path: Path,
    bp_stream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式目录持续恢复失败时按权威文件键下载，缺失文件仍安全返回 404。"""
    _redis_client, _, _ = bp_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        owner = get_user_model().objects.create_user(
            username=f"bp-download-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        other = get_user_model().objects.create_user(
            username=f"bp-download-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        _update_bp_profile(
            owner.pk,
            gender=1,
            birth_date=date(1990, 1, 1),
            height_cm="170.00",
            weight_kg="68.25",
        )
        ppg_sample, acc_sample = _bp_sample_inputs()
        task_id = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=ppg_sample,
            acc_content=acc_sample,
        )
        storage = LocalFileStorage(task_root, tmp_path / "artifacts")
        replace_attempts = 0

        def fail_replace(source, destination):
            """模拟正式目录发布与后续恢复都遇到的文件系统错误。"""
            nonlocal replace_attempts
            replace_attempts += 1
            raise OSError("PRIVATE-PUBLISH-FAILURE")

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "replace", fail_replace)
            _run_once(f"bp-download-failure-{uuid4().hex[:12]}")
            task = Task.objects.get(pk=task_id)
            assert task.status == Task.StatusChoices.SUCCEEDED
            assert task.attempt_count == 1
            result_snapshot = dict(task.result_payload)
            metric_snapshot = {
                item.metric.code: item.numeric_value
                for item in TaskMetric.objects.filter(task=task).select_related("metric")
            }
            output_paths = {
                logical_name: storage.resolve_file(
                    StorageArea.TASK_DATA,
                    storage_key,
                )
                for logical_name, storage_key in task.result_payload["output_files"].items()
            }
            output_snapshot = {
                logical_name: path.read_bytes() for logical_name, path in output_paths.items()
            }
            bodies, denied_status = asyncio.run(_download_bp_outputs(owner, other, (task_id,)))
            assert denied_status == 404
            assert {
                logical_name: bodies[(task_id, logical_name)] for logical_name in output_paths
            } == output_snapshot
            assert replace_attempts >= 3

        output_root = _task_output_root(task_root, task)
        assert not output_root.is_symlink()
        ppg_path = output_paths["processed_ppg"]
        ppg_path.unlink()
        status, body = asyncio.run(_download_one_bp_output(owner, task_id, "processed_ppg"))
        assert status == 404
        assert str(task_root).encode() not in body
        task.refresh_from_db()
        assert task.status == Task.StatusChoices.SUCCEEDED
        assert task.attempt_count == 1
        assert task.result_payload == result_snapshot
        assert {
            item.metric.code: item.numeric_value
            for item in TaskMetric.objects.filter(task=task).select_related("metric")
        } == metric_snapshot
        assert not output_root.is_symlink()
        assert output_paths["processed_scg"].read_bytes() == output_snapshot["processed_scg"]


@pytest.mark.django_db(transaction=True)
def test_bp_metric_constraint_rolls_back_success_and_both_output_files(
    tmp_path: Path,
    bp_stream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双模型结果写入指标约束失败时回滚成功状态与正式文件发布。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        owner = get_user_model().objects.create_user(
            username=f"bp-rollback-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        _update_bp_profile(
            owner.pk,
            gender=1,
            birth_date=date(1990, 1, 1),
            height_cm="170.00",
            weight_kg="68.25",
        )
        ppg_sample, acc_sample = _bp_sample_inputs()
        task_id = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=ppg_sample,
            acc_content=acc_sample,
        )
        project_metrics = execution_application._project_selected_metrics

        def duplicate_metric(*, version_id, algorithm, values):
            """触发真实的任务指标唯一约束以验证成功事务回滚。"""
            projected = project_metrics(
                version_id=version_id,
                algorithm=algorithm,
                values=values,
            )
            assert len(projected) == 2
            return (projected[0], *projected)

        with monkeypatch.context() as patcher:
            patcher.setattr(
                execution_application,
                "_project_selected_metrics",
                duplicate_metric,
            )
            with pytest.raises(
                CommandError,
                match="推理消费者的数据库操作失败",
            ):
                _run_once(f"bp-rollback-{uuid4().hex[:12]}")

        task = Task.objects.get(pk=task_id)
        assert task.status == Task.StatusChoices.RUNNING
        assert task.failure_code is None
        assert task.result_payload is None
        assert task.finished_at is None
        assert task.execution_token is not None
        assert task.attempt_count == 1
        assert not TaskMetric.objects.filter(task=task).exists()
        execution_dir = task_root / ".executions" / str(task.pk) / task.execution_token.hex
        assert {path.name for path in execution_dir.iterdir()} == {
            "ppg-processed.csv",
            "scg-processed.csv",
        }
        output_root = _task_output_root(task_root, task)
        assert not output_root.exists()
        assert not output_root.is_symlink()
        assert redis_client.xpending(stream, group)["pending"] == 1


@pytest.mark.django_db(transaction=True)
def test_bp_late_execution_cannot_replace_authoritative_outputs(
    tmp_path: Path,
    bp_stream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """血压执行失租并晚到时不能提交结果或改写已有正式双文件目录。"""
    redis_client, stream, group = bp_stream
    task_root = tmp_path / "tasks"
    replacement_token = uuid4()
    claimed_tokens = []
    with override_settings(
        TASK_DATA_ROOT=task_root,
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
        INFERENCE_LEASE_SECONDS=0.3,
        INFERENCE_HEARTBEAT_SECONDS=0.03,
    ):
        Channel.objects.get_or_create(label=_CHANNEL)
        _create_bp_metrics()
        model, version = _create_published_bp_version(tmp_path / "source")
        owner = get_user_model().objects.create_user(
            username=f"bp-stale-owner-{uuid4().hex}",
            password=_PASSWORD,
        )
        _update_bp_profile(
            owner.pk,
            gender=1,
            birth_date=date(1990, 1, 1),
            height_cm="170.00",
            weight_kg="68.25",
        )
        ppg_sample, acc_sample = _bp_sample_inputs()
        task_id = _submit_bp_task(
            user_id=owner.pk,
            model_id=model.model_id,
            version_id=version.version_id,
            ppg_content=ppg_sample,
            acc_content=acc_sample,
        )
        task = Task.objects.get(pk=task_id)
        storage = LocalFileStorage(task_root, tmp_path / "artifacts")
        old_output_dir = storage.create_execution_output_dir(task_id, uuid4())
        old_contents = {
            "ppg-processed.csv": b"previous-published-ppg",
            "scg-processed.csv": b"previous-published-scg",
        }
        for filename, contents in old_contents.items():
            (old_output_dir / filename).write_bytes(contents)
        output_root = _task_output_root(task_root, task)
        output_root.parent.mkdir(parents=True, exist_ok=True)
        output_root.symlink_to(old_output_dir, target_is_directory=True)
        original_claim = inference_command.claim_task_for_execution

        def capture_claim(*, task_id):
            """记录当前消费者领取的令牌以模拟同一执行晚到。"""
            token = original_claim(task_id=task_id)
            if task_id == task_id_expected:
                claimed_tokens.append(token)
            return token

        task_id_expected = task_id

        def revoke_lease(*, task_id, execution_token):
            """在工作进程运行期间更换数据库令牌并拒绝旧租约续期。"""
            updated = Task.objects.filter(
                pk=task_id,
                status=Task.StatusChoices.RUNNING,
                execution_token=execution_token,
            ).update(
                execution_token=replacement_token,
                lease_expires_at=timezone.now() + timedelta(minutes=1),
            )
            assert updated == 1
            return False

        monkeypatch.setattr(
            inference_command,
            "claim_task_for_execution",
            capture_claim,
        )
        monkeypatch.setattr(
            inference_command,
            "renew_task_execution_lease",
            revoke_lease,
        )
        _run_once(f"bp-stale-{uuid4().hex[:12]}")

        task.refresh_from_db()
        assert len(claimed_tokens) == 1
        stale_token = claimed_tokens[0]
        assert task.status == Task.StatusChoices.RUNNING
        assert task.execution_token == replacement_token
        assert stale_token != replacement_token
        assert task.attempt_count == 1
        assert task.result_payload is None
        assert task.failure_code is None
        assert not TaskMetric.objects.filter(task=task).exists()
        stale_output_dir = task_root / ".executions" / str(task.pk) / stale_token.hex
        assert {path.name for path in stale_output_dir.iterdir()} == {
            "ppg-processed.csv",
            "scg-processed.csv",
        }
        stale_keys = {
            "processed_ppg": (f".executions/{task.pk}/{stale_token.hex}/ppg-processed.csv"),
            "processed_scg": (f".executions/{task.pk}/{stale_token.hex}/scg-processed.csv"),
        }
        assert not task_persistence.save_task_execution_success(
            task_id=task_id,
            execution_token=stale_token,
            result_payload={
                "sbp": 120.0,
                "dbp": 80.0,
                "quality": {"score": 90.0, "threshold": 70},
                "output_files": stale_keys,
            },
            metrics=(),
        )
        task.refresh_from_db()
        assert task.status == Task.StatusChoices.RUNNING
        assert task.execution_token == replacement_token
        assert task.result_payload is None
        assert not TaskMetric.objects.filter(task=task).exists()
        assert output_root.is_symlink()
        assert output_root.resolve() == old_output_dir.resolve()
        assert {path.name: path.read_bytes() for path in output_root.iterdir()} == old_contents
        assert redis_client.xpending(stream, group)["pending"] == 1
