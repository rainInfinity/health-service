"""验证健康资料应用用例与 PostgreSQL 持久化行为。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from health_service.application.profiles import read_profile, update_profile
from health_service.models import Profile
from health_service.schemas.internal import ProfileData


@pytest.mark.django_db(transaction=True)
def test_updates_maintain_audit_fields_and_empty_changes_do_not_write():
    """验证实际更新维护审计时间，空更改集不创建或写入资料。"""
    user_model = get_user_model()
    missing_user = user_model.objects.create_user(
        username="profile-empty-missing",
        password="Violet-forest-549!strong",
    )
    assert update_profile(missing_user.pk, {}) is None
    assert not Profile.objects.filter(user_id=missing_user.pk).exists()

    user = user_model.objects.create_user(
        username="profile-audit-fields",
        password="Violet-forest-549!strong",
    )
    profile = Profile.objects.create(user=user, name="未修改", updated_by="operator:existing")
    old_created_at = timezone.now() - timedelta(days=5)
    old_updated_at = timezone.now() - timedelta(days=1)
    Profile.objects.filter(user_id=user.pk).update(
        created_at=old_created_at,
        updated_at=old_updated_at,
    )

    with CaptureQueriesContext(connection) as queries:
        unchanged = update_profile(user.pk, {})

    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for query in queries.captured_queries
    )
    assert unchanged.name == "未修改"
    profile.refresh_from_db()
    assert profile.created_at == old_created_at
    assert profile.updated_at == old_updated_at
    assert profile.updated_by == "operator:existing"

    update_profile(user.pk, {"height_cm": Decimal("167.00")})
    profile.refresh_from_db()
    assert profile.height_cm == Decimal("167.00")
    assert profile.created_at == old_created_at
    assert profile.updated_at > old_updated_at
    assert profile.updated_by == "operator:existing"



@pytest.mark.django_db(transaction=True)
def test_reading_missing_profile_does_not_create_a_record():
    """验证读取不存在的资料返回缺失且不写入数据库。"""
    user = get_user_model().objects.create_user(
        username="missing-profile-read",
        password="Violet-forest-549!strong",
    )

    assert read_profile(user.pk) is None
    assert not Profile.objects.filter(user_id=user.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_first_update_creates_profile_and_partial_update_preserves_other_values():
    """验证下划线更改集可首次建档并按字段、病史键合并。"""
    user = get_user_model().objects.create_user(
        username="profile-internal-update",
        password="Violet-forest-549!strong",
    )

    created = update_profile(
        user.pk,
        {
            "name": "资料本人",
            "birth_date": date(1990, 2, 3),
            "height_cm": Decimal("166.25"),
            "history_json": {"is_diabetic": True},
        },
    )

    assert isinstance(created, ProfileData)
    assert created.name == "资料本人"
    assert created.birth_date == date(1990, 2, 3)
    assert created.height_cm == Decimal("166.25")
    assert created.history_json == {"is_diabetic": True}
    assert Profile.objects.filter(user_id=user.pk).count() == 1

    updated = update_profile(
        user.pk,
        {
            "weight_kg": Decimal("61.50"),
            "history_json": {"is_hypertensive": False},
        },
    )

    assert updated.name == "资料本人"
    assert updated.birth_date == date(1990, 2, 3)
    assert updated.height_cm == Decimal("166.25")
    assert updated.weight_kg == Decimal("61.50")
    assert updated.history_json == {"is_diabetic": True, "is_hypertensive": False}
    persisted = Profile.objects.get(user_id=user.pk)
    assert persisted.history_json == {"is_diabetic": True, "is_hypertensive": False}


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_updates_merge_distinct_fields_and_history_keys():
    """验证并发首次填写保留两组资料字段与内部病史键。"""
    user = get_user_model().objects.create_user(
        username="profile-concurrent-first-update",
        password="Violet-forest-549!strong",
    )
    barrier = Barrier(2)

    def update_concurrently(changes: dict[str, object]) -> None:
        """在独立数据库连接中并发提交资料更改集。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            update_profile(user.pk, changes)
        finally:
            connections.close_all()

    changes = (
        {
            "name": "并发首次姓名",
            "history_json": {"is_diabetic": True},
        },
        {
            "medical_history": "并发首次病史",
            "history_json": {"is_hypertensive": False},
        },
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(update_concurrently, item) for item in changes]
        for future in futures:
            future.result(timeout=30)

    profile = Profile.objects.get(user_id=user.pk)
    assert Profile.objects.filter(user_id=user.pk).count() == 1
    assert profile.name == "并发首次姓名"
    assert profile.medical_history == "并发首次病史"
    assert profile.history_json == {"is_diabetic": True, "is_hypertensive": False}
