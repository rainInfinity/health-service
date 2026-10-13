"""验证账号注册事务及 PostgreSQL 唯一约束行为。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections

from health_service.application.accounts import UsernameAlreadyExists, register_user
from health_service.models import Profile


@pytest.mark.django_db(transaction=True)
def test_registration_rolls_back_account_when_profile_insert_fails():
    """用真实 PostgreSQL 触发器验证资料创建失败会回滚账号。"""
    username = "registration-profile-failure"
    with connection.cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS health_service_test_fail_profile ON health_profile")
        cursor.execute("DROP FUNCTION IF EXISTS health_service_test_fail_profile_insert()")
        cursor.execute(
            """
            CREATE FUNCTION health_service_test_fail_profile_insert() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'forced profile insert failure' USING ERRCODE = '23514';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER health_service_test_fail_profile
            BEFORE INSERT ON health_profile
            FOR EACH ROW EXECUTE FUNCTION health_service_test_fail_profile_insert()
            """
        )

    try:
        with pytest.raises(IntegrityError):
            register_user(username=username, password="Violet-forest-549!strong")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS health_service_test_fail_profile ON health_profile")
            cursor.execute("DROP FUNCTION IF EXISTS health_service_test_fail_profile_insert()")

    assert not get_user_model().objects.filter(username=username).exists()
    assert not Profile.objects.filter(user__username=username).exists()


@pytest.mark.django_db(transaction=True)
def test_non_username_integrity_failure_is_not_reported_as_duplicate_username():
    """用非用户名检查约束验证数据库故障不会伪装成用户名冲突。"""
    username = "registration-non-username-failure"
    constraint_name = "health_user_test_reject_registration"
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE health_user DROP CONSTRAINT IF EXISTS {constraint_name}")
        cursor.execute(
            f"ALTER TABLE health_user ADD CONSTRAINT {constraint_name} "
            f"CHECK (username <> '{username}')"
        )

    try:
        with pytest.raises(IntegrityError):
            register_user(username=username, password="Violet-forest-549!strong")
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE health_user DROP CONSTRAINT IF EXISTS {constraint_name}")

    assert not get_user_model().objects.filter(username=username).exists()
    assert not Profile.objects.filter(user__username=username).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_registration_commits_one_account_and_profile():
    """用两个 PostgreSQL 连接竞争用户名，验证唯一约束只允许一份提交。"""
    username = "concurrent-duplicate-registration"
    password = "Violet-forest-549!strong"
    barrier = Barrier(2)

    def register_concurrently() -> str:
        """在独立数据库连接中尝试注册同一用户名。"""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                register_user(username=username, password=password)
            except UsernameAlreadyExists:
                return "conflict"
            return "created"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: register_concurrently(), range(2)))

    assert sorted(outcomes) == ["conflict", "created"]
    user = get_user_model().objects.get(username=username)
    assert Profile.objects.filter(user_id=user.pk).count() == 1
