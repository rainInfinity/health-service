"""验证普通渠道账号注册的 PostgreSQL 与 HTTP 行为。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
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
from health_service.application.profiles import update_profile
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Profile


async def _read_my_account(client: AsyncClient, channel: str, token: str | None = None):
    """使用可选 Bearer Token 请求本人账号接口。"""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return await client.get(f"/api/v1/{channel}/users/me", headers=headers)


async def _read_my_profile(client: AsyncClient, channel: str, token: str):
    """使用当前渠道 Access 请求本人资料。"""
    return await client.get(
        f"/api/v1/{channel}/users/me/profile",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _patch_my_profile(client: AsyncClient, channel: str, token: str, payload: dict):
    """向本人资料接口提交 PATCH JSON。"""
    return await client.patch(
        f"/api/v1/{channel}/users/me/profile",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


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
def test_concurrent_refresh_requests_rotate_one_old_jti_once():
    """用两个 PostgreSQL 连接并发刷新同一 jti，确认只有一个事务成功。"""
    user = get_user_model().objects.create_user(
        username="refresh-concurrency-user",
        password="Indigo-river-951!strong",
    )
    issued = issue_token_pair(user, "ios-cn")
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
                    channel="ios-cn",
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


@pytest.mark.django_db(transaction=True)
def test_profile_read_shares_one_record_and_projects_missing_profile():
    """验证跨渠道共享 Profile，缺失资料只返回空投影且不会创建。"""
    user = get_user_model().objects.create_user(
        username="profile-read-user",
        password="Jade-forest-462!strong",
    )
    profile = Profile.objects.create(
        user=user,
        name="健康资料本人",
        gender=2,
        birth_date=date(1991, 2, 3),
        height_cm=Decimal("166.25"),
        weight_kg=Decimal("61.50"),
        medical_history="季节性过敏",
        history_json={"isDiabetic": False},
    )
    assert profile.user_id == user.pk
    empty_user = get_user_model().objects.create_user(
        username="profile-read-empty-user",
        password="Amber-field-852!strong",
    )
    android_pair = issue_token_pair(user, "android-cn")
    wxapp_pair = issue_token_pair(user, "wxapp")
    empty_pair = issue_token_pair(empty_user, "ios-cn")
    profile_count_before = Profile.objects.count()

    async def read_profiles():
        client = AsyncClient()
        android = await _read_my_profile(client, "android-cn", android_pair.access_token)
        wxapp = await _read_my_profile(client, "wxapp", wxapp_pair.access_token)
        empty = await _read_my_profile(client, "ios-cn", empty_pair.access_token)
        await _close_worker_database_connection()
        return android, wxapp, empty

    android, wxapp, empty = asyncio.run(read_profiles())

    assert android.status_code == wxapp.status_code == empty.status_code == 200
    assert android.json()["data"] == wxapp.json()["data"]
    assert android.json()["data"]["name"] == "健康资料本人"
    assert android.json()["data"]["gender"] == 2
    assert android.json()["data"]["birthDate"] == "1991-02-03"
    assert android.json()["data"]["medicalHistory"] == "季节性过敏"
    assert android.json()["data"]["historyJson"] == {"isDiabetic": False}
    assert empty.json()["data"] == {
        "name": None,
        "gender": 0,
        "birthDate": None,
        "heightCm": None,
        "weightKg": None,
        "medicalHistory": None,
        "historyJson": None,
    }
    assert Profile.objects.filter(user=user).count() == 1
    assert not Profile.objects.filter(user=empty_user).exists()
    assert Profile.objects.count() == profile_count_before == 1


@pytest.mark.django_db(transaction=True)
def test_profile_patch_preserves_absent_fields_and_merges_history_keys():
    """验证 PATCH 的缺席、null、false、键删除和无效值边界。"""
    user = get_user_model().objects.create_user(
        username="profile-patch-merge-user",
        password="Mossy-river-648!strong",
    )
    Profile.objects.create(
        user=user,
        name="原姓名",
        gender=1,
        birth_date=date(1990, 4, 7),
        height_cm=Decimal("172.00"),
        weight_kg=Decimal("68.25"),
        medical_history="既有病史",
        history_json={
            "isDiabetic": True,
            "isHypertensive": True,
            "legacyFlag": {"source": "persisted"},
        },
    )
    pair = issue_token_pair(user, "android-cn")

    async def send_patches():
        client = AsyncClient()
        merged = await _patch_my_profile(
            client,
            "android-cn",
            pair.access_token,
            {
                "name": None,
                "gender": 0,
                "heightCm": None,
                "historyJson": {
                    "isDiabetic": None,
                    "isHypertensive": False,
                },
            },
        )
        invalid = (
            await _patch_my_profile(client, "android-cn", pair.access_token, {"heightCm": 0}),
            await _patch_my_profile(client, "android-cn", pair.access_token, {"weightKg": -1}),
            await _patch_my_profile(client, "android-cn", pair.access_token, {"gender": 3}),
            await _patch_my_profile(client, "android-cn", pair.access_token, {"gender": None}),
        )
        await _close_worker_database_connection()
        return merged, invalid

    merged, invalid = asyncio.run(send_patches())
    data = merged.json()["data"]
    assert merged.status_code == 200
    assert data["name"] is None
    assert data["gender"] == 0
    assert data["birthDate"] == "1990-04-07"
    assert data["heightCm"] is None
    assert data["weightKg"] == "68.25"
    assert data["medicalHistory"] == "既有病史"
    assert data["historyJson"] == {
        "isHypertensive": False,
        "legacyFlag": {"source": "persisted"},
    }
    assert [response.status_code for response in invalid] == [422] * 4
    persisted = Profile.objects.get(user=user)
    assert persisted.name is None
    assert persisted.gender == 0
    assert persisted.height_cm is None
    assert persisted.weight_kg == Decimal("68.25")
    assert persisted.history_json == {
        "isHypertensive": False,
        "legacyFlag": {"source": "persisted"},
    }

    async def clear_history():
        client = AsyncClient()
        response = await _patch_my_profile(
            client,
            "android-cn",
            pair.access_token,
            {"historyJson": None},
        )
        await _close_worker_database_connection()
        return response

    cleared = asyncio.run(clear_history())
    assert cleared.status_code == 200
    assert cleared.json()["data"]["historyJson"] is None
    assert Profile.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_profile_patch_creates_missing_profile_for_first_update():
    """验证首个有效 PATCH 在用户行锁内创建唯一资料。"""
    user = get_user_model().objects.create_user(
        username="profile-first-patch-user",
        password="Dawn-valley-371!strong",
    )
    pair = issue_token_pair(user, "wxapp")

    async def send_patch():
        client = AsyncClient()
        response = await _patch_my_profile(
            client,
            "wxapp",
            pair.access_token,
            {"name": "首次填写"},
        )
        await _close_worker_database_connection()
        return response

    response = asyncio.run(send_patch())

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "首次填写"
    assert response.json()["data"]["gender"] == 0
    assert Profile.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_profile_patches_preserve_distinct_fields_and_history_keys():
    """用并发 PostgreSQL 事务验证字段更新及 JSON 合并不互相覆盖。"""
    user = get_user_model().objects.create_user(
        username="profile-concurrent-patch-user",
        password="Silver-forest-574!strong",
    )
    Profile.objects.create(
        user=user,
        name="并发前",
        medical_history="并发前病史",
        history_json={"legacyFlag": "preserved"},
    )
    barrier = Barrier(2)

    def patch_concurrently(changes: dict[str, object]) -> None:
        """在独立数据库连接中等待后提交一个资料更改集。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            update_profile(user.pk, changes)
        finally:
            connections.close_all()

    changes = (
        {
            "name": "并发姓名",
            "history_json": {"isDiabetic": True},
        },
        {
            "medical_history": "并发病史",
            "history_json": {"isHypertensive": False},
        },
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(patch_concurrently, item) for item in changes]
        for future in futures:
            future.result(timeout=30)

    profile = Profile.objects.get(user=user)
    assert profile.name == "并发姓名"
    assert profile.medical_history == "并发病史"
    assert profile.history_json == {
        "isDiabetic": True,
        "isHypertensive": False,
        "legacyFlag": "preserved",
    }


@pytest.mark.django_db(transaction=True)
def test_profile_routes_confine_reads_and_patches_to_authenticated_user():
    """验证资料响应白名单、账号隔离、userId 拒绝和凭证边界。"""
    user_model = get_user_model()
    first_user = user_model.objects.create_user(
        username="profile-isolation-first",
        password="Amber-sea-732!strong",
    )
    second_user = user_model.objects.create_user(
        username="profile-isolation-second",
        password="Coral-hill-416!strong",
    )
    Profile.objects.create(user=first_user, name="账号甲", gender=1)
    Profile.objects.create(user=second_user, name="账号乙", gender=2)
    first_pair = issue_token_pair(first_user, "android-cn")
    second_pair = issue_token_pair(second_user, "android-cn")

    async def send_isolation_requests():
        client = AsyncClient()
        missing = await client.get("/api/v1/android-cn/users/me/profile")
        wrong_channel = await _read_my_profile(client, "wxapp", first_pair.access_token)
        first_read = await _read_my_profile(client, "android-cn", first_pair.access_token)
        first_patch = await _patch_my_profile(
            client,
            "android-cn",
            first_pair.access_token,
            {"name": "账号甲更新"},
        )
        forged_owner = await _patch_my_profile(
            client,
            "android-cn",
            first_pair.access_token,
            {"userId": second_user.pk, "name": "跨账号写入"},
        )
        second_read = await _read_my_profile(client, "android-cn", second_pair.access_token)
        second_patch = await _patch_my_profile(
            client,
            "android-cn",
            second_pair.access_token,
            {"name": "账号乙更新"},
        )
        guessed_path = await client.get(
            f"/api/v1/android-cn/users/{second_user.pk}/profile",
            headers={"Authorization": f"Bearer {first_pair.access_token}"},
        )
        await _close_worker_database_connection()
        return (
            missing,
            wrong_channel,
            first_read,
            first_patch,
            forged_owner,
            second_read,
            second_patch,
            guessed_path,
        )

    (
        missing,
        wrong_channel,
        first_read,
        first_patch,
        forged_owner,
        second_read,
        second_patch,
        guessed_path,
    ) = asyncio.run(send_isolation_requests())

    assert missing.status_code == 401
    assert missing.json()["code"] == "INVALID_TOKEN"
    assert wrong_channel.status_code == 403
    assert wrong_channel.json()["code"] == "CHANNEL_FORBIDDEN"
    assert first_read.status_code == second_read.status_code == 200
    assert set(first_read.json()["data"]) == {
        "name",
        "gender",
        "birthDate",
        "heightCm",
        "weightKg",
        "medicalHistory",
        "historyJson",
    }
    assert first_read.json()["data"]["name"] == "账号甲"
    assert second_read.json()["data"]["name"] == "账号乙"
    assert first_patch.status_code == second_patch.status_code == 200
    assert forged_owner.status_code == 422
    assert forged_owner.json()["code"] == "INVALID_INPUT"
    assert guessed_path.status_code == 404
    assert Profile.objects.get(user=first_user).name == "账号甲更新"
    assert Profile.objects.get(user=second_user).name == "账号乙更新"

    first_user.is_active = False
    first_user.save(update_fields={"is_active"})

    async def read_disabled_user_profile():
        client = AsyncClient()
        response = await _read_my_profile(client, "android-cn", first_pair.access_token)
        await _close_worker_database_connection()
        return response

    inactive = asyncio.run(read_disabled_user_profile())

    assert inactive.status_code == 401
    assert inactive.json()["code"] == "INVALID_TOKEN"
