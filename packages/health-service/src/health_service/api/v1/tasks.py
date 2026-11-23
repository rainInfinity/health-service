"""提供普通渠道草稿创建、本人分页列表和受限详情查询。"""

from typing import Annotated

from asgiref.sync import sync_to_async
from django.http import FileResponse, HttpRequest
from ninja import Body, File, Form, Router
from ninja.files import UploadedFile

from health_service.api.responses import ApiResponse, success_response
from health_service.application.tasks import (
    create_task_draft,
    list_user_tasks,
    open_user_task_input_file,
    open_user_task_output_file,
    read_user_task,
    submit_and_dispatch_user_task,
    upload_task_input,
)
from health_service.infrastructure.auth.access import ChannelAccessAuth
from health_service.schemas.internal.tasks import TaskMeasurementData
from health_service.schemas.requests.tasks import (
    CreateTaskRequest,
    SubmitTaskRequest,
)
from health_service.schemas.responses.tasks import (
    TaskDetailResponse,
    TaskInputFileResponse,
    TaskPageResponse,
    TaskSummaryResponse,
    to_task_detail_response,
    to_task_input_file_response,
    to_task_page_response,
    to_task_summary_response,
)

from .common import require_authenticated_channel
from .contracts import ERROR_RESPONSES

router = Router(tags=["用户端推理任务与文件"])


@router.post(
    "/{channel}/tasks",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[TaskSummaryResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def create_channel_task(
    request: HttpRequest,
    channel: str,
    body: CreateTaskRequest,
):
    """为当前渠道已配置模型创建固定版本草稿。"""
    require_authenticated_channel(request, channel)
    task = await sync_to_async(create_task_draft, thread_sensitive=True)(
        user_id=request.user.pk,
        channel=channel,
        model_id=body.model_id,
    )
    return success_response(
        request,
        to_task_summary_response(task),
        status=201,
    )


@router.get(
    "/{channel}/tasks",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[TaskPageResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_channel_tasks(
    request: HttpRequest,
    channel: str,
    offset: int = 0,
    limit: int = 20,
):
    """返回当前账号与来源渠道的稳定倒序任务摘要页。"""
    require_authenticated_channel(request, channel)
    total, tasks = await sync_to_async(list_user_tasks, thread_sensitive=True)(
        user_id=request.user.pk,
        channel=channel,
        offset=offset,
        limit=limit,
    )
    return success_response(
        request,
        to_task_page_response(
            tasks,
            total=total,
            offset=offset,
            limit=limit,
        ),
    )


@router.get(
    "/{channel}/tasks/{task_id}",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[TaskDetailResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_channel_task(
    request: HttpRequest,
    channel: str,
    task_id: int,
):
    """只返回本人当前渠道任务的权威结果或安全失败摘要。"""
    require_authenticated_channel(request, channel)
    task = await sync_to_async(read_user_task, thread_sensitive=True)(
        task_id=task_id,
        user_id=request.user.pk,
        channel=channel,
    )
    return success_response(request, to_task_detail_response(task))


@router.post(
    "/{channel}/tasks/{task_id}/files",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[TaskInputFileResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def upload_channel_task_input(
    request: HttpRequest,
    channel: str,
    task_id: int,
    logical_name: Annotated[str, Form(alias="logicalName")],
    file: Annotated[UploadedFile, File()],
):
    """向本人当前渠道的草稿完整上传一个算法声明输入。"""
    require_authenticated_channel(request, channel)
    uploaded = await sync_to_async(upload_task_input, thread_sensitive=True)(
        task_id=task_id,
        user_id=request.user.pk,
        channel=channel,
        logical_name=logical_name,
        source_filename=file.name,
        source=file,
    )
    return success_response(
        request,
        to_task_input_file_response(uploaded),
        status=201,
    )


@router.get(
    "/{channel}/tasks/{task_id}/files/{file_id}/download",
    auth=ChannelAccessAuth(),
    response={200: None, **ERROR_RESPONSES},
    by_alias=True,
)
async def download_channel_task_input(
    request: HttpRequest,
    channel: str,
    task_id: int,
    file_id: int,
):
    """以本人和来源渠道权限下载当前或历史输入文件。"""
    require_authenticated_channel(request, channel)
    file = await sync_to_async(open_user_task_input_file, thread_sensitive=True)(
        task_id=task_id,
        file_id=file_id,
        user_id=request.user.pk,
        channel=channel,
    )
    return FileResponse(
        file,
        as_attachment=True,
        filename=f"task-{task_id}-input-{file_id}",
        content_type="application/octet-stream",
    )


@router.get(
    "/{channel}/tasks/{task_id}/outputs/{logical_name}/download",
    auth=ChannelAccessAuth(),
    response={200: None, **ERROR_RESPONSES},
    by_alias=True,
)
async def download_channel_task_output(
    request: HttpRequest,
    channel: str,
    task_id: int,
    logical_name: str,
):
    """只下载本人成功结果中按逻辑名登记的处理后文件。"""
    require_authenticated_channel(request, channel)
    file = await sync_to_async(open_user_task_output_file, thread_sensitive=True)(
        task_id=task_id,
        logical_name=logical_name,
        user_id=request.user.pk,
        channel=channel,
    )
    return FileResponse(
        file,
        as_attachment=True,
        filename=f"task-{task_id}-output",
        content_type="application/octet-stream",
    )


@router.post(
    "/{channel}/tasks/{task_id}/submit",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[TaskSummaryResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def submit_channel_task(
    request: HttpRequest,
    channel: str,
    task_id: int,
    body: Annotated[SubmitTaskRequest, Body(None)],
):
    """校验输入并原子冻结本人资料、可选采集属性及私有元数据。"""
    require_authenticated_channel(request, channel)
    task = await sync_to_async(
        submit_and_dispatch_user_task,
        thread_sensitive=True,
    )(
        task_id=task_id,
        user_id=request.user.pk,
        channel=channel,
        client_metadata=body.client_metadata if body is not None else None,
        measurement=(
            TaskMeasurementData(**body.measurement.model_dump())
            if body is not None and body.measurement is not None
            else None
        ),
    )
    return success_response(request, to_task_summary_response(task))
