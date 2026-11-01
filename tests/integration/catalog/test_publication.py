"""验证版本制品登记、指标投影与不可变发布边界。"""

from io import BytesIO
from pathlib import Path

import pytest
from django.test import override_settings
from health_inference import register_algorithm
from health_inference.registry import _ALGORITHMS
from health_inference.types import (
    AlgorithmDefinition,
    InferenceInput,
    InferenceOutput,
    MetricContract,
)
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

_PUBLISHABLE_TEST_KEY = "test.catalog.publisher"
_PUBLISHABLE_TEST_METRIC = "test.catalog.value"


def _create_test_catalog_version(
    *,
    code: str = "pulse",
    params: dict | None = None,
    algorithm_key: str = _PUBLISHABLE_TEST_KEY,
):
    """创建目录发布边界测试使用的未发布版本。"""
    model = create_model(code=code, name="Pulse", description=None)
    return create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=algorithm_key,
        params=params,
    )


def _add_test_catalog_metric(version_id: int) -> None:
    """选择测试算法声明的单个计数指标。"""
    create_metric_definition(
        code=_PUBLISHABLE_TEST_METRIC,
        description="测试发布指标",
        value_type="number",
        unit="count",
    )
    set_model_version_metrics(
        version_id=version_id,
        metric_codes=(_PUBLISHABLE_TEST_METRIC,),
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


def _publishable_test_algorithm() -> AlgorithmDefinition:
    """构造目录发布校验所需的测试专用契约。"""
    return AlgorithmDefinition(
        key=_PUBLISHABLE_TEST_KEY,
        is_example=False,
        required_inputs=(),
        required_artifacts=(),
        input_extensions={},
        artifact_extensions={},
        required_profile_fields=(),
        parameter_validator=_validate_test_parameters,
        result_validator=_validate_test_result,
        resource_factory=lambda _: _run_test_algorithm,
        metric_contracts=(MetricContract(_PUBLISHABLE_TEST_METRIC, "number", "count"),),
    )


register_algorithm(_publishable_test_algorithm())


def _required_artifact_algorithm() -> AlgorithmDefinition:
    """构造只用于验证制品发布边界的纯契约定义。"""
    return AlgorithmDefinition(
        key="test.catalog.required-artifact",
        is_example=False,
        required_inputs=(),
        required_artifacts=("weights",),
        input_extensions={},
        artifact_extensions={"weights": frozenset({"bin"})},
        required_profile_fields=(),
        parameter_validator=_validate_test_parameters,
        result_validator=_validate_test_result,
        resource_factory=lambda _: _run_test_algorithm,
        metric_contracts=(),
    )


register_algorithm(_required_artifact_algorithm())


@pytest.mark.django_db
def test_artifact_upload_is_version_scoped_and_removes_duplicate_orphan(tmp_path):
    """同逻辑名跨版本互不覆盖，同版本冲突清除本次孤儿文件。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=32,
    ):
        version = _create_test_catalog_version(
            algorithm_key="test.catalog.required-artifact",
        )
        artifact = upload_model_artifact(
            version_id=version.version_id,
            logical_name="weights",
            source_filename="weights.bin",
            source=BytesIO(b"first artifact"),
        )
        stored_path = tmp_path / "artifacts" / artifact.storage_key
        assert stored_path.read_bytes() == b"first artifact"
        assert artifact.size_bytes == len(b"first artifact")

        other_version = create_model_version(
            model_id=version.model_id,
            version="2.0",
            algorithm_key="test.catalog.required-artifact",
            params=None,
        )
        other_artifact = upload_model_artifact(
            version_id=other_version.version_id,
            logical_name="weights",
            source_filename="weights.bin",
            source=BytesIO(b"second artifact"),
        )
        other_path = tmp_path / "artifacts" / other_artifact.storage_key
        assert other_path.read_bytes() == b"second artifact"
        assert stored_path != other_path
        assert stored_path.relative_to(tmp_path / "artifacts").parts[:2] == (
            "pulse",
            "1.0",
        )
        assert other_path.relative_to(tmp_path / "artifacts").parts[:2] == (
            "pulse",
            "2.0",
        )
        assert stored_path.name.startswith("weights-")
        assert other_path.name.startswith("weights-")


        with pytest.raises(CatalogConflict):
            upload_model_artifact(
                version_id=version.version_id,
                logical_name="weights",
                source_filename="weights.bin",
                source=BytesIO(b"duplicate artifact"),
            )

        assert ModelArtifact.objects.filter(model_version_id=version.version_id).count() == 1
        assert stored_path.read_bytes() == b"first artifact"
        assert set((tmp_path / "artifacts").rglob("*.bin")) == {
            stored_path,
            other_path,
        }
        published = publish_model_version(version_id=version.version_id)
        assert published.published_at is not None
        with pytest.raises(CatalogConflict):
            upload_model_artifact(
                version_id=version.version_id,
                logical_name="weights",
                source_filename="weights.bin",
                source=BytesIO(b"replacement artifact"),
            )
        assert stored_path.read_bytes() == b"first artifact"
        assert ModelArtifact.objects.filter(model_version_id=version.version_id).count() == 1


@pytest.mark.django_db
def test_unknown_metric_does_not_replace_existing_projection():
    """未知代码拒绝整批替换并保留原有合法指标集合。"""
    version = _create_test_catalog_version()
    metric = create_metric_definition(
        code=_PUBLISHABLE_TEST_METRIC,
        description="测试指标",
        value_type="number",
        unit="count",
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
    version = _create_test_catalog_version()
    with pytest.raises(CatalogConfigurationInvalid):
        create_metric_definition(
            code=_PUBLISHABLE_TEST_METRIC,
            description="错误类型",
            value_type="boolean",
            unit="count",
        )

    mismatched = MetricDefinition.objects.create(
        code=_PUBLISHABLE_TEST_METRIC,
        description="测试指标语义",
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
def test_test_algorithm_version_publishes_without_artifacts_and_is_immutable(tmp_path):
    """零制品测试算法可发布，重发不改时间且已发布配置不能变更。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
        MAX_UPLOAD_BYTES=32,
    ):
        version = _create_test_catalog_version()
        _add_test_catalog_metric(version.version_id)
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
                source_filename="weights.bin",
                source=BytesIO(b"late artifact"),
            )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "algorithm_key",
    ["hr_br", "ppg.active.offline.hr_br", "missing.algorithm"],
)
def test_template_and_unknown_algorithms_cannot_be_published(algorithm_key):
    """旧模板、无修订号别名和未知标识都不能发布。"""
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
    """测试算法拒绝非空版本参数并保持版本未发布。"""
    version = _create_test_catalog_version(params={"window": 2})

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
            source_filename="weights.bin",
            source=BytesIO(b"model weights"),
        )
        stored_path = Path(tmp_path / "artifacts" / artifact.storage_key)
        stored_path.unlink()
        with pytest.raises(CatalogConfigurationInvalid):
            publish_model_version(version_id=version.version_id)
def _create_ppg_v1_version(*, params: dict | None = None):
    """创建正式 HR/BR v1 的未发布版本。"""
    model = create_model(code="pulse-v1", name="Pulse v1", description=None)
    return create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key="ppg.active.offline.hr_br.v1",
        params=params,
    )


def _add_ppg_v1_metrics(version_id: int) -> None:
    """选择正式 v1 声明的两个固定单位指标。"""
    definitions = (
        ("hr", "beats_per_minute"),
        ("br", "breaths_per_minute"),
    )
    for code, unit in definitions:
        create_metric_definition(
            code=code,
            description=code,
            value_type="number",
            unit=unit,
        )
    set_model_version_metrics(
        version_id=version_id,
        metric_codes=tuple(code for code, _ in definitions),
    )


@pytest.mark.django_db
def test_official_ppg_v1_publishes_without_artifacts(tmp_path):
    """正式 v1 可按完整指标契约发布且无需模型制品。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        version = _create_ppg_v1_version()
        _add_ppg_v1_metrics(version.version_id)

        published = publish_model_version(version_id=version.version_id)

    assert published.published_at is not None
    assert not ModelArtifact.objects.filter(model_version_id=version.version_id).exists()
    assert tuple(
        metric.code for metric in list_model_version_metrics(version_id=version.version_id)
    ) == ("hr", "br")


@pytest.mark.django_db
def test_official_ppg_v1_rejects_nonempty_parameters():
    """正式 v1 的非空固定参数阻止发布。"""
    version = _create_ppg_v1_version(params={"filter_order": 3})

    with pytest.raises(CatalogConfigurationInvalid):
        publish_model_version(version_id=version.version_id)

    persisted = get_model_version(version_id=version.version_id)
    assert persisted is not None and persisted.published_at is None
