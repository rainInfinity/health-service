from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest

from health_service.models import Channel, Model, ModelVersion, Task


def _task_relationships(prefix):
    user = get_user_model().objects.create_user(username=f"{prefix}-user", password="secret")
    channel = Channel.objects.create(label=f"{prefix[:20]}-channel")
    model = Model.objects.create(code=f"{prefix}-model", name="Model")
    version = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key=f"{prefix}.v1",
    )
    return {"user": user, "channel": channel, "model_version": version}


def _create_task(prefix="task", **fields):
    return Task.objects.create(**_task_relationships(prefix), **fields)


@pytest.mark.django_db
def test_draft_task_allows_missing_snapshot_and_business_times():
    task = _create_task()

    assert task.status == Task.StatusChoices.DRAFT
    assert task.profile_snapshot is None
    assert task.submitted_at is None
    assert task.started_at is None
    assert task.finished_at is None


@pytest.mark.django_db
def test_success_requires_result_and_completion_time():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix="success-missing-result",
                status=Task.StatusChoices.SUCCEEDED,
                finished_at=timezone.now(),
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix="success-missing-finished-at",
                status=Task.StatusChoices.SUCCEEDED,
                result_payload={"value": 1},
            )

    task = _create_task(
        prefix="valid-success",
        status=Task.StatusChoices.SUCCEEDED,
        result_payload={"value": 1},
        finished_at=timezone.now(),
    )
    assert task.result_payload == {"value": 1}


@pytest.mark.django_db
def test_failed_task_requires_completion_time_and_nonempty_code():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix="failure-missing-code",
                status=Task.StatusChoices.FAILED,
                finished_at=timezone.now(),
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix="failure-missing-finished-at",
                status=Task.StatusChoices.FAILED,
                failure_code="TASK.ERROR",
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix="failure-empty-code",
                status=Task.StatusChoices.FAILED,
                finished_at=timezone.now(),
                failure_code="",
            )

    task = _create_task(
        prefix="valid-failure",
        status=Task.StatusChoices.FAILED,
        finished_at=timezone.now(),
        failure_code="TASK.ERROR",
    )
    assert task.failure_code == "TASK.ERROR"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "field", "value"),
    [
        (Task.StatusChoices.DRAFT, "result_payload", {"unexpected": True}),
        (Task.StatusChoices.SUBMITTED, "failure_code", "TASK.ERROR"),
        (Task.StatusChoices.RUNNING, "failure_message", "failure"),
        (Task.StatusChoices.SUCCEEDED, "failure_detail", {"reason": "failure"}),
        (Task.StatusChoices.DRAFT, "finished_at", timezone.now()),
    ],
)
def test_nonterminal_or_nonfailed_states_reject_conflicting_fields(status, field, value):
    fields = {"status": status}
    if status == Task.StatusChoices.SUCCEEDED:
        fields.update(result_payload={"value": 1}, finished_at=timezone.now())
    fields[field] = value

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(
                prefix=f"conflict-{status.lower()}-{field}",
                **fields,
            )


@pytest.mark.django_db
def test_task_rejects_unknown_status():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create_task(prefix="invalid-status", status="UNKNOWN")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("relation", ["user", "channel", "model_version"])
def test_task_rejects_missing_foreign_key_targets(relation):
    fields = _task_relationships(f"missing-{relation}")
    fields.pop(relation)
    fields[f"{relation}_id"] = -1

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Task.objects.create(**fields)
