"""提供受管理渠道保护的模型目录写入及普通渠道模型查询。"""

from typing import Annotated

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import File, Form, Router
from ninja.files import UploadedFile

from health_service.api.responses import ApiResponse, success_response
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    list_available_models,
    list_model_versions,
    publish_model_version,
    select_channel_model_version,
    set_model_version_metrics,
    update_model_version,
    upload_model_artifact,
)
from health_service.infrastructure.auth.access import ChannelAccessAuth
from health_service.schemas.requests.catalog import (
    CreateMetricDefinitionRequest,
    CreateModelRequest,
    CreateModelVersionRequest,
    PatchModelVersionRequest,
    SelectModelVersionRequest,
    SetModelVersionMetricsRequest,
)
from health_service.schemas.responses.catalog import (
    AvailableModelPageResponse,
    CatalogModelResponse,
    MetricDefinitionResponse,
    ModelArtifactResponse,
    ModelVersionPageResponse,
    ModelVersionResponse,
    to_available_model_page_response,
    to_catalog_model_response,
    to_metric_definition_response,
    to_model_artifact_response,
    to_model_version_page_response,
    to_model_version_response,
)

from .common import require_authenticated_channel, require_manager_channel
from .contracts import ERROR_RESPONSES

router = Router(tags=["管理端模型管理"])


@router.post(
    "/manager/models",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[CatalogModelResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def create_catalog_model(request: HttpRequest, body: CreateModelRequest):
    """创建管理目录中的模型种类。"""
    require_manager_channel(request, "manager")
    model = await sync_to_async(create_model, thread_sensitive=True)(
        code=body.code,
        name=body.name,
        description=body.description,
    )
    return success_response(
        request,
        to_catalog_model_response(model),
        status=201,
    )


@router.get(
    "/manager/models/{model_id}/versions",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[ModelVersionPageResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_catalog_model_versions(
    request: HttpRequest,
    model_id: int,
    offset: int = 0,
    limit: int = 20,
):
    """按稳定顺序分页读取模型版本。"""
    require_manager_channel(request, "manager")
    total, versions = await sync_to_async(list_model_versions, thread_sensitive=True)(
        model_id=model_id,
        offset=offset,
        limit=limit,
    )
    return success_response(
        request,
        to_model_version_page_response(
            total,
            versions,
            offset=offset,
            limit=limit,
        ),
    )


@router.post(
    "/manager/models/{model_id}/versions",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[ModelVersionResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def create_catalog_model_version(
    request: HttpRequest,
    model_id: int,
    body: CreateModelVersionRequest,
):
    """为指定模型创建未发布版本。"""
    require_manager_channel(request, "manager")
    version = await sync_to_async(create_model_version, thread_sensitive=True)(
        model_id=model_id,
        version=body.version,
        algorithm_key=body.algorithm_key,
        params=body.params,
    )
    return success_response(
        request,
        to_model_version_response(version),
        status=201,
    )


@router.patch(
    "/manager/model-versions/{version_id}",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[ModelVersionResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def patch_catalog_model_version(
    request: HttpRequest,
    version_id: int,
    body: PatchModelVersionRequest,
):
    """更新未发布版本的算法标识或固定参数。"""
    require_manager_channel(request, "manager")
    changes = body.model_dump(exclude_unset=True)
    version = await sync_to_async(update_model_version, thread_sensitive=True)(
        version_id=version_id,
        changes=changes,
    )
    return success_response(request, to_model_version_response(version))


@router.post(
    "/manager/model-versions/{version_id}/artifacts",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[ModelArtifactResponse], **ERROR_RESPONSES},
    by_alias=True,
)
def upload_catalog_model_artifact(
    request: HttpRequest,
    version_id: int,
    logical_name: Annotated[str, Form(alias="logicalName")],
    file: Annotated[UploadedFile, File()],
):
    """上传制品文件并登记其算法逻辑名。"""
    require_manager_channel(request, "manager")
    artifact = upload_model_artifact(
        version_id=version_id,
        logical_name=logical_name,
        source=file,
    )
    return success_response(
        request,
        to_model_artifact_response(artifact),
        status=201,
    )


@router.post(
    "/manager/metrics",
    auth=ChannelAccessAuth(),
    response={201: ApiResponse[MetricDefinitionResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def create_catalog_metric(
    request: HttpRequest,
    body: CreateMetricDefinitionRequest,
):
    """创建不可复用稳定语义的全局指标定义。"""
    require_manager_channel(request, "manager")
    metric = await sync_to_async(create_metric_definition, thread_sensitive=True)(
        code=body.code,
        description=body.description,
        value_type=body.value_type,
        unit=body.unit,
    )
    return success_response(
        request,
        to_metric_definition_response(metric),
        status=201,
    )


@router.put(
    "/manager/model-versions/{version_id}/metrics",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[list[MetricDefinitionResponse]], **ERROR_RESPONSES},
    by_alias=True,
)
async def replace_catalog_model_metrics(
    request: HttpRequest,
    version_id: int,
    body: SetModelVersionMetricsRequest,
):
    """替换未发布版本的完整投影指标集合。"""
    require_manager_channel(request, "manager")
    metrics = await sync_to_async(set_model_version_metrics, thread_sensitive=True)(
        version_id=version_id,
        metric_codes=tuple(body.metric_codes),
    )
    return success_response(
        request,
        [to_metric_definition_response(metric) for metric in metrics],
    )


@router.post(
    "/manager/model-versions/{version_id}/publish",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[ModelVersionResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def publish_catalog_model_version(request: HttpRequest, version_id: int):
    """按算法契约校验并首次发布模型版本。"""
    require_manager_channel(request, "manager")
    version = await sync_to_async(publish_model_version, thread_sensitive=True)(
        version_id=version_id,
    )
    return success_response(request, to_model_version_response(version))


@router.put(
    "/manager/channels/{channel}/models/{model_id}/selection",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[ModelVersionResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def select_catalog_model_version(
    request: HttpRequest,
    channel: str,
    model_id: int,
    body: SelectModelVersionRequest,
):
    """切换渠道当前模型版本并终止原版本草稿。"""
    require_manager_channel(request, "manager")
    version = await sync_to_async(
        select_channel_model_version,
        thread_sensitive=True,
    )(
        channel=channel,
        model_id=model_id,
        version_id=body.version_id,
    )
    return success_response(request, to_model_version_response(version))


@router.get(
    "/{channel}/models",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[AvailableModelPageResponse], **ERROR_RESPONSES},
    by_alias=True,
    tags=["用户端模型目录"],
)
async def read_available_models(
    request: HttpRequest,
    channel: str,
    offset: int = 0,
    limit: int = 20,
):
    """返回当前普通渠道可用模型及算法声明的输入名。"""
    require_authenticated_channel(request, channel)
    page = await sync_to_async(list_available_models, thread_sensitive=True)(
        channel=channel,
        offset=offset,
        limit=limit,
    )
    return success_response(request, to_available_model_page_response(page))
