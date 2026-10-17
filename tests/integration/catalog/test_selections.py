"""验证渠道模型版本独立选择与旧草稿原子终止。"""

import pytest
from django.contrib.auth import get_user_model
from health_service.application.catalog import (
    create_model,
    create_model_version,
    publish_model_version,
    select_channel_model_version,
)
from health_service.application.catalog_exceptions import (
    CatalogConfigurationInvalid,
    CatalogNotFound,
)
from health_service.models import Channel, ChannelModelSelection, Task


def _published_version(*, model_id: int, version: str):
    """创建并发布零制品示例版本。"""
    draft = create_model_version(
        model_id=model_id,
        version=version,
        algorithm_key="hr_br.example",
        params=None,
    )
    return publish_model_version(version_id=draft.version_id)


@pytest.mark.django_db
def test_switch_fails_only_old_channel_drafts_and_rollback_does_not_restore_them():
    """版本切换只失败本渠道旧草稿，提交任务不变且回退不复活草稿。"""
    model = create_model(code="pulse", name="Pulse", description=None)
    old_version = _published_version(model_id=model.model_id, version="1.0")
    new_version = _published_version(model_id=model.model_id, version="2.0")
    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=old_version.version_id,
    )
    ios_channel = Channel.objects.get(label="ios-cn")
    selection = ChannelModelSelection.objects.get(
        channel=ios_channel,
        model_id=model.model_id,
    )
    original_selection_updated_at = selection.updated_at
    android_channel = Channel.objects.create(label="android-cn")
    user = get_user_model().objects.create_user(
        username="selection-task-owner",
        password="Pine-river-426!strong",
    )
    draft = Task.objects.create(
        user=user,
        channel=ios_channel,
        model_version_id=old_version.version_id,
    )
    submitted = Task.objects.create(
        user=user,
        channel=ios_channel,
        model_version_id=old_version.version_id,
        status=Task.StatusChoices.SUBMITTED,
    )
    other_channel_draft = Task.objects.create(
        user=user,
        channel=android_channel,
        model_version_id=old_version.version_id,
    )

    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=old_version.version_id,
    )
    selection.refresh_from_db()
    assert selection.updated_at == original_selection_updated_at
    draft.refresh_from_db()
    assert draft.status == Task.StatusChoices.DRAFT

    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=new_version.version_id,
    )
    draft.refresh_from_db()
    submitted.refresh_from_db()
    other_channel_draft.refresh_from_db()
    selection.refresh_from_db()
    assert selection.current_version_id == new_version.version_id
    assert draft.status == Task.StatusChoices.FAILED
    assert draft.failure_code == "TASK.MODEL_SWITCHED"
    assert draft.finished_at is not None
    assert submitted.status == Task.StatusChoices.SUBMITTED
    assert other_channel_draft.status == Task.StatusChoices.DRAFT
    first_finished_at = draft.finished_at

    select_channel_model_version(
        channel="ios-cn",
        model_id=model.model_id,
        version_id=old_version.version_id,
    )
    draft.refresh_from_db()
    selection.refresh_from_db()
    assert draft.status == Task.StatusChoices.FAILED
    assert draft.finished_at == first_finished_at
    assert selection.current_version_id == old_version.version_id


@pytest.mark.django_db
def test_selection_rejects_unknown_channel_unpublished_or_foreign_version():
    """无效渠道、未发布版本及其他模型版本均不能改变当前选择。"""
    first_model = create_model(code="first", name="First", description=None)
    second_model = create_model(code="second", name="Second", description=None)
    published = _published_version(model_id=first_model.model_id, version="1.0")
    unpublished = create_model_version(
        model_id=first_model.model_id,
        version="2.0",
        algorithm_key="hr_br.example",
        params=None,
    )
    foreign = _published_version(model_id=second_model.model_id, version="1.0")

    with pytest.raises(CatalogNotFound):
        select_channel_model_version(
            channel="unknown",
            model_id=first_model.model_id,
            version_id=published.version_id,
        )
    with pytest.raises(CatalogConfigurationInvalid):
        select_channel_model_version(
            channel="ios-cn",
            model_id=first_model.model_id,
            version_id=unpublished.version_id,
        )
    with pytest.raises(CatalogConfigurationInvalid):
        select_channel_model_version(
            channel="ios-cn",
            model_id=first_model.model_id,
            version_id=foreign.version_id,
        )

    assert not ChannelModelSelection.objects.exists()
