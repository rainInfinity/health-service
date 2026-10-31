"""验证模型版本目录用例、唯一性和已求值查询边界。"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from health_service.application.catalog import (
    create_model,
    create_model_version,
    list_model_versions,
    update_model_version,
)
from health_service.application.catalog_exceptions import CatalogConflict, CatalogNotFound
from health_service.infrastructure.persistence.catalog import get_model_version
from health_service.models import Model, ModelVersion

from tests.integration.algorithm_fixtures import TEST_ALGORITHM_KEY


@pytest.mark.django_db
def test_model_and_version_creation_reject_duplicate_identity():
    """重复模型编码及同模型版本号都映射为稳定冲突。"""
    model = create_model(code="pulse", name="Pulse", description=None)
    create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )

    with pytest.raises(CatalogConflict):
        create_model(code="pulse", name="Duplicate", description=None)
    with pytest.raises(CatalogConflict):
        create_model_version(
            model_id=model.model_id,
            version="1.0",
            algorithm_key=TEST_ALGORITHM_KEY,
            params=None,
        )

    assert Model.objects.filter(code="pulse").count() == 1
    assert ModelVersion.objects.filter(model_id=model.model_id).count() == 1


@pytest.mark.django_db
def test_model_version_creation_rejects_missing_owner_without_orphan():
    """引用不存在模型时返回资源缺失且不创建孤立版本。"""
    with pytest.raises(CatalogNotFound):
        create_model_version(
            model_id=999999,
            version="1.0",
            algorithm_key=TEST_ALGORITHM_KEY,
            params=None,
        )

    assert not ModelVersion.objects.exists()


@pytest.mark.django_db
def test_model_version_results_are_evaluated_without_per_row_queries():
    """创建、单版本读取与分页读取使用固定查询数且包含所属模型字段。"""
    model = create_model(code="pulse", name="Pulse", description="PPG model")
    versions = tuple(
        create_model_version(
            model_id=model.model_id,
            version=f"1.{index}",
            algorithm_key=TEST_ALGORITHM_KEY,
            params={"window": index},
        )
        for index in range(10)
    )
    with CaptureQueriesContext(connection) as create_queries:
        created = create_model_version(
            model_id=model.model_id,
            version="2.0",
            algorithm_key=TEST_ALGORITHM_KEY,
            params={"window": 10},
        )
    assert sum(query["sql"].lstrip().upper().startswith("SELECT") for query in create_queries) == 1
    assert (created.model_code, created.model_name, created.params) == (
        "pulse",
        "Pulse",
        {"window": 10},
    )

    with CaptureQueriesContext(connection) as read_queries:
        loaded = get_model_version(version_id=versions[0].version_id)
    assert sum(query["sql"].lstrip().upper().startswith("SELECT") for query in read_queries) == 1
    assert loaded is not None
    assert (loaded.model_code, loaded.model_name, loaded.version) == (
        "pulse",
        "Pulse",
        "1.0",
    )

    with CaptureQueriesContext(connection) as list_queries:
        total, page = list_model_versions(model_id=model.model_id, offset=0, limit=20)
    assert sum(query["sql"].lstrip().upper().startswith("SELECT") for query in list_queries) == 3
    assert total == 11
    assert len(page) == 11
    assert all(version.model_code == "pulse" for version in page)


@pytest.mark.django_db
def test_model_version_update_changes_only_unpublished_configuration():
    """未发布版本接受固定参数更新并返回新的已求值配置。"""
    model = create_model(code="pulse", name="Pulse", description=None)
    version = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=TEST_ALGORITHM_KEY,
        params=None,
    )

    updated = update_model_version(
        version_id=version.version_id,
        changes={"params": {"mode": "test"}},
    )

    assert updated.params == {"mode": "test"}
