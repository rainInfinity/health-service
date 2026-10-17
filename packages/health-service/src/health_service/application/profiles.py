"""编排本人健康资料的读取和事务性部分更新用例。"""

from django.db import transaction

from health_service.infrastructure.persistence.profiles import (
    create_profile as persist_create_profile,
)
from health_service.infrastructure.persistence.profiles import (
    lock_account,
    lock_profile,
)
from health_service.infrastructure.persistence.profiles import (
    read_profile as read_persisted_profile,
)
from health_service.infrastructure.persistence.profiles import (
    update_profile as persist_update_profile,
)
from health_service.schemas.internal import ProfileData


def read_profile(user_id: int) -> ProfileData | None:
    """读取用户现有资料；缺失时返回空值且不创建记录。"""
    return read_persisted_profile(user_id=user_id)


def update_profile(user_id: int, changes: dict[str, object]) -> ProfileData | None:
    """锁定账号后合并本人资料字段并定向保存一次。"""
    with transaction.atomic():
        lock_account(user_id=user_id)
        profile = lock_profile(user_id=user_id)
        values = {
            field: changes[field]
            for field in (
                "name",
                "gender",
                "birth_date",
                "height_cm",
                "weight_kg",
                "medical_history",
            )
            if field in changes
        }

        if "history_json" in changes:
            history_patch = changes["history_json"]
            if history_patch is None:
                values["history_json"] = None
            else:
                existing_history = None if profile is None else profile.history_json
                if existing_history is None:
                    existing_history = {}
                if not isinstance(existing_history, dict):
                    raise TypeError("Stored history_json must be an object")
                merged_history = dict(existing_history)
                for key, value in history_patch.items():
                    if value is None:
                        merged_history.pop(key, None)
                    else:
                        merged_history[key] = value
                values["history_json"] = merged_history

        if not values:
            return profile
        if profile is None:
            return persist_create_profile(user_id=user_id, values=values)

        persist_update_profile(user_id=user_id, changes=values)
        return profile.model_copy(update=values)
