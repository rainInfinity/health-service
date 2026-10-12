from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest

from health_service.models import Channel, Model, ModelVersion, Task, TaskInputFile


def _create_task(prefix):
    user = get_user_model().objects.create_user(username=f"{prefix}-user", password="secret")
    channel = Channel.objects.create(label=f"{prefix[:20]}-channel")
    model = Model.objects.create(code=f"{prefix}-model", name="Model")
    version = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key=f"{prefix}.v1",
    )
    return Task.objects.create(user=user, channel=channel, model_version=version)


def _create_file(task, storage_key, *, is_selected, logical_name="ecg", size_bytes=128):
    return TaskInputFile.objects.create(
        task=task,
        logical_name=logical_name,
        storage_key=storage_key,
        size_bytes=size_bytes,
        uploaded_at=timezone.now(),
        is_selected=is_selected,
    )


@pytest.mark.django_db
def test_unselected_same_name_history_can_coexist_with_current_file():
    task = _create_task("file-history")
    _create_file(task, "input/old-one.ecg", is_selected=False)
    _create_file(task, "input/old-two.ecg", is_selected=False)
    _create_file(task, "input/current.ecg", is_selected=True)

    files = TaskInputFile.objects.filter(task=task, logical_name="ecg")
    assert files.count() == 3
    assert list(files.filter(is_selected=True).values_list("storage_key", flat=True)) == [
        "input/current.ecg"
    ]


@pytest.mark.django_db
def test_only_one_same_name_file_can_be_selected_per_task():
    task = _create_task("file-selected")
    _create_file(task, "input/selected-one.ecg", is_selected=True)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_file(task, "input/selected-two.ecg", is_selected=True)


@pytest.mark.django_db
@pytest.mark.parametrize("size_bytes", [0, -1])
def test_file_size_must_be_positive(size_bytes):
    task = _create_task(f"file-size-{size_bytes}")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_file(
                task,
                f"input/invalid-size-{size_bytes}.ecg",
                is_selected=True,
                size_bytes=size_bytes,
            )


@pytest.mark.django_db
def test_storage_key_is_unique_across_tasks():
    first_task = _create_task("file-key-one")
    second_task = _create_task("file-key-two")
    _create_file(first_task, "input/shared-key.ecg", is_selected=True)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_file(second_task, "input/shared-key.ecg", is_selected=True)
