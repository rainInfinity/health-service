"""编排本人健康资料的读取和事务性部分更新用例。"""

from django.contrib.auth import get_user_model
from django.db import transaction

from health_service.models import Profile


def read_profile(user_id: int) -> Profile | None:
    """读取用户现有资料；缺失时返回空值且不创建记录。"""
    return Profile.objects.filter(user_id=user_id).first()


def update_profile(user_id: int, changes: dict[str, object]) -> Profile | None:
    """锁定账号后合并本人资料字段并保存一次。"""
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        profile = Profile.objects.select_for_update().filter(user_id=user_id).first()
        if profile is None:
            if not changes:
                return None
            profile = Profile(user=user)

        for field in (
            "name",
            "gender",
            "birth_date",
            "height_cm",
            "weight_kg",
            "medical_history",
        ):
            if field in changes:
                setattr(profile, field, changes[field])

        if "history_json" in changes:
            history_patch = changes["history_json"]
            if history_patch is None:
                profile.history_json = None
            else:
                existing_history = profile.history_json or {}
                if not isinstance(existing_history, dict):
                    raise TypeError("Stored history_json must be an object")
                merged_history = dict(existing_history)
                for key, value in history_patch.items():
                    if value is None:
                        merged_history.pop(key, None)
                    else:
                        merged_history[key] = value
                profile.history_json = merged_history

        if changes:
            profile.save()
        return profile
