from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest


@pytest.mark.django_db
def test_django_uses_custom_user_and_hashes_password():
    user_model = get_user_model()
    assert user_model._meta.db_table == "health_user"

    user = user_model.objects.create_user(
        username="hashed-password-user",
        password="correct-horse-battery-staple",
    )

    assert user.check_password("correct-horse-battery-staple")
    assert user.password != "correct-horse-battery-staple"


@pytest.mark.django_db
def test_multiple_users_can_have_no_phone():
    user_model = get_user_model()

    first = user_model.objects.create_user(username="no-phone-one", password="secret")
    second = user_model.objects.create_user(username="no-phone-two", password="secret")

    assert first.phone is None
    assert second.phone is None
    assert first.pk != second.pk


@pytest.mark.django_db
def test_soft_deleted_user_keeps_phone_reserved():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="soft-deleted-owner",
        password="secret",
        phone="+8613800000000",
    )
    user.is_active = False
    user.deleted_at = timezone.now()
    user.save(update_fields={"is_active", "deleted_at"})

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user_model.objects.create_user(
                username="replacement-owner",
                password="secret",
                phone="+8613800000000",
            )


@pytest.mark.django_db
def test_audit_timestamps_and_optional_actor_snapshot():
    user_model = get_user_model()
    before_create = timezone.now()
    user = user_model.objects.create_user(
        username="audited-user",
        password="secret",
        created_by="system:test",
    )
    after_create = timezone.now()

    assert before_create <= user.created_at <= after_create
    assert user.created_at == user.updated_at
    assert user.created_by == "system:test"
    assert user.updated_by is None

    created_at = user.created_at
    user.updated_by = "user:42"
    user.save(update_fields={"updated_by"})
    user.refresh_from_db()

    assert user.created_at == created_at
    assert user.updated_by == "user:42"
    assert user.updated_at >= user.created_at
