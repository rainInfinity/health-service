import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from health_service.models import (
    Channel,
    MetricDefinition,
    Model,
    ModelVersion,
    Task,
    TaskMetric,
)


def _create_success_task(prefix):
    user = get_user_model().objects.create_user(username=f"{prefix}-user", password="secret")
    channel = Channel.objects.create(label=f"{prefix[:20]}-channel")
    model = Model.objects.create(code=f"{prefix}-model", name="Model")
    version = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key=f"{prefix}.v1",
    )
    return Task.objects.create(
        user=user,
        channel=channel,
        model_version=version,
        status=Task.StatusChoices.SUCCEEDED,
        result_payload={"complete": True},
        finished_at=timezone.now(),
    )


@pytest.mark.django_db
def test_false_and_numeric_zero_are_values_not_missing_metrics():
    task = _create_success_task("metric-values")
    numeric = MetricDefinition.objects.create(
        code="metric-number",
        value_type=MetricDefinition.ValueTypeChoices.NUMBER,
    )
    boolean = MetricDefinition.objects.create(
        code="metric-boolean",
        value_type=MetricDefinition.ValueTypeChoices.BOOLEAN,
    )
    absent = MetricDefinition.objects.create(
        code="metric-absent",
        value_type=MetricDefinition.ValueTypeChoices.NUMBER,
    )

    numeric_projection = TaskMetric.objects.create(
        task=task,
        metric=numeric,
        numeric_value=0.0,
    )
    boolean_projection = TaskMetric.objects.create(
        task=task,
        metric=boolean,
        boolean_value=False,
    )

    assert numeric_projection.numeric_value == 0.0
    assert numeric_projection.boolean_value is None
    assert boolean_projection.boolean_value is False
    assert boolean_projection.numeric_value is None
    assert not TaskMetric.objects.filter(task=task, metric=absent).exists()
    assert TaskMetric.objects.filter(task=task).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("numeric_value", "boolean_value"),
    [(None, None), (1.5, False)],
)
def test_projection_requires_exactly_one_value(numeric_value, boolean_value):
    task = _create_success_task("metric-exclusive")
    metric = MetricDefinition.objects.create(
        code="metric-exclusive",
        value_type=MetricDefinition.ValueTypeChoices.NUMBER,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        TaskMetric.objects.create(
            task=task,
            metric=metric,
            numeric_value=numeric_value,
            boolean_value=boolean_value,
        )


@pytest.mark.django_db
def test_task_and_metric_pair_is_unique_but_metric_can_be_reused():
    first_task = _create_success_task("metric-owner-one")
    second_task = _create_success_task("metric-owner-two")
    metric = MetricDefinition.objects.create(
        code="shared-metric",
        value_type=MetricDefinition.ValueTypeChoices.NUMBER,
    )
    TaskMetric.objects.create(task=first_task, metric=metric, numeric_value=10.0)
    TaskMetric.objects.create(task=second_task, metric=metric, numeric_value=11.0)

    assert TaskMetric.objects.filter(metric=metric).count() == 2
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskMetric.objects.create(task=first_task, metric=metric, numeric_value=12.0)
