"""验证管理请求逐次检查令牌渠道、账号状态和当前权限。"""

from types import SimpleNamespace

import pytest

from health_service.api.exceptions import ApiError, ApiErrorCode
from health_service.api.v1.common import require_manager_channel


@pytest.mark.parametrize(
    ("request_channel", "token_channel", "authenticated", "active", "is_staff", "allowed"),
    [
        ("manager", "manager", True, True, True, True),
        ("manager", "android-cn", True, True, True, False),
        ("android-cn", "manager", True, True, True, False),
        ("manager", "manager", True, False, True, False),
        ("manager", "manager", True, True, False, False),
        ("manager", "manager", False, True, True, False),
    ],
)
def test_manager_request_requires_matching_active_staff_access(
    request_channel: str,
    token_channel: str,
    authenticated: bool,
    active: bool,
    is_staff: bool,
    allowed: bool,
):
    """只允许当前有效的管理渠道 Access 访问管理业务。"""
    user = SimpleNamespace(
        is_authenticated=authenticated,
        is_active=active,
        is_staff=is_staff,
    )
    request = SimpleNamespace(user=user, authenticated_channel=token_channel)

    if allowed:
        require_manager_channel(request, request_channel)
        assert request.api_channel == "manager"
    else:
        with pytest.raises(ApiError) as error:
            require_manager_channel(request, request_channel)
        assert error.value.code is ApiErrorCode.CHANNEL_FORBIDDEN
