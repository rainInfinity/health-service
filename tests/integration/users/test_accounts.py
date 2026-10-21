"""验证账号注册事务及 PostgreSQL 唯一约束行为。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from health_service.application.accounts import UsernameAlreadyExists, register_user
from health_service.models import Channel, Profile


@pytest.mark.django_db(transaction=True)
def test_registration_rolls_back_account_when_profile_insert_fails():
    """用真实 PostgreSQL 触发器验证资料创建失败会回滚账号。"""
    channel = "android-cn"
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
            register_user(
                username=username,
                password="Violet-forest-549!strong",
                channel=channel,
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER IF EXISTS health_service_test_fail_profile ON health_profile"
            )
            cursor.execute("DROP FUNCTION IF EXISTS health_service_test_fail_profile_insert()")

    assert not get_user_model().objects.filter(username=username).exists()
    assert not Profile.objects.filter(user__username=username).exists()
    assert not Channel.objects.filter(label=channel).exists()


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
            register_user(
                username=username,
                password="Violet-forest-549!strong",
                channel="android-intl",
            )
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
                register_user(
                    username=username,
                    password=password,
                    channel="android-cn",
                )
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


@pytest.mark.django_db(transaction=True)
def test_registration_channel_migration_preserves_legacy_account():
    """验证新增可空关系保留历史账号数据并将未知来源置空。"""
    previous = ("health_service", "0008_task_attempt_count_task_execution_token_and_more")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate([previous])
        old_apps = executor.loader.project_state([previous]).apps
        old_user_model = old_apps.get_model("health_service", "User")
        now = timezone.now()
        legacy_user = old_user_model.objects.create(
            username="registration-migration-legacy",
            password="not-a-real-hash",
            phone=None,
            is_staff=False,
            is_superuser=False,
            created_at=now,
            updated_at=now,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        current_apps = executor.loader.project_state(latest).apps
        migrated_user = current_apps.get_model("health_service", "User").objects.get(
            pk=legacy_user.pk
        )

        assert migrated_user.username == "registration-migration-legacy"
        assert migrated_user.password == "not-a-real-hash"
        assert migrated_user.registration_channel_id is None
    finally:
        MigrationExecutor(connection).migrate(latest)
