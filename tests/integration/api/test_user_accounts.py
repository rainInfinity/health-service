"""验证普通渠道账号注册的 PostgreSQL 与 HTTP 行为。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import AsyncClient
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from ninja_jwt.tokens import AccessToken, RefreshToken

from health_service.application.accounts import refresh_user_tokens
from health_service.application.exceptions import InvalidRefreshToken
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Profile


async def _read_my_account(client: AsyncClient, channel: str, token: str | None = None):
    """使用可选 Bearer Token 请求本人账号接口。"""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return await client.get(f"/api/v1/{channel}/users/me", headers=headers)


@pytest.mark.django_db(transaction=True)
def test_my_account_requires_active_access_token_for_the_same_channel():
    """验证本人账号接口只接受有效且渠道匹配的 Access Token。"""
    user = get_user_model().objects.create_user(
        username="my-account-access-user",
        password="Amber-valley-472!strong",
    )
    expired_token = AccessToken.for_user(user)
    expired_token["channel"] = "android-cn"
    expired_token.set_exp(lifetime=timedelta(seconds=-1))

    async def send_requests():
        client = AsyncClient()
        android_login = await _login(
            client,
            "android-cn",
            {
                "username": "my-account-access-user",
                "password": "Amber-valley-472!strong",
            },
        )
        wxapp_login = await _login(
            client,
            "wxapp",
            {
                "username": "my-account-access-user",
                "password": "Amber-valley-472!strong",
            },
        )
        android_pair = android_login.json()["data"]
        responses = (
            await _read_my_account(client, "android-cn"),
            await _read_my_account(client, "android-cn", str(expired_token)),
            await _read_my_account(client, "android-cn", android_pair["refreshToken"]),
            await _read_my_account(client, "wxapp", android_pair["accessToken"]),
            await _read_my_account(client, "android-cn", android_pair["accessToken"]),
        )
        await _close_worker_database_connection()
        return android_login, wxapp_login, responses, android_pair["accessToken"]

    android_login, wxapp_login, responses, android_access = asyncio.run(send_requests())
    missing, expired, refresh, wrong_channel, valid = responses

    assert android_login.status_code == wxapp_login.status_code == 200
    assert [response.status_code for response in (missing, expired, refresh)] == [
        401,
        401,
        401,
    ]
    assert [response.json()["code"] for response in (missing, expired, refresh)] == [
        "INVALID_TOKEN",
        "INVALID_TOKEN",
        "INVALID_TOKEN",
    ]
    assert wrong_channel.status_code == 403
    assert wrong_channel.json()["code"] == "CHANNEL_FORBIDDEN"
    assert valid.status_code == 200
    assert valid.json()["code"] == "OK"
    assert valid.json()["data"] == {
        "userId": user.pk,
        "username": "my-account-access-user",
    }
    assert valid.json()["requestId"] == valid["X-Request-ID"]

    user.is_active = False
    user.save(update_fields={"is_active"})

    async def read_inactive_account():
        client = AsyncClient()
        response = await _read_my_account(client, "android-cn", android_access)
        await _close_worker_database_connection()
        return response

    inactive = asyncio.run(read_inactive_account())

    assert inactive.status_code == 401
    assert inactive.json()["code"] == "INVALID_TOKEN"
    assert inactive.json()["data"] is None


async def _register(client: AsyncClient, channel: str, payload: dict):
    """向指定渠道发送 JSON 注册请求。"""
    return await client.post(
        f"/api/v1/{channel}/auth/register",
        data=json.dumps(payload),
        content_type="application/json",
    )


async def _login(client: AsyncClient, channel: str, payload: dict):
    """向指定渠道发送 JSON 密码登录请求。"""
    return await client.post(
        f"/api/v1/{channel}/auth/login",
        data=json.dumps(payload),
        content_type="application/json",
    )


async def _refresh(client: AsyncClient, channel: str, token: str):
    """向指定渠道提交 Refresh 轮换请求。"""
    return await client.post(
        f"/api/v1/{channel}/auth/refresh",
        data=json.dumps({"refreshToken": token}),
        content_type="application/json",
    )


async def _logout(
    client: AsyncClient,
    channel: str,
    token: str,
    access_token: str | None = None,
):
    """提交当前渠道 Refresh 登出请求，可附带不应参与鉴权的 Access。"""
    headers = {} if access_token is None else {"Authorization": f"Bearer {access_token}"}
    return await client.post(
        f"/api/v1/{channel}/auth/logout",
        data=json.dumps({"refreshToken": token}),
        content_type="application/json",
        headers=headers,
    )


async def _close_worker_database_connection() -> None:
    """关闭异步视图同步用例线程持有的 PostgreSQL 连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()


@pytest.mark.django_db(transaction=True)
def test_registration_creates_hashed_user_and_empty_profile_atomically():
    """验证注册公开字段、密码哈希及账号资料的一致创建。"""
    username = "register-flow-user"
    password = "Violet-plateau-541!strong"

    async def send_request():
        client = AsyncClient()
        response = await _register(
            client,
            "android-cn",
            {"username": username, "password": password},
        )
        await _close_worker_database_connection()
        return response

    response = asyncio.run(send_request())
    user_model = get_user_model()
    user = user_model.objects.get(username=username)
    profile = Profile.objects.get(user=user)

    assert response.status_code == 201
    assert response.json()["code"] == "OK"
    assert response.json()["data"] == {"userId": user.pk, "username": username}
    assert response.json()["requestId"] == response["X-Request-ID"]
    assert user.check_password(password)
    assert user.password != password
    assert user.phone is None
    assert not user.is_staff
    assert not user.is_superuser
    assert profile.user_id == user.pk
    assert profile.gender == Profile.GenderChoices.UNKNOWN


@pytest.mark.django_db(transaction=True)
def test_registration_rejects_duplicate_invalid_and_privileged_inputs():
    """验证全局用户名冲突、密码规则、权限字段及 manager 边界。"""
    username = "register-validation-user"
    valid = {"username": username, "password": "Copper-orchid-738!strong"}

    async def send_requests():
        client = AsyncClient()
        responses = (
            await _register(client, "wxapp", valid),
            await _register(client, "ios-cn", valid),
            await _register(
                client,
                "android-intl",
                {"username": "invalid-password-user", "password": "x"},
            ),
            await _register(
                client,
                "ios-intl",
                {
                    "username": "privileged-user",
                    "password": "Copper-orchid-738!strong",
                    "is_staff": True,
                },
            ),
            await _register(
                client,
                "manager",
                {"username": "manager-user", "password": "Copper-orchid-738!strong"},
            ),
        )
        await _close_worker_database_connection()
        return responses

    first, duplicate, invalid_password, privileged, manager = asyncio.run(send_requests())

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "USERNAME_EXISTS"
    assert duplicate.json()["data"] is None
    assert invalid_password.status_code == 422
    assert invalid_password.json()["code"] == "INVALID_INPUT"
    assert privileged.status_code == 422
    assert privileged.json()["code"] == "INVALID_INPUT"
    assert manager.status_code == 404
    assert manager.json()["code"] == "NOT_FOUND"
    assert (
        not get_user_model()
        .objects.filter(username__in={"invalid-password-user", "privileged-user", "manager-user"})
        .exists()
    )
    assert Profile.objects.filter(user__username=username).count() == 1


@pytest.mark.django_db(transaction=True)
def test_password_login_issues_channel_bound_tokens_for_one_account():
    """验证同一账号跨渠道登录保留相同 ID 并签发渠道独立令牌。"""
    username = "login-cross-channel-user"
    password = "Cobalt-forest-824!strong"

    async def send_requests():
        client = AsyncClient()
        registration = await _register(
            client,
            "android-cn",
            {"username": username, "password": password},
        )
        android_login = await _login(
            client,
            "android-cn",
            {"username": username, "password": password},
        )
        wxapp_login = await _login(
            client,
            "wxapp",
            {"username": username, "password": password},
        )
        await _close_worker_database_connection()
        return registration, android_login, wxapp_login

    registration, android_login, wxapp_login = asyncio.run(send_requests())
    android_pair = android_login.json()["data"]
    wxapp_pair = wxapp_login.json()["data"]
    android_access = AccessToken(android_pair["accessToken"])
    wxapp_access = AccessToken(wxapp_pair["accessToken"])
    android_refresh = RefreshToken(android_pair["refreshToken"])
    wxapp_refresh = RefreshToken(wxapp_pair["refreshToken"])

    assert registration.status_code == 201
    assert android_login.status_code == wxapp_login.status_code == 200
    assert android_login.json()["code"] == wxapp_login.json()["code"] == "OK"
    assert android_pair.keys() == {"accessToken", "refreshToken"}
    assert android_access["user_id"] == wxapp_access["user_id"]
    assert android_access["channel"] == android_refresh["channel"] == "android-cn"
    assert wxapp_access["channel"] == wxapp_refresh["channel"] == "wxapp"
    assert android_pair["accessToken"] != wxapp_pair["accessToken"]
    assert android_pair["refreshToken"] != wxapp_pair["refreshToken"]
    assert get_user_model().objects.filter(username=username).count() == 1
    assert Profile.objects.filter(user__username=username).count() == 1


@pytest.mark.django_db(transaction=True)
def test_login_failures_have_the_same_public_credentials_error():
    """验证不存在、密码错误和停用账号使用相同的安全错误文案。"""
    user_model = get_user_model()
    user_model.objects.create_user(
        username="login-wrong-password-user",
        password="Cobalt-forest-824!strong",
    )
    inactive_user = user_model.objects.create_user(
        username="login-inactive-user",
        password="Cobalt-forest-824!strong",
    )
    inactive_user.is_active = False
    inactive_user.save(update_fields={"is_active"})

    async def send_requests():
        client = AsyncClient()
        responses = (
            await _login(
                client,
                "android-cn",
                {"username": "login-missing-user", "password": "Cobalt-forest-824!strong"},
            ),
            await _login(
                client,
                "android-cn",
                {"username": "login-wrong-password-user", "password": "incorrect-password"},
            ),
            await _login(
                client,
                "android-cn",
                {"username": "login-inactive-user", "password": "Cobalt-forest-824!strong"},
            ),
        )
        await _close_worker_database_connection()
        return responses

    missing, incorrect, inactive = asyncio.run(send_requests())

    assert [response.status_code for response in (missing, incorrect, inactive)] == [401] * 3
    assert [response.json()["code"] for response in (missing, incorrect, inactive)] == [
        "INVALID_CREDENTIALS"
    ] * 3
    assert [response.json()["message"] for response in (missing, incorrect, inactive)] == [
        "用户名或密码错误"
    ] * 3
    assert all(response.json()["data"] is None for response in (missing, incorrect, inactive))

@pytest.mark.django_db(transaction=True)
def test_manager_login_requires_staff_and_uses_manager_channel_tokens():
    """验证只有可用管理账号能登录管理渠道且失败信息一致。"""
    user_model = get_user_model()
    manager = user_model.objects.create_user(
        username="manager-login-user",
        password="Cobalt-forest-824!strong",
        is_staff=True,
        is_superuser=False,
    )
    ordinary = user_model.objects.create_user(
        username="ordinary-manager-login-user",
        password="Cobalt-forest-824!strong",
    )
    inactive_manager = user_model.objects.create_user(
        username="inactive-manager-login-user",
        password="Cobalt-forest-824!strong",
        is_staff=True,
        is_superuser=False,
        is_active=False,
    )

    async def send_requests():
        client = AsyncClient()
        responses = (
            await _login(
                client,
                "manager",
                {"username": manager.username, "password": "Cobalt-forest-824!strong"},
            ),
            await _login(
                client,
                "manager",
                {"username": ordinary.username, "password": "Cobalt-forest-824!strong"},
            ),
            await _login(
                client,
                "manager",
                {"username": ordinary.username, "password": "incorrect-password"},
            ),
            await _login(
                client,
                "manager",
                {"username": "missing-manager-login-user", "password": "Cobalt-forest-824!strong"},
            ),
            await _login(
                client,
                "manager",
                {"username": inactive_manager.username, "password": "Cobalt-forest-824!strong"},
            ),
            await _login(
                client,
                "android-cn",
                {"username": manager.username, "password": "Cobalt-forest-824!strong"},
            ),
        )
        await _close_worker_database_connection()
        return responses

    (
        manager_login,
        ordinary_denied,
        bad_password,
        missing_user,
        inactive_denied,
        manager_ordinary_login,
    ) = asyncio.run(send_requests())

    assert manager_login.status_code == 200
    assert manager_login.json()["code"] == "OK"
    manager_pair = manager_login.json()["data"]
    assert AccessToken(manager_pair["accessToken"])["channel"] == "manager"
    assert RefreshToken(manager_pair["refreshToken"])["channel"] == "manager"
    assert manager_ordinary_login.status_code == 200
    assert AccessToken(manager_ordinary_login.json()["data"]["accessToken"])["channel"] == "android-cn"

    failures = (ordinary_denied, bad_password, missing_user, inactive_denied)
    assert [response.status_code for response in failures] == [401] * 4
    assert [response.json()["code"] for response in failures] == ["INVALID_CREDENTIALS"] * 4
    assert [response.json()["message"] for response in failures] == ["用户名或密码错误"] * 4
    assert all(response.json()["data"] is None for response in failures)
    assert get_user_model().objects.get(pk=ordinary.pk).is_staff is False
    assert OutstandingToken.objects.filter(user_id=manager.pk).count() == 2
    assert not OutstandingToken.objects.filter(user_id=ordinary.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_refresh_rotates_once_and_rejects_replay_wrong_channel_and_inactive_user():
    """验证轮换成功后旧令牌、错渠道及停用账号均不能再签发令牌。"""
    user = get_user_model().objects.create_user(
        username="refresh-contract-user",
        password="Cedar-orbit-349!strong",
    )
    android_pair = issue_token_pair(user, "android-cn")
    wxapp_pair = issue_token_pair(user, "wxapp")
    old_android_refresh = RefreshToken(android_pair.refresh_token)
    wxapp_jti = RefreshToken(wxapp_pair.refresh_token)["jti"]

    async def rotate_and_reject_failures():
        client = AsyncClient()
        rotated = await _refresh(client, "android-cn", android_pair.refresh_token)
        rotated_refresh = rotated.json()["data"]["refreshToken"]
        count_before_rejections = await sync_to_async(
            OutstandingToken.objects.count, thread_sensitive=True
        )()
        wrong_channel = await _refresh(client, "android-cn", wxapp_pair.refresh_token)
        replay = await _refresh(client, "android-cn", android_pair.refresh_token)
        count_after_rejections = await sync_to_async(
            OutstandingToken.objects.count, thread_sensitive=True
        )()
        await _close_worker_database_connection()
        return (
            rotated,
            rotated_refresh,
            wrong_channel,
            replay,
            count_before_rejections,
            count_after_rejections,
        )

    (
        rotated,
        rotated_refresh,
        wrong_channel,
        replay,
        count_before_rejections,
        count_after_rejections,
    ) = asyncio.run(rotate_and_reject_failures())
    rotated_pair = rotated.json()["data"]
    assert rotated.status_code == 200
    assert rotated.json()["code"] == "OK"
    assert set(rotated_pair) == {"accessToken", "refreshToken"}
    assert AccessToken(rotated_pair["accessToken"])["channel"] == "android-cn"
    assert RefreshToken(rotated_refresh)["channel"] == "android-cn"
    assert RefreshToken(rotated_refresh)["jti"] != old_android_refresh["jti"]
    assert BlacklistedToken.objects.filter(token__jti=old_android_refresh["jti"]).exists()
    assert not BlacklistedToken.objects.filter(token__jti=wxapp_jti).exists()
    assert wrong_channel.status_code == 403
    assert wrong_channel.json()["code"] == "CHANNEL_FORBIDDEN"
    assert replay.status_code == 401
    assert replay.json()["code"] == "INVALID_TOKEN"
    assert count_after_rejections == count_before_rejections

    token_count_before_inactive_attempt = OutstandingToken.objects.count()
    user.is_active = False
    user.save(update_fields={"is_active"})

    async def refresh_inactive_account():
        client = AsyncClient()
        response = await _refresh(client, "android-cn", rotated_refresh)
        await _close_worker_database_connection()
        return response

    inactive = asyncio.run(refresh_inactive_account())

    assert inactive.status_code == 401
    assert inactive.json()["code"] == "INVALID_TOKEN"
    assert OutstandingToken.objects.count() == token_count_before_inactive_attempt

@pytest.mark.django_db(transaction=True)
def test_manager_refresh_rechecks_staff_but_logout_remains_available():
    """验证撤销管理权限阻止刷新但仍允许本人撤销管理 Refresh。"""
    user = get_user_model().objects.create_user(
        username="manager-refresh-user",
        password="Cedar-orbit-349!strong",
        is_staff=True,
        is_superuser=False,
    )
    manager_pair = issue_token_pair(user, "manager")
    other_manager_pair = issue_token_pair(user, "manager")
    ordinary_pair = issue_token_pair(user, "android-cn")
    manager_jti = RefreshToken(manager_pair.refresh_token)["jti"]
    other_manager_jti = RefreshToken(other_manager_pair.refresh_token)["jti"]

    async def check_channel_isolation():
        client = AsyncClient()
        responses = (
            await _refresh(client, "android-cn", manager_pair.refresh_token),
            await _refresh(client, "manager", ordinary_pair.refresh_token),
            await _logout(client, "android-cn", manager_pair.refresh_token),
            await _logout(client, "manager", ordinary_pair.refresh_token),
        )
        await _close_worker_database_connection()
        return responses

    wrong_channel_responses = asyncio.run(check_channel_isolation())
    assert [response.status_code for response in wrong_channel_responses] == [403] * 4
    assert [response.json()["code"] for response in wrong_channel_responses] == [
        "CHANNEL_FORBIDDEN"
    ] * 4

    user.is_staff = False
    user.save(update_fields={"is_staff"})

    async def revoke_and_refresh():
        client = AsyncClient()
        denied_refresh = await _refresh(client, "manager", manager_pair.refresh_token)
        logout = await _logout(client, "manager", manager_pair.refresh_token)
        ordinary_refresh = await _refresh(client, "android-cn", ordinary_pair.refresh_token)
        await _close_worker_database_connection()
        return denied_refresh, logout, ordinary_refresh

    denied_refresh, logout, ordinary_refresh = asyncio.run(revoke_and_refresh())

    assert denied_refresh.status_code == 401
    assert denied_refresh.json()["code"] == "INVALID_TOKEN"
    assert logout.status_code == 200
    assert BlacklistedToken.objects.filter(token__jti=manager_jti).exists()
    assert not BlacklistedToken.objects.filter(token__jti=other_manager_jti).exists()
    assert ordinary_refresh.status_code == 200
    assert AccessToken(ordinary_refresh.json()["data"]["accessToken"])["channel"] == "android-cn"



@pytest.mark.parametrize(
    ("channel", "is_staff"),
    [("ios-cn", False), ("manager", True)],
)

@pytest.mark.django_db(transaction=True)
def test_concurrent_refresh_requests_rotate_one_old_jti_once(channel: str, is_staff: bool):
    """用两个 PostgreSQL 连接并发刷新，确认普通与管理令牌均只轮换一次。"""
    user = get_user_model().objects.create_user(
        username="refresh-concurrency-user",
        password="Indigo-river-951!strong",
        is_staff=is_staff,
        is_superuser=False,
    )
    issued = issue_token_pair(user, channel)
    old_jti = RefreshToken(issued.refresh_token)["jti"]
    barrier = Barrier(2)

    def rotate_concurrently():
        """在独立数据库连接中尝试同一枚旧 Refresh。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                refresh_user_tokens(
                    raw_token=issued.refresh_token,
                    channel=channel,
                )
            except InvalidRefreshToken:
                return "rejected"
            return "rotated"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: rotate_concurrently(), range(2)))

    assert sorted(outcomes) == ["rejected", "rotated"]
    assert BlacklistedToken.objects.filter(token__jti=old_jti).exists()
    assert OutstandingToken.objects.filter(user=user).count() == 2


@pytest.mark.django_db(transaction=True)
def test_logout_uses_refresh_without_access_and_preserves_other_channel_tokens():
    """验证过期 Access 不阻止登出，且只撤销当前渠道 Refresh。"""
    user = get_user_model().objects.create_user(
        username="logout-refresh-user",
        password="Silver-maple-286!strong",
    )
    android_pair = issue_token_pair(user, "android-cn")
    ios_pair = issue_token_pair(user, "ios-cn")
    android_jti = RefreshToken(android_pair.refresh_token)["jti"]
    ios_jti = RefreshToken(ios_pair.refresh_token)["jti"]
    expired_access = AccessToken.for_user(user)
    expired_access["channel"] = "android-cn"
    expired_access.set_exp(lifetime=timedelta(seconds=-1))

    async def send_logout_and_follow_up_requests():
        client = AsyncClient()
        logout_response = await _logout(
            client,
            "android-cn",
            android_pair.refresh_token,
            str(expired_access),
        )
        revoked_refresh = await _refresh(client, "android-cn", android_pair.refresh_token)
        ios_refresh = await _refresh(client, "ios-cn", ios_pair.refresh_token)
        existing_access = await _read_my_account(client, "android-cn", android_pair.access_token)
        await _close_worker_database_connection()
        return logout_response, revoked_refresh, ios_refresh, existing_access

    logout_response, revoked_refresh, ios_refresh, existing_access = asyncio.run(
        send_logout_and_follow_up_requests()
    )
    ios_rotated_pair = ios_refresh.json()["data"]

    assert logout_response.status_code == 200
    assert logout_response.json()["code"] == "OK"
    assert logout_response.json()["data"] is None
    assert logout_response.json()["requestId"] == logout_response["X-Request-ID"]
    assert BlacklistedToken.objects.filter(token__jti=android_jti).exists()
    assert revoked_refresh.status_code == 401
    assert revoked_refresh.json()["code"] == "INVALID_TOKEN"
    assert ios_refresh.status_code == 200
    assert ios_refresh.json()["code"] == "OK"
    assert AccessToken(ios_rotated_pair["accessToken"])["channel"] == "ios-cn"
    assert BlacklistedToken.objects.filter(token__jti=ios_jti).exists()
    assert existing_access.status_code == 200
    assert existing_access.json()["data"] == {
        "userId": user.pk,
        "username": "logout-refresh-user",
    }


