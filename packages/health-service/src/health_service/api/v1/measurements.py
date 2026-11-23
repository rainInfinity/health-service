"""提供当前账号与来源渠道的最新健康结果和日期分页查询。"""

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Query, Router

from health_service.api.responses import ApiResponse, success_response
from health_service.application.measurements import list_measurements, read_latest_measurements
from health_service.infrastructure.auth.access import ChannelAccessAuth
from health_service.schemas.requests.measurements import MeasurementHistoryQuery
from health_service.schemas.responses.measurements import (
    LatestMeasurementsResponse,
    MeasurementPageResponse,
    to_measurement_response,
)

from .common import require_authenticated_channel
from .contracts import ERROR_RESPONSES

router = Router(tags=["用户端健康记录"])


@router.get(
    "/{channel}/measurements/latest",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[LatestMeasurementsResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_channel_latest_measurements(request: HttpRequest, channel: str):
    """按服务端提交时间独立选择各指标最近有效值，失败不覆盖旧值。"""
    require_authenticated_channel(request, channel)
    records = await sync_to_async(read_latest_measurements, thread_sensitive=True)(
        user_id=request.user.pk, channel=channel
    )
    return success_response(
        request,
        LatestMeasurementsResponse(**{
            metric: to_measurement_response(record) if record is not None else None
            for metric, record in records.items()
        }),
    )


@router.get(
    "/{channel}/measurements",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[MeasurementPageResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_channel_measurements(
    request: HttpRequest, channel: str, query: Query[MeasurementHistoryQuery]
):
    """按北京时间包含首尾的日期筛选，普通分页不承诺跨请求快照。"""
    require_authenticated_channel(request, channel)
    total, records = await sync_to_async(list_measurements, thread_sensitive=True)(
        user_id=request.user.pk,
        channel=channel,
        metric=query.metric,
        from_date=query.from_date,
        to_date=query.to_date,
        offset=query.offset,
        limit=query.limit,
    )
    return success_response(
        request,
        MeasurementPageResponse(
            items=tuple(to_measurement_response(record) for record in records),
            total=total,
            offset=query.offset,
            limit=query.limit,
        ),
    )
