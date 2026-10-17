"""实现普通渠道已登录用户的本人账号与资料查询。"""

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router

from health_service.api.responses import ApiResponse, success_response
from health_service.application.profiles import read_profile, update_profile
from health_service.infrastructure.auth.access import ChannelAccessAuth
from health_service.schemas.requests.profiles import ProfilePatchRequest
from health_service.schemas.responses.accounts import UserPublicResponse
from health_service.schemas.responses.profiles import (
    MyProfileResponse,
    to_my_profile_response,
)

from .common import require_authenticated_channel
from .contracts import ERROR_RESPONSES

router = Router(tags=["用户端用户与资料"])




@router.get(
    "/{channel}/users/me",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[UserPublicResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_my_account(request: HttpRequest, channel: str):
    """返回当前渠道已认证用户的公开账号字段。"""
    require_authenticated_channel(request, channel)
    user = request.user
    return success_response(
        request,
        UserPublicResponse(user_id=user.pk, username=user.username),
    )


@router.get(
    "/{channel}/users/me/profile",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[MyProfileResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def read_my_profile(request: HttpRequest, channel: str):
    """返回本人健康资料；不存在时仅投影未填写值。"""
    require_authenticated_channel(request, channel)
    profile = await sync_to_async(read_profile, thread_sensitive=True)(request.user.pk)
    return success_response(request, to_my_profile_response(profile))


@router.patch(
    "/{channel}/users/me/profile",
    auth=ChannelAccessAuth(),
    response={200: ApiResponse[MyProfileResponse], **ERROR_RESPONSES},
    by_alias=True,
)
async def update_my_profile(request: HttpRequest, channel: str, body: ProfilePatchRequest):
    """只应用请求实际提交的本人资料字段。"""
    require_authenticated_channel(request, channel)
    changes = body.model_dump(exclude_unset=True)
    if "history_json" in body.model_fields_set and body.history_json is not None:
        changes["history_json"] = body.history_json.model_dump(
            exclude_unset=True,
            by_alias=False,
        )
    profile = await sync_to_async(update_profile, thread_sensitive=True)(
        request.user.pk,
        changes,
    )
    return success_response(request, to_my_profile_response(profile))
