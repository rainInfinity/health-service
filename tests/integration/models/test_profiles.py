from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
import pytest

from health_service.models import Profile


@pytest.mark.django_db
def test_profile_shares_user_primary_key_and_is_one_to_one():
    user = get_user_model().objects.create_user(username="profile-owner", password="secret")
    profile = Profile.objects.create(user=user)

    assert profile.pk == user.pk
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Profile.objects.create(user=user)


@pytest.mark.django_db
def test_unfilled_health_values_remain_null_or_absent():
    user = get_user_model().objects.create_user(username="partial-profile", password="secret")
    profile = Profile.objects.create(user=user, history_json={"is_diabetic": True})

    assert profile.name is None
    assert profile.gender == Profile.GenderChoices.UNKNOWN
    assert profile.birth_date is None
    assert profile.height_cm is None
    assert profile.weight_kg is None
    assert profile.history_json == {"is_diabetic": True}
    assert "is_hypertensive" not in profile.history_json


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gender", 3),
        ("height_cm", Decimal("0")),
        ("height_cm", Decimal("-1.00")),
        ("weight_kg", Decimal("0")),
        ("weight_kg", Decimal("-1.00")),
    ],
)
def test_database_rejects_invalid_profile_values(field, value):
    user = get_user_model().objects.create_user(
        username=f"invalid-{field}-{value}",
        password="secret",
    )
    profile = Profile.objects.create(user=user)
    setattr(profile, field, value)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            profile.save()
