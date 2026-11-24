"""验证场景成员实际文件、下载授权及终态发布恢复不重算。"""

import asyncio
import csv
import json
import os
from copy import deepcopy
from datetime import date
from io import StringIO

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.test import AsyncClient
from health_inference import AlgorithmRuntime, InferenceError
from health_service.application.catalog import select_channel_model_version
from health_service.application.execution import save_task_execution_success
from health_service.management.commands import run_inference as inference_command
from health_service.models import Task, TaskMetric

from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.integration.workflows.test_bp_inference_consumer import (
    _bp_sample_inputs,
    _update_bp_profile,
)
from tests.integration.workflows.test_diabetes_inference_consumer import _task_output_root
from tests.integration.workflows.test_scene_support import (
    BP_SCENE,
    CHANNEL,
    PASSWORD,
    PPG_SCENE,
    artifact_path,
    create_scene_version,
    prepare_scene_result,
    run_scene_consumer,
    standalone_member_output,
    submit_scene_task,
)
from tests.integration.workflows.test_scene_support import (
    scene_environment as scene_environment,
)
from tests.integration.workflows.test_spo2_inference_consumer import _response_body

pytestmark = pytest.mark.django_db(transaction=True)


async def _download_scene(environment, task_id, logical_names, missing_names=()):
    """检查本人字节、非本人及跨渠道隐藏、失败和未登记逻辑名拒绝。"""
    client = AsyncClient()

    async def login(user, channel):
        """签发实际绑定账号和渠道的 Access Token。"""
        response = await client.post(
            f"/api/v1/{channel}/auth/login",
            data=json.dumps({"username": user.username, "password": PASSWORD}),
            content_type="application/json",
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}

    try:
        owner = await login(environment.owner, CHANNEL)
        other = await login(environment.other, CHANNEL)
        cross_channel = await login(environment.owner, "wxapp")
        bodies = {}
        for name in logical_names:
            response = await client.get(
                f"/api/v1/{CHANNEL}/tasks/{task_id}/outputs/{name}/download", headers=owner
            )
            assert response.status_code == 200
            bodies[name] = await _response_body(response)
            for channel, headers in ((CHANNEL, other), ("wxapp", cross_channel)):
                denied = await client.get(
                    f"/api/v1/{channel}/tasks/{task_id}/outputs/{name}/download", headers=headers
                )
                assert denied.status_code == 404
                assert str(environment.root).encode() not in denied.content
        for name in (*missing_names, "unregistered", "processed_ppg"):
            for channel, headers in (
                (CHANNEL, owner), (CHANNEL, other), ("wxapp", cross_channel)
            ):
                response = await client.get(
                    f"/api/v1/{channel}/tasks/{task_id}/outputs/{name}/download", headers=headers
                )
                assert response.status_code == 404
        detail = await client.get(f"/api/v1/{CHANNEL}/tasks/{task_id}", headers=owner)
        assert detail.status_code == 200
        return bodies, detail.json()["data"]
    finally:
        await sync_to_async(connections.close_all, thread_sensitive=True)()


def _file_bytes(directory):
    """检查正式目录的整个实际文件树，而不只检查引用映射。"""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _projected_metrics(task):
    """读取权威成功结果的数据库指标投影。"""
    return {
        metric.metric.code: metric.numeric_value
        for metric in TaskMetric.objects.filter(task=task).select_related("metric")
    }


@pytest.mark.parametrize("algorithm_key", [PPG_SCENE, BP_SCENE])
def test_scene_files_match_real_members_and_protected_downloads(scene_environment, algorithm_key):
    """三份同名 PPG 或双传感器输出保持原字节、表头和成员目录且不可越权。"""
    environment = scene_environment
    model, version = create_scene_version(environment, algorithm_key=algorithm_key)
    if algorithm_key == BP_SCENE:
        _update_bp_profile(
            environment.owner.pk,
            gender=1,
            birth_date=date(1990, 1, 1),
            height_cm="170.00",
            weight_kg="68.25",
        )
        ppg, acc = _bp_sample_inputs()
    else:
        ppg, acc = synthetic_ppg_csv(heart_rate_hz=1.8), None
    task = submit_scene_task(environment, model, version, ppg_content=ppg, acc_content=acc)
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == "ALL_SUCCEEDED"
    expected_bytes = {}
    expected_tree = {}
    expected_metrics = {}
    for member_name, member in task.result_payload["results"].items():
        standalone = standalone_member_output(environment, task, member_name, member["algorithmKey"])
        assert member["result"] == standalone.payload
        assert "output_files" not in member["result"]
        expected_metrics.update(standalone.metrics)
        for name, path in standalone.output_files.items():
            prefixed = f"{member_name}_{name}"
            body = path.read_bytes()
            expected_bytes[prefixed] = body
            expected_tree[f"{member_name}/{path.name}"] = body
            registered = environment.storage.task_data_root / task.result_payload["output_files"][prefixed]
            assert registered.parent.name == member_name
            assert registered.name == path.name
            assert registered.read_bytes() == body
            header = next(csv.reader(StringIO(body.decode("utf-8"))))
            if member_name == "bp":
                assert header == (["timestamp", "ppg"] if name == "processed_ppg" else ["timestamp", "scgX", "scgY", "scgZ"])
            else:
                timestamp_column = "timestamp" if member_name in {"hr", "br"} else "timestampFrame"
                assert header == [timestamp_column, "rsp" if name == "processed_rsp" else "ppg"]
    assert _projected_metrics(task) == expected_metrics
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert formal.is_symlink()
    assert _file_bytes(formal) == expected_tree
    expected_members = {path.split("/")[0] for path in expected_tree}
    assert {path.name for path in formal.iterdir()} == expected_members
    if algorithm_key == PPG_SCENE:
        assert len([name for name in expected_tree if name.endswith("/ppg-processed.csv")]) == 3
    downloaded, detail = asyncio.run(_download_scene(environment, task.pk, expected_bytes))
    assert downloaded == expected_bytes
    assert detail["status"] == "SUCCEEDED"
    assert detail["result"]["results"] == task.result_payload["results"]
    assert detail["failureCode"] is detail["failureMessage"] is detail["qualityFailure"] is None


def test_failed_member_written_files_are_removed_before_publication(scene_environment, monkeypatch):
    """真实 diabetes 已写文件后失败，正式树及本人下载都不暴露其残留。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    run_actual = AlgorithmRuntime.run
    written_before_failure = {}

    def reject_after_actual_output(runtime, inference_input):
        """让原成员实际计算并写盘后在调用边界故障，不返回模拟结果。"""
        output = run_actual(runtime, inference_input)
        if runtime.definition.key == "ppg.active.offline.diabetes.v1":
            written_before_failure.update(
                {name: path.read_bytes() for name, path in output.output_files.items()}
            )
            raise InferenceError("RESULT.INVALID_OUTPUT", "PRIVATE-written-member-output")
        return output

    with monkeypatch.context() as patcher:
        patcher.setattr(AlgorithmRuntime, "run", reject_after_actual_output)
        token, result = prepare_scene_result(task)
    assert written_before_failure["processed_ppg"]
    assert result.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert result.payload["results"]["diabetes"]["status"] == "FAILED"
    assert "PRIVATE" not in str(result.payload)
    execution_dir = environment.storage.task_data_root / ".executions" / str(task.pk) / token.hex
    assert not (execution_dir / "diabetes").exists()
    assert {path.name for path in execution_dir.iterdir()} == {"hr", "br", "spo2"}
    assert save_task_execution_success(task_id=task.pk, execution_token=token, result=result)
    task.refresh_from_db()
    formal = _task_output_root(environment.storage.task_data_root, task)
    expected_tree = {
        "hr/ppg-processed.csv", "br/rsp-processed.csv", "spo2/ppg-processed.csv"
    }
    assert set(_file_bytes(formal)) == expected_tree
    registered = {
        name: (environment.storage.task_data_root / key).read_bytes()
        for name, key in task.result_payload["output_files"].items()
    }
    downloaded, detail = asyncio.run(
        _download_scene(environment, task.pk, registered, ("diabetes_processed_ppg",))
    )
    assert downloaded == registered
    assert detail["result"]["outcome"] == "PARTIALLY_SUCCEEDED"
    assert detail["result"]["results"]["diabetes"]["result"] is None
    assert detail["failureCode"] is detail["qualityFailure"] is None


@pytest.mark.parametrize("recovery_path", ["download", "terminal-message"])
@pytest.mark.parametrize("partial", [False, True])
def test_scene_publication_recovers_immutable_files_without_recalculation(
    scene_environment, monkeypatch, recovery_path, partial
):
    """成功及部分成功在发布失败后仅恢复原链接，不重算、不换版本、不改文件。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    if partial:
        artifact_path(environment, version, "diabetes").write_bytes(b"invalid-initial-resource")
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    replace_actual = os.replace
    replace_attempts = 0

    def fail_first_publication(source, destination):
        """仅首个正式目录原子替换失败，后续恢复仍执行真实替换。"""
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            raise OSError("PRIVATE-first-publication-failure")
        return replace_actual(source, destination)

    with monkeypatch.context() as patcher:
        patcher.setattr(os, "replace", fail_first_publication)
        run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == ("PARTIALLY_SUCCEEDED" if partial else "ALL_SUCCEEDED")
    assert replace_attempts == 1
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    payload = deepcopy(task.result_payload)
    metrics = _projected_metrics(task)
    paths = {
        name: environment.storage.task_data_root / key
        for name, key in payload["output_files"].items()
    }
    bytes_before = {name: path.read_bytes() for name, path in paths.items()}
    stat_before = {name: (path.stat().st_ino, path.stat().st_mtime_ns) for name, path in paths.items()}
    _, replacement = create_scene_version(environment, model=model, bias=96.0, logit=1.0)
    select_channel_model_version(
        channel=CHANNEL, model_id=model.model_id, version_id=replacement.version_id
    )

    def reject_reclaim(*, task_id):
        """已成功场景的终态消息不能重新领取执行权。"""
        raise AssertionError("终态场景不得重新领取或计算")

    monkeypatch.setattr(inference_command, "claim_task_for_execution", reject_reclaim)
    if recovery_path == "download":
        downloaded, _ = asyncio.run(_download_scene(environment, task.pk, paths))
        assert downloaded == bytes_before
    else:
        for _ in range(2):
            environment.redis.xadd(environment.stream, {"task_id": str(task.pk)})
            run_scene_consumer()
            assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 0
    task.refresh_from_db()
    assert task.attempt_count == 1
    assert task.model_version_id == version.version_id
    assert task.result_payload == payload
    assert _projected_metrics(task) == metrics
    assert {name: path.read_bytes() for name, path in paths.items()} == bytes_before
    assert {name: (path.stat().st_ino, path.stat().st_mtime_ns) for name, path in paths.items()} == stat_before
    assert formal.is_symlink()
    expected_tree = {f"{path.parent.name}/{path.name}": bytes_before[name] for name, path in paths.items()}
    assert _file_bytes(formal) == expected_tree
    assert formal.resolve() == next(iter(paths.values())).parent.parent.resolve()


@pytest.mark.parametrize("algorithm_key", [PPG_SCENE, BP_SCENE])
def test_all_failed_scene_has_no_formal_directory_and_replay_does_not_compute(
    scene_environment, monkeypatch, algorithm_key
):
    """全失败保留可查询汇总，但本人、非本人及跨渠道均得不到任何输出。"""
    environment = scene_environment
    model, version = create_scene_version(environment, algorithm_key=algorithm_key)
    task = submit_scene_task(
        environment,
        model,
        version,
        ppg_content="timestampFrame,avgR,avgG,avgB\n0,120,128,136\n",
        acc_content="timestamp,accX,accY,accZ\n0,0,0,0\n" if algorithm_key == BP_SCENE else None,
    )
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == "ALL_FAILED"
    assert "output_files" not in task.result_payload
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    payload = deepcopy(task.result_payload)

    def reject_reclaim(*, task_id):
        """全失败也是已提交成功终态，不能再次执行成员。"""
        raise AssertionError("ALL_FAILED 终态不得重新计算")

    monkeypatch.setattr(inference_command, "claim_task_for_execution", reject_reclaim)
    for _ in range(2):
        environment.redis.xadd(environment.stream, {"task_id": str(task.pk)})
        run_scene_consumer()
    names = (
        ("bp_processed_ppg", "bp_processed_scg")
        if algorithm_key == BP_SCENE
        else ("hr_processed_ppg", "br_processed_rsp", "spo2_processed_ppg", "diabetes_processed_ppg")
    )
    bodies, detail = asyncio.run(_download_scene(environment, task.pk, (), names))
    assert bodies == {}
    assert detail["result"] == payload
    assert detail["failureCode"] is detail["failureMessage"] is detail["qualityFailure"] is None
    task.refresh_from_db()
    assert task.attempt_count == 1
    assert task.result_payload == payload
    assert not TaskMetric.objects.filter(task=task).exists()
    assert not formal.exists() and not formal.is_symlink()
    assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 0
