"""验证版本制品登记、指标投影与不可变发布边界。"""

from io import BytesIO
from pathlib import Path

import pytest
from django.test import override_settings
from health_inference.registry import _ALGORITHMS
from health_inference.types import AlgorithmDefinition, InferenceInput, InferenceOutput
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    set_model_version_metrics,
    update_model_version,
    upload_model_artifact,
)
from health_service.application.catalog_exceptions import (
    CatalogConfigurationInvalid,
    CatalogConflict,
)
from health_service.infrastructure.persistence.catalog import (
    get_model_version,
    list_model_version_metrics,
)
from health_service.models import MetricDefinition, ModelArtifact, ModelVersionMetric


def _create_example_version(*, code: str = "pulse", params: dict | None = None):
    """创建 HR/BR 非临床示例的未发布版本。"""
    model = create_model(code=code, name="Pulse", description=None)
    return create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="hr_br.example",
        params=params,
    )


def _add_example_metrics(version_id: int) -> None:
    """创建并选择示例算法声明的三个带单位指标。"""
    definitions = (
        ("example.ppg.sample_count", "number", "samples"),
        ("example.ppg.duration_seconds", "number", "seconds"),
        ("example.ppg.sampling_rate_hz", "number", "hertz"),
    )
    for code, value_type, unit in definitions:
        create_metric_definition(
            code=code,
            description=code,
            value_type=value_type,
            unit=unit,
        )
    set_model_version_metrics(
        version_id=version_id,
        metric_codes=tuple(code for code, _, _ in definitions),
    )


def _validate_test_parameters(params):
    """测试算法不接受任何发布参数。"""
    if params:
        raise ValueError("参数必须为空")


def _validate_test_result(output):
    """测试算法不执行推理结果验证。"""


def _run_test_algorithm(inference_input: InferenceInput) -> InferenceOutput:
    """阻止发布测试意外进入实际算法执行。"""
    raise AssertionError("发布校验不应执行算法")


def _required_artifact_algorithm() -> AlgorithmDefinition:
    """构造只用于验证制品发布边界的纯契约定义。"""
    return AlgorithmDefinition(
        key="test.catalog.required-artifact",
        is_example=False,
        required_inputs=(),
        required_artifacts=("weights",),
        required_profile_fields=(),
        parameter_validator=_validate_test_parameters,
        result_validator=_validate_test_result,
        runner=_run_test_algorithm,
        metric_contracts=(),
    )


@pytest.mark.django_db
def test_artifact_upload_registers_complete_file_and_removes_duplicate_orphan(tmp_path):
    """同版本逻辑名冲突不覆盖已登记文件且清除本次孤儿文件。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=32,
    ):
        version = _create_example_version()
        artifact = upload_model_artifact(
            version_id=version.version_id,
            logical_name="weights",
            source=BytesIO(b"first artifact"),
        )
        stored_path = tmp_path / "artifacts" / artifact.storage_key
        assert stored_path.read_bytes() == b"first artifact"
        assert artifact.size_bytes == len(b"first artifact")

        with pytest.raises(CatalogConflict):
            upload_model_artifact(
                version_id=version.version_id,
                logical_name="weights",
                source=BytesIO(b"duplicate artifact"),
            )

        assert ModelArtifact.objects.filter(model_version_id=version.version_id).count() == 1
        assert stored_path.read_bytes() == b"first artifact"
        assert tuple(path for path in (tmp_path / "artifacts").rglob("*.bin")) == (
            stored_path,
        )


@pytest.mark.django_db
def test_unknown_metric_does_not_replace_existing_projection():
    """未知代码拒绝整批替换并保留原有合法指标集合。"""
    version = _create_example_version()
    metric = create_metric_definition(
        code="example.ppg.sample_count",
        description="样本数",
        value_type="number",
        unit="samples",
    )

    with pytest.raises(CatalogConflict):
        create_metric_definition(
            code=metric.code,
            description="重复定义",
            value_type="number",
            unit="samples",
        )
    set_model_version_metrics(
        version_id=version.version_id,
        metric_codes=(metric.code,),
    )

    with pytest.raises(CatalogConfigurationInvalid):
        set_model_version_metrics(
            version_id=version.version_id,
            metric_codes=(metric.code, "missing.metric"),
        )

    selected = list_model_version_metrics(version_id=version.version_id)
    assert tuple(item.code for item in selected) == (metric.code,)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("value_type", "unit"),
    [("boolean", None), ("number", "milliseconds")],
)
def test_mismatched_metric_contract_is_rejected(value_type, unit):
    """指标类型或单位与算法契约不匹配时拒绝投影。"""
    version = _create_example_version()
    with pytest.raises(CatalogConfigurationInvalid):
        create_metric_definition(
            code="example.ppg.sample_count",
            description="错误类型",
            value_type="boolean",
            unit="samples",
        )

    mismatched = MetricDefinition.objects.create(
        code="example.ppg.sample_count",
        description="错误语义",
        value_type=value_type,
        unit=unit,
    )
    with pytest.raises(CatalogConfigurationInvalid):
        set_model_version_metrics(
            version_id=version.version_id,
            metric_codes=(mismatched.code,),
        )
    assert not ModelVersionMetric.objects.filter(
        model_version_id=version.version_id
    ).exists()


@pytest.mark.django_db
def test_example_version_publishes_without_artifacts_and_is_immutable(tmp_path):
    """零制品示例可发布，重发不改时间且已发布配置不能变更。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=32,
    ):
        version = _create_example_version()
        _add_example_metrics(version.version_id)
        published = publish_model_version(version_id=version.version_id)
        repeated = publish_model_version(version_id=version.version_id)

        assert published.published_at is not None
        assert repeated.published_at == published.published_at
        assert not ModelArtifact.objects.filter(model_version_id=version.version_id).exists()

        with pytest.raises(CatalogConflict):
            update_model_version(
                version_id=version.version_id,
                changes={"params": {"window": 2}},
            )
        with pytest.raises(CatalogConflict):
            set_model_version_metrics(version_id=version.version_id, metric_codes=())
        with pytest.raises(CatalogConflict):
            upload_model_artifact(
                version_id=version.version_id,
                logical_name="weights",
                source=BytesIO(b"late artifact"),
            )


@pytest.mark.django_db
@pytest.mark.parametrize("algorithm_key", ["hr_br", "missing.algorithm"])
def test_template_and_unknown_algorithms_cannot_be_published(algorithm_key):
    """正式接口模板及未知标识都没有可执行注册定义。"""
    model = create_model(code="pulse", name="Pulse", description=None)
    version = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=algorithm_key,
        params=None,
    )

    with pytest.raises(CatalogConfigurationInvalid):
        publish_model_version(version_id=version.version_id)

    persisted = get_model_version(version_id=version.version_id)
    assert persisted is not None and persisted.published_at is None


@pytest.mark.django_db
def test_invalid_fixed_parameters_cannot_be_published():
    """示例算法拒绝非空版本参数并保持版本未发布。"""
    version = _create_example_version(params={"window": 2})

    with pytest.raises(CatalogConfigurationInvalid):
        publish_model_version(version_id=version.version_id)

    persisted = get_model_version(version_id=version.version_id)
    assert persisted is not None and persisted.published_at is None


@pytest.mark.django_db
def test_publish_requires_exact_artifact_inventory_and_existing_file(tmp_path, monkeypatch):
    """发布严格匹配算法制品名并校验已登记物理文件仍存在。"""
    definition = _required_artifact_algorithm()
    monkeypatch.setitem(_ALGORITHMS, definition.key, definition)
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=32,
    ):
        model = create_model(code="artifact-model", name="Artifact", description=None)
        version = create_model_version(
            model_id=model.model_id,
            version="1.0",
            algorithm_key=definition.key,
            params=None,
        )
        with pytest.raises(CatalogConfigurationInvalid):
            publish_model_version(version_id=version.version_id)

        artifact = upload_model_artifact(
            version_id=version.version_id,
            logical_name="weights",
            source=BytesIO(b"model weights"),
        )
        stored_path = Path(tmp_path / "artifacts" / artifact.storage_key)
        stored_path.unlink()
        with pytest.raises(CatalogConfigurationInvalid):
            publish_model_version(version_id=version.version_id)
