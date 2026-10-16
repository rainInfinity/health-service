from django.db import IntegrityError, transaction
import pytest

from health_service.models import (
    Channel,
    ChannelModelSelection,
    MetricDefinition,
    Model,
    ModelArtifact,
    ModelVersion,
    ModelVersionMetric,
)


@pytest.mark.django_db
def test_channel_label_is_unique():
    Channel.objects.create(label="android-cn")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Channel.objects.create(label="android-cn")


@pytest.mark.django_db
def test_model_code_is_unique():
    Model.objects.create(code="pulse", name="Pulse")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Model.objects.create(code="pulse", name="Another pulse")


@pytest.mark.django_db
def test_model_version_is_unique_within_its_model():
    model = Model.objects.create(code="blood-pressure", name="Blood pressure")
    ModelVersion.objects.create(model=model, version="1.0", algorithm_key="bp.v1")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ModelVersion.objects.create(model=model, version="1.0", algorithm_key="bp.v2")


@pytest.mark.django_db
def test_channels_keep_independent_current_version_selections():
    channel_one = Channel.objects.create(label="android-cn")
    channel_two = Channel.objects.create(label="ios-cn")
    model = Model.objects.create(code="pulse", name="Pulse")
    version_one = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key="pulse.v1",
    )
    version_two = ModelVersion.objects.create(
        model=model,
        version="2.0",
        algorithm_key="pulse.v2",
    )

    first = ChannelModelSelection.objects.create(
        channel=channel_one,
        model=model,
        current_version=version_one,
    )
    second = ChannelModelSelection.objects.create(
        channel=channel_two,
        model=model,
        current_version=version_two,
    )

    assert first.current_version_id == version_one.pk
    assert second.current_version_id == version_two.pk
    assert ChannelModelSelection.objects.filter(model=model).count() == 2


@pytest.mark.django_db
def test_channel_and_model_pair_is_unique():
    channel = Channel.objects.create(label="manager")
    model = Model.objects.create(code="pulse", name="Pulse")
    version = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key="pulse.v1",
    )
    ChannelModelSelection.objects.create(
        channel=channel,
        model=model,
        current_version=version,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ChannelModelSelection.objects.create(
                channel=channel,
                model=model,
                current_version=version,
            )


@pytest.mark.django_db
def test_artifact_names_are_version_scoped_and_storage_keys_are_global():
    model = Model.objects.create(code="ecg", name="ECG")
    version_one = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key="ecg.v1",
    )
    version_two = ModelVersion.objects.create(
        model=model,
        version="2.0",
        algorithm_key="ecg.v2",
    )
    ModelArtifact.objects.create(
        model_version=version_one,
        logical_name="weights",
        storage_key="ecg/v1/weights.bin",
        size_bytes=128,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ModelArtifact.objects.create(
                model_version=version_one,
                logical_name="weights",
                storage_key="ecg/v1/other.bin",
                size_bytes=128,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ModelArtifact.objects.create(
                model_version=version_two,
                logical_name="metadata",
                storage_key="ecg/v1/weights.bin",
                size_bytes=128,
            )


@pytest.mark.django_db
@pytest.mark.parametrize("size_bytes", [0, -1])
def test_artifact_size_must_be_positive(size_bytes):
    model = Model.objects.create(code=f"artifact-size-{size_bytes}", name="Model")
    version = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key="artifact-size.test",
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ModelArtifact.objects.create(
                model_version=version,
                logical_name="weights",
                storage_key=f"artifact-size/{size_bytes}",
                size_bytes=size_bytes,
            )


@pytest.mark.django_db
def test_metric_definitions_are_typed_unique_and_shareable_by_versions():
    model = Model.objects.create(code="vitals", name="Vitals")
    version_one = ModelVersion.objects.create(
        model=model,
        version="1.0",
        algorithm_key="vitals.v1",
    )
    version_two = ModelVersion.objects.create(
        model=model,
        version="2.0",
        algorithm_key="vitals.v2",
    )
    numeric_metric = MetricDefinition.objects.create(
        code="heart-rate",
        value_type=MetricDefinition.ValueTypeChoices.NUMBER,
        unit="bpm",
    )
    boolean_metric = MetricDefinition.objects.create(
        code="irregular-rhythm",
        value_type=MetricDefinition.ValueTypeChoices.BOOLEAN,
    )
    ModelVersionMetric.objects.create(model_version=version_one, metric=numeric_metric)
    ModelVersionMetric.objects.create(model_version=version_two, metric=numeric_metric)
    ModelVersionMetric.objects.create(model_version=version_one, metric=boolean_metric)

    assert ModelVersionMetric.objects.filter(metric=numeric_metric).count() == 2
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ModelVersionMetric.objects.create(model_version=version_one, metric=numeric_metric)


@pytest.mark.django_db
def test_metric_code_and_value_type_constraints():
    MetricDefinition.objects.create(code="body-temperature", value_type="number")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MetricDefinition.objects.create(code="body-temperature", value_type="boolean")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MetricDefinition.objects.create(code="invalid-type", value_type="text")
