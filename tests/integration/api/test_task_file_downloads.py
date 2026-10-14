"""验证普通渠道任务输入历史文件与成功输出的授权下载。"""

import asyncio
import json
from collections.abc import Mapping
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import AsyncClient, override_settings
from django.utils import timezone

from health_inference import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    register_algorithm,
)
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.execution import save_task_execution_success
from health_service.application.tasks import create_task_draft, upload_task_input
from health_service.infrastructure.inference_adapter import (
    clear_version_runtime_cache,
    run_task_inference,
)
from health_service.infrastructure.storage import LocalFileStorage, StorageArea
from health_service.models import Task, TaskInputFile

_PASSWORD = "Willow-river-458!strong"


def _accept_empty_parameters(params: Mapping[str, object]) -> None:
    """为下载回归执行器声明无参数契约。"""
    if params:
        raise ValueError("参数必须为空")


def _write_report(inference_input: InferenceInput) -> InferenceOutput:
    """读取实际选中输入并写入本次执行独有的报告文件。"""
    source = inference_input.files["source"].read_bytes()
    report = inference_input.output_dir / "report.csv"
    report.write_bytes(b"processed:" + source)
    return InferenceOutput(
        payload={"source_bytes": len(source)},
        metrics={},
        output_files={"report": report},
    )


def _validate_report(output: InferenceOutput) -> None:
    """确保报告逻辑名指向本次执行实际生成的文件。"""
    report = output.output_files.get("report")
    if not isinstance(report, Path) or not report.is_file():
        raise ValueError("报告文件无效")


def _create_download_task(tmp_path: Path, *, username: str):
    """建立带历史输入和真实执行输出的成功任务。"""
    algorithm_key = f"test.download.{uuid4().hex}"
    register_algorithm(
        AlgorithmDefinition(
            key=algorithm_key,
            is_example=False,
            required_inputs=("source",),
            required_artifacts=(),
            required_profile_fields=(),
            parameter_validator=_accept_empty_parameters,
            result_validator=_validate_report,
            runner=_write_report,
            metric_contracts=(),
        )
    )
    clear_version_runtime_cache()

    user = get_user_model().objects.create_user(
        username=username,
        password=_PASSWORD,
    )
    model = create_model(
        code=f"download-{uuid4().hex}",
        name="Download test model",
        description=None,
    )
    draft_version = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=algorithm_key,
        params=None,
    )
    version = publish_model_version(version_id=draft_version.version_id)
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=version.version_id,
    )
    task_draft = create_task_draft(
        user_id=user.pk,
        channel="ios-cn",
        model_id=model.model_id,
    )
    historical_file = upload_task_input(
        task_id=task_draft.task_id,
        user_id=user.pk,
        channel="ios-cn",
        logical_name="source",
        source=BytesIO(b"historical input"),
    )
    current_file = upload_task_input(
        task_id=task_draft.task_id,
        user_id=user.pk,
        channel="ios-cn",
        logical_name="source",
        source=BytesIO(b"current input"),
    )

    now = timezone.now()
    execution_token = uuid4()
    Task.objects.filter(pk=task_draft.task_id).update(
        status=Task.StatusChoices.RUNNING,
        profile_snapshot={},
        submitted_at=now,
        started_at=now,
        execution_token=execution_token,
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=1,
    )
    try:
        result = run_task_inference(
            task_id=task_draft.task_id,
            execution_token=execution_token,
        )
        if result is None or not save_task_execution_success(
            task_id=task_draft.task_id,
            execution_token=execution_token,
            result=result,
        ):
            raise AssertionError("示例执行未提交成功结果")
    finally:
        clear_version_runtime_cache()

    task = Task.objects.get(pk=task_draft.task_id)
    output_key = task.result_payload["output_files"]["report"]
    output_path = LocalFileStorage(
        tmp_path / "tasks",
        tmp_path / "artifacts",
    ).resolve_file(
        area=StorageArea.TASK_DATA,
        storage_key=output_key,
    )
    return user, task, historical_file, current_file, output_path


async def _login(client: AsyncClient, channel: str, username: str):
    """通过真实普通渠道登录接口获取 Access。"""
    return await client.post(
        f"/api/v1/{channel}/auth/login",
        data=json.dumps({"username": username, "password": _PASSWORD}),
        content_type="application/json",
    )


async def _response_body(response) -> bytes:
    """读取普通或流式 HTTP 响应的实际字节。"""
    if not response.streaming:
        return response.content
    content = response.streaming_content
    if hasattr(content, "__aiter__"):
        return b"".join([chunk async for chunk in content])
    return b"".join(content)


async def _close_worker_database_connection() -> None:
    """关闭异步请求同步线程持有的数据库连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()


@pytest.mark.django_db(transaction=True)
def test_download_routes_serve_historical_input_and_real_algorithm_output(tmp_path):
    """HTTP 下载返回历史输入与执行器生成的原始输出字节。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        user, task, historical, current, output_path = _create_download_task(
            tmp_path,
            username=f"download-owner-{uuid4().hex}",
        )

        async def exercise_api():
            """通过普通渠道令牌请求两类文件并读取流式响应。"""
            client = AsyncClient()
            login = await _login(client, "ios-cn", user.username)
            headers = {
                "Authorization": f"Bearer {login.json()['data']['accessToken']}"
            }
            historical_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task.pk}/files/{historical.file_id}/download",
                headers=headers,
            )
            current_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task.pk}/files/{current.file_id}/download",
                headers=headers,
            )
            output_response = await client.get(
                f"/api/v1/ios-cn/tasks/{task.pk}/outputs/report/download",
                headers=headers,
            )
            bodies = (
                await _response_body(historical_response),
                await _response_body(current_response),
                await _response_body(output_response),
            )
            headers_out = (
                historical_response["Content-Type"],
                current_response["Content-Type"],
                output_response["Content-Type"],
                output_response["Content-Disposition"],
            )
            await _close_worker_database_connection()
            return (
                historical_response.status_code,
                current_response.status_code,
                output_response.status_code,
                bodies,
                headers_out,
            )

        historical_status, current_status, output_status, bodies, headers_out = (
            asyncio.run(exercise_api())
        )

    assert (historical_status, current_status, output_status) == (200, 200, 200)
    assert bodies == (
        b"historical input",
        b"current input",
        b"processed:current input",
    )
    assert all(content_type == "application/octet-stream" for content_type in headers_out[:3])
    assert "application/json" not in headers_out[2]
    assert "attachment" in headers_out[3]
    assert output_path.read_bytes() == b"processed:current input"


@pytest.mark.django_db(transaction=True)
def test_download_routes_hide_unauthorized_missing_and_non_success_files(tmp_path):
    """拒绝越权、缺失文件及失败或失租执行的孤儿输出且不泄露路径。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        user, task, historical, _, _ = _create_download_task(
            tmp_path,
            username=f"download-owner-{uuid4().hex}",
        )
        other_user = get_user_model().objects.create_user(
            username=f"download-other-{uuid4().hex}",
            password=_PASSWORD,
        )
        now = timezone.now()
        failed_task = Task.objects.create(
            user=user,
            channel_id=task.channel_id,
            model_version_id=task.model_version_id,
            status=Task.StatusChoices.FAILED,
            profile_snapshot={},
            submitted_at=now,
            finished_at=now,
            failure_code="INFERENCE.INVALID_INPUT",
            failure_message="输入无效",
        )
        stale_token = uuid4()
        stale_task = Task.objects.create(
            user=user,
            channel_id=task.channel_id,
            model_version_id=task.model_version_id,
            status=Task.StatusChoices.RUNNING,
            profile_snapshot={},
            submitted_at=now,
            started_at=now,
            execution_token=stale_token,
            lease_expires_at=now - timedelta(seconds=1),
            attempt_count=1,
        )
        storage = LocalFileStorage(
            tmp_path / "tasks",
            tmp_path / "artifacts",
        )
        failed_output = storage.create_execution_output_dir(failed_task.pk, uuid4())
        (failed_output / "report.csv").write_bytes(b"orphan failure output")
        stale_output = storage.create_execution_output_dir(stale_task.pk, stale_token)
        (stale_output / "report.csv").write_bytes(b"orphan stale output")
        historical_record = TaskInputFile.objects.get(pk=historical.file_id)
        missing_input = tmp_path / "tasks" / historical_record.storage_key
        missing_input.unlink()

        async def exercise_api():
            """验证账号、来源渠道、逻辑名和文件存在性边界。"""
            client = AsyncClient()
            owner_login = await _login(client, "ios-cn", user.username)
            other_login = await _login(client, "ios-cn", other_user.username)
            other_channel_login = await _login(client, "wxapp", user.username)
            owner_headers = {
                "Authorization": f"Bearer {owner_login.json()['data']['accessToken']}"
            }
            other_headers = {
                "Authorization": f"Bearer {other_login.json()['data']['accessToken']}"
            }
            other_channel_headers = {
                "Authorization": (
                    f"Bearer {other_channel_login.json()['data']['accessToken']}"
                )
            }
            requests = (
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/files/{historical.file_id}/download",
                    headers=owner_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/files/999999999/download",
                    headers=owner_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/files/{historical.file_id}/download",
                    headers=other_headers,
                ),
                await client.get(
                    f"/api/v1/wxapp/tasks/{task.pk}/files/{historical.file_id}/download",
                    headers=other_channel_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/outputs/unknown/download",
                    headers=owner_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/outputs/report/download",
                    headers=other_headers,
                ),
                await client.get(
                    f"/api/v1/wxapp/tasks/{task.pk}/outputs/report/download",
                    headers=other_channel_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{failed_task.pk}/outputs/report/download",
                    headers=owner_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{stale_task.pk}/outputs/report/download",
                    headers=owner_headers,
                ),
                await client.get(
                    f"/api/v1/ios-cn/tasks/{task.pk}/files/{historical.file_id}/download"
                ),
            )
            await _close_worker_database_connection()
            return requests

        responses = asyncio.run(exercise_api())

    assert [response.status_code for response in responses] == [
        404,
        404,
        404,
        404,
        404,
        404,
        404,
        404,
        404,
        401,
    ]
    assert all(
        tmp_path.as_posix().encode() not in response.content
        for response in responses
    )
    assert (failed_output / "report.csv").read_bytes() == b"orphan failure output"
    assert (stale_output / "report.csv").read_bytes() == b"orphan stale output"
