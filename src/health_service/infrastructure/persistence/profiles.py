"""实现健康资料的具体 ORM 持久化操作。"""

from django.contrib.auth import get_user_model
from django.utils import timezone

from health_service.models import Profile
from health_service.schemas.internal import ProfileData


def read_profile(*, user_id: int) -> ProfileData | None:
    """读取现有资料；资料缺失时不创建记录。"""
    profile = Profile.objects.filter(user_id=user_id).first()
    return None if profile is None else _profile_data(profile)


def lock_account(*, user_id: int) -> None:
    """在调用方事务内锁定账号行作为资料写入的稳定锁点。"""
    get_user_model().objects.select_for_update().get(pk=user_id)


def lock_profile(*, user_id: int) -> ProfileData | None:
    """在调用方事务内锁定并读取现有资料。"""
    profile = Profile.objects.select_for_update().filter(user_id=user_id).first()
    return None if profile is None else _profile_data(profile)


def create_profile(
    *,
    user_id: int,
    values: dict[str, object] | None = None,
) -> ProfileData:
    """为账号创建资料并返回不依赖 ORM 的已求值结果。"""
    profile = Profile.objects.create(user_id=user_id, **(values or {}))
    return _profile_data(profile)


def update_profile(*, user_id: int, changes: dict[str, object]) -> None:
    """定向更新已锁定的资料字段并维护修改时间。"""
    updated = Profile.objects.filter(user_id=user_id).update(
        **changes,
        updated_at=timezone.now(),
    )
    if updated != 1:
        raise Profile.DoesNotExist(f"Profile with user_id={user_id} was not updated")


def _profile_data(profile: Profile) -> ProfileData:
    """将已加载的资料模型投影为内部结果。"""
    return ProfileData(
        user_id=profile.user_id,
        name=profile.name,
        gender=profile.gender,
        birth_date=profile.birth_date,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        medical_history=profile.medical_history,
        history_json=profile.history_json,
    )
