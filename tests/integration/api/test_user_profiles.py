"""验证普通渠道本人健康资料 API 的 PostgreSQL 与 HTTP 行为。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import AsyncClient

from health_service.application.profiles import update_profile
from health_service.infrastructure.auth.tokens import issue_token_pair
from health_service.models import Profile


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


async def _close_worker_database_connection() -> None:
    """关闭异步视图同步用例线程持有的 PostgreSQL 连接。"""
    await sync_to_async(connections.close_all, thread_sensitive=True)()

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
        history_json={"is_diabetic": False},
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
            "is_diabetic": True,
            "is_hypertensive": True,
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
        "is_hypertensive": False,
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
            "history_json": {"is_diabetic": True},
        },
        {
            "medical_history": "并发病史",
            "history_json": {"is_hypertensive": False},
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
        "is_diabetic": True,
        "is_hypertensive": False,
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


def test_profile_openapi_documents_optional_public_history_flags():
    """验证资料 OpenAPI 公开驼峰病史键且不把缺失键声明为必填。"""

    async def read_schema():
        """读取实际 API OpenAPI JSON。"""
        return await AsyncClient().get("/api/v1/openapi.json")

    response = asyncio.run(read_schema())
    assert response.status_code == 200
    schema = response.json()
    components = schema["components"]["schemas"]
    operation = schema["paths"]["/api/v1/{channel}/users/me/profile"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    envelope_name = response_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    profile_name = components[envelope_name]["properties"]["data"]["$ref"].rsplit(
        "/",
        maxsplit=1,
    )[-1]
    history_schema = components[profile_name]["properties"]["historyJson"]

    assert set(history_schema["properties"]) == {"isDiabetic", "isHypertensive"}
    assert not {"is_diabetic", "is_hypertensive"} & set(history_schema["properties"])
    assert not {"isDiabetic", "isHypertensive"} & set(history_schema.get("required", []))
    assert history_schema["additionalProperties"] is True
