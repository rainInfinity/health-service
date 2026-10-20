"""验证任务成功结果与指标投影的租约及事务边界。"""

import math
from collections.abc import Mapping
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import QuerySet
from django.test import override_settings
from django.utils import timezone
from health_inference import (
    AlgorithmDefinition,
    InferenceError,
    InferenceInput,
    InferenceOutput,
    LowScoreDetails,
    MetricContract,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
    register_algorithm,
)
from health_service.application.catalog import (
    create_metric_definition,
    create_model,
    create_model_version,
    publish_model_version,
    set_model_version_metrics,
)
from health_service.application.execution import (
    claim_task_for_execution,
    record_task_execution_failure,
    save_task_execution_success,
)
from health_service.infrastructure.persistence.tasks import (
    save_task_execution_success as persist_task_execution_success,
)
from health_service.infrastructure.storage import LocalFileStorage
from health_service.models import Channel, Task, TaskMetric
from health_service.schemas.internal.execution import (
    PreparedInferenceResult,
    ProjectedTaskMetricData,
)


def _accept_empty_parameters(params: Mapping[str, object]) -> None:
    """为结果测试算法声明空参数契约。"""
    if params:
        raise ValueError("参数必须为空")


def _accept_result(_: InferenceOutput) -> None:
    """接受由用例单独检查的测试结果。"""


def _unused_runner(_: InferenceInput) -> InferenceOutput:
    """阻止结果持久化测试意外执行算法。"""
    raise AssertionError("结果测试不应执行算法")


def _create_running_task() -> tuple[Task, UUID, dict[str, str], dict[str, int]]:
    """创建固定契约、已发布版本及持有有效令牌的运行任务。"""
    prefix = f"test.result.{uuid4().hex}"
    suffixes = {
        "count": ("number", "items"),
        "enabled": ("boolean", None),
        "missing": ("number", "items"),
        "unselected": ("number", "items"),
    }
    metric_codes = {name: f"{prefix}.{name}" for name in suffixes}
    contracts = tuple(
        MetricContract(
            code=metric_codes[name],
            value_type=value_type,
            unit=unit,
        )
        for name, (value_type, unit) in suffixes.items()
    )
    register_algorithm(
        AlgorithmDefinition(
            key=prefix,
            is_example=False,
            required_inputs=(),
            required_artifacts=(),
            required_profile_fields=(),
            parameter_validator=_accept_empty_parameters,
            result_validator=_accept_result,
            runner=_unused_runner,
            metric_contracts=contracts,
        )
    )

    model = create_model(code=f"result-{uuid4().hex}", name="结果测试", description=None)
    draft = create_model_version(
        model_id=model.model_id,
        version="1.0",
        algorithm_key=prefix,
        params=None,
    )
    metric_ids: dict[str, int] = {}
    for name, (value_type, unit) in suffixes.items():
        metric = create_metric_definition(
            code=metric_codes[name],
            description=metric_codes[name],
            value_type=value_type,
            unit=unit,
        )
        metric_ids[name] = metric.metric_id
    set_model_version_metrics(
        version_id=draft.version_id,
        metric_codes=tuple(metric_codes[name] for name in ("count", "enabled", "missing")),
    )
    version = publish_model_version(version_id=draft.version_id)

    user = get_user_model().objects.create_user(
        username=f"result-{uuid4().hex}",
        password="Dawn-valley-483!strong",
    )
    now = timezone.now()
    token = uuid4()
    channel, _ = Channel.objects.get_or_create(label="ios-cn")
    task = Task.objects.create(
        user=user,
        channel=channel,
        model_version_id=version.version_id,
        status=Task.StatusChoices.RUNNING,
        profile_snapshot={},
        submitted_at=now,
        started_at=now,
        execution_token=token,
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=1,
    )
    return task, token, metric_codes, metric_ids


@pytest.mark.django_db(transaction=True)
def test_success_commits_result_and_only_returned_selected_metrics():
    """成功事务同时写终态与实际投影，不为缺席或未选指标补值。"""
    task, token, codes, metric_ids = _create_running_task()
    result = PreparedInferenceResult(
        payload={"summary": {"samples": 3}},
        metrics={codes["count"]: 0, codes["enabled"]: False, codes["unselected"]: 7},
        output_files={},
    )

    assert save_task_execution_success(
        task_id=task.pk,
        execution_token=token,
        result=result,
    )

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == {"summary": {"samples": 3}}
    assert task.finished_at is not None
    assert task.execution_token is None
    assert task.lease_expires_at is None
    assert task.failure_code is None
    projections = {
        item.metric_id: item
        for item in TaskMetric.objects.filter(task=task)
    }
    assert set(projections) == {metric_ids["count"], metric_ids["enabled"]}
    assert projections[metric_ids["count"]].numeric_value == 0.0
    assert projections[metric_ids["count"]].boolean_value is None
    assert projections[metric_ids["enabled"]].numeric_value is None
    assert projections[metric_ids["enabled"]].boolean_value is False


@pytest.mark.parametrize(
    ("payload", "metric_values"),
    [
        ({}, {"count": True}),
        ({}, {"enabled": 1}),
        ({}, {"count": math.inf}),
        ({}, {"count": "1"}),
        ({}, {"unknown": 1}),
        ({"nested": [math.nan]}, {}),
    ],
    ids=(
        "boolean-is-not-number",
        "integer-is-not-boolean",
        "non-finite-metric",
        "wrong-metric-type",
        "unknown-metric",
        "non-finite-payload",
    ),
)
@pytest.mark.django_db(transaction=True)
def test_invalid_result_is_rejected_without_changing_running_task(payload, metric_values):
    """违反结果类型或 JSON 契约时不写终态和任何指标。"""
    task, token, codes, _ = _create_running_task()
    metrics = {
        codes.get(name, name): value
        for name, value in metric_values.items()
    }

    with pytest.raises(InferenceError) as error:
        save_task_execution_success(
            task_id=task.pk,
            execution_token=token,
            result=PreparedInferenceResult(
                payload=payload,
                metrics=metrics,
                output_files={},
            ),
        )

    assert error.value.code == "RESULT.INVALID_OUTPUT"
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.finished_at is None
    assert task.execution_token == token
    assert not TaskMetric.objects.filter(task=task).exists()


@pytest.mark.parametrize("stale_kind", ["token", "expired"])
@pytest.mark.django_db(transaction=True)
def test_stale_or_expired_execution_cannot_commit_success(stale_kind):
    """错误令牌或已过期租约不能把运行任务提交为成功。"""
    task, token, codes, _ = _create_running_task()
    supplied_token = token
    if stale_kind == "token":
        supplied_token = uuid4()
    else:
        Task.objects.filter(pk=task.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

    assert not save_task_execution_success(
        task_id=task.pk,
        execution_token=supplied_token,
        result=PreparedInferenceResult(
            payload={"ok": True},
            metrics={codes["count"]: 1},
            output_files={},
        ),
    )

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.finished_at is None
    assert not TaskMetric.objects.filter(task=task).exists()


@pytest.mark.django_db(transaction=True)
def test_metric_constraint_failure_rolls_back_success_transition():
    """指标唯一约束失败时数据库回滚同事务内的成功状态与结果。"""
    task, token, _, metric_ids = _create_running_task()
    duplicate = ProjectedTaskMetricData(metric_id=metric_ids["count"], value=1)

    with pytest.raises(IntegrityError):
        persist_task_execution_success(
            task_id=task.pk,
            execution_token=token,
            result_payload={"ok": True},
            metrics=(duplicate, duplicate),
        )

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.finished_at is None
    assert task.execution_token == token
    assert not TaskMetric.objects.filter(task=task).exists()


@pytest.mark.parametrize(
    ("error", "expected_code", "private_text"),
    [
        (
            InferenceError("TASK.UNKNOWN_ALGORITHM", "HEALTH-PROFILE-SENTINEL"),
            "TASK.UNKNOWN_ALGORITHM",
            "HEALTH-PROFILE-SENTINEL",
        ),
        (
            InferenceError("INFERENCE.INVALID_INPUT", "RAW-INPUT-SENTINEL"),
            "INFERENCE.INVALID_INPUT",
            "RAW-INPUT-SENTINEL",
        ),
        (
            InferenceError("RESULT.PRIVATE_DETAIL", "PRIVATE-PATH-SENTINEL"),
            "RESULT.INVALID_OUTPUT",
            "PRIVATE-PATH-SENTINEL",
        ),
        (None, "INFERENCE.EXECUTION_ERROR", None),
        (
            InferenceError(
                "INFERENCE.QUALITY_REJECTED",
                "FORGED-QUALITY-SENTINEL",
            ),
            "INFERENCE.EXECUTION_ERROR",
            "FORGED-QUALITY-SENTINEL",
        ),
    ],
    ids=(
        "task-code",
        "inference-code",
        "result-code-sanitized",
        "unexpected-error",
        "forged-quality-code",
    ),
)
@pytest.mark.django_db(transaction=True)
def test_failure_persists_safe_code_without_private_diagnostics(
    error, expected_code, private_text
):
    """失败终态只保存受控代码，不保留异常正文或任意明细。"""
    task, token, _, _ = _create_running_task()

    assert record_task_execution_failure(
        task_id=task.pk,
        execution_token=token,
        error=error,
    ) == expected_code

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == expected_code
    assert task.failure_message is not None
    if private_text is not None:
        assert private_text not in task.failure_message
    assert task.failure_detail is None
    assert task.finished_at is not None
    assert task.execution_token is None
    assert task.lease_expires_at is None


@pytest.mark.parametrize(
    ("rejection", "expected_detail"),
    [
        (
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(40, 70),
            ),
            {
                "quality": {
                    "reason": "LOW_SCORE",
                    "details": {"score": 40, "threshold": 70},
                }
            },
        ),
        (
            QualityRejection(QualityRejectionReason.NO_VALID_SIGNAL, None),
            {
                "quality": {
                    "reason": "NO_VALID_SIGNAL",
                    "details": None,
                }
            },
        ),
    ],
    ids=("low-score", "no-valid-signal"),
)
@pytest.mark.django_db(transaction=True)
def test_quality_rejection_commits_whitelisted_failure_terminal(
    rejection: QualityRejection,
    expected_detail: dict[str, object],
):
    """两类质量拒绝原子保存失败终态且不生成成功结果或指标。"""
    task, token, _, _ = _create_running_task()
    error = QualityRejectedError(rejection)
    error.args = ("PRIVATE-QUALITY-SENTINEL",)
    error.safe_message = "PRIVATE-QUALITY-SENTINEL"

    assert record_task_execution_failure(
        task_id=task.pk,
        execution_token=token,
        error=error,
    ) == "INFERENCE.QUALITY_REJECTED"

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.QUALITY_REJECTED"
    assert task.failure_message is not None
    assert "PRIVATE-QUALITY-SENTINEL" not in task.failure_message
    assert task.failure_detail == expected_detail
    assert task.finished_at is not None
    assert task.execution_token is None
    assert task.lease_expires_at is None
    assert task.result_payload is None
    assert not TaskMetric.objects.filter(task=task).exists()

@pytest.mark.parametrize("corruption", ["unknown-reason", "invalid-score"])
@pytest.mark.django_db(transaction=True)
def test_corrupt_quality_rejection_is_generic_failure(corruption: str):
    """未知原因或损坏评分不能通过服务层校验成为质量结论。"""
    task, token, _, _ = _create_running_task()
    rejection = QualityRejection(
        QualityRejectionReason.LOW_SCORE,
        LowScoreDetails(40, 70),
    )
    if corruption == "unknown-reason":
        object.__setattr__(rejection, "reason", "UNKNOWN")
    else:
        object.__setattr__(rejection.details, "score", 70)
    error = QualityRejectedError(
        QualityRejection(
            QualityRejectionReason.LOW_SCORE,
            LowScoreDetails(40, 70),
        )
    )
    error.rejection = rejection
    error.args = ("PRIVATE-CORRUPT-QUALITY-SENTINEL",)
    error.safe_message = "PRIVATE-CORRUPT-QUALITY-SENTINEL"

    assert record_task_execution_failure(
        task_id=task.pk,
        execution_token=token,
        error=error,
    ) == "INFERENCE.EXECUTION_ERROR"

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.EXECUTION_ERROR"
    assert task.failure_message is not None
    assert "PRIVATE-CORRUPT-QUALITY-SENTINEL" not in task.failure_message
    assert task.failure_detail is None



@pytest.mark.django_db(transaction=True)
def test_quality_failure_transaction_rolls_back_all_fields(monkeypatch):
    """终态更新之后发生数据库错误时回滚全部失败字段。"""
    task, token, _, _ = _create_running_task()
    original_update = QuerySet.update

    def update_then_fail(queryset: QuerySet, **fields: object) -> int:
        """在失败终态 SQL 执行后模拟事务内数据库故障。"""
        updated = original_update(queryset, **fields)
        if fields.get("failure_code") == "INFERENCE.QUALITY_REJECTED":
            raise IntegrityError("PRIVATE-DATABASE-SENTINEL")
        return updated

    monkeypatch.setattr(QuerySet, "update", update_then_fail)
    with pytest.raises(IntegrityError):
        record_task_execution_failure(
            task_id=task.pk,
            execution_token=token,
            error=QualityRejectedError(
                QualityRejection(
                    QualityRejectionReason.LOW_SCORE,
                    LowScoreDetails(40, 70),
                )
            ),
        )

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.failure_code is None
    assert task.failure_message is None
    assert task.failure_detail is None
    assert task.finished_at is None
    assert task.execution_token == token
    assert task.lease_expires_at is not None
    assert not TaskMetric.objects.filter(task=task).exists()


@pytest.mark.parametrize("stale_kind", ["token", "expired"])
@pytest.mark.django_db(transaction=True)
def test_stale_or_expired_execution_cannot_commit_failure(stale_kind):
    """错误令牌或过期租约不能将任务终结为失败。"""
    task, token, _, _ = _create_running_task()
    supplied_token = token
    if stale_kind == "token":
        supplied_token = uuid4()
    else:
        Task.objects.filter(pk=task.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

    assert record_task_execution_failure(
        task_id=task.pk,
        execution_token=supplied_token,
        error=QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.LOW_SCORE,
                LowScoreDetails(40, 70),
            )
        ),
    ) is None

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.failure_code is None
    assert task.failure_message is None
    assert task.failure_detail is None
    assert task.finished_at is None
    assert task.execution_token == token
    assert task.lease_expires_at is not None
    assert not TaskMetric.objects.filter(task=task).exists()


@pytest.mark.django_db(transaction=True)
def test_old_execution_failure_cannot_overwrite_reclaimed_success():
    """旧令牌的迟到失败不能覆盖新执行已提交的成功结果。"""
    task, old_token, _, _ = _create_running_task()
    Task.objects.filter(pk=task.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    new_token = claim_task_for_execution(task_id=task.pk)
    assert new_token is not None and new_token != old_token
    assert save_task_execution_success(
        task_id=task.pk,
        execution_token=new_token,
        result=PreparedInferenceResult(
            payload={"attempt": 2},
            metrics={},
            output_files={},
        ),
    )

    assert record_task_execution_failure(
        task_id=task.pk,
        execution_token=old_token,
        error=QualityRejectedError(
            QualityRejection(
                QualityRejectionReason.NO_VALID_SIGNAL,
                None,
            )
        ),
    ) is None

    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload == {"attempt": 2}
    assert task.failure_code is None
    assert task.failure_message is None
    assert task.finished_at is not None


@pytest.mark.parametrize("outcome", ["success", "failure", "expired"])
@pytest.mark.django_db(transaction=True)
def test_output_file_is_authoritative_only_after_valid_success_commit(
    tmp_path, outcome
):
    """仅当前有效执行的成功事务能把专属输出登记为权威结果。"""
    with override_settings(
        TASK_DATA_ROOT=tmp_path / "tasks",
        MODEL_ARTIFACT_ROOT=tmp_path / "artifacts",
    ):
        task, token, codes, _ = _create_running_task()
        storage = LocalFileStorage(
            tmp_path / "tasks",
            tmp_path / "artifacts",
        )
        output_dir = storage.create_execution_output_dir(task.pk, token)
        output_file = output_dir / "diagnostic.csv"
        output_file.write_text("sample_count\n4\n", encoding="utf-8")
        storage_key = storage.validate_output_file(task.pk, token, output_file)
        result = PreparedInferenceResult(
            payload={"ok": True},
            metrics={codes["count"]: 4},
            output_files={"diagnostic": storage_key},
        )

        if outcome == "success":
            assert save_task_execution_success(
                task_id=task.pk,
                execution_token=token,
                result=result,
            )
        elif outcome == "failure":
            assert record_task_execution_failure(
                task_id=task.pk,
                execution_token=token,
                error=QualityRejectedError(
                    QualityRejection(
                        QualityRejectionReason.LOW_SCORE,
                        LowScoreDetails(40, 70),
                    )
                ),
            ) == "INFERENCE.QUALITY_REJECTED"
        else:
            Task.objects.filter(pk=task.pk).update(
                lease_expires_at=timezone.now() - timedelta(seconds=1)
            )
            assert not save_task_execution_success(
                task_id=task.pk,
                execution_token=token,
                result=result,
            )

    task.refresh_from_db()
    assert output_file.read_text(encoding="utf-8") == "sample_count\n4\n"
    if outcome == "success":
        assert task.status == Task.StatusChoices.SUCCEEDED
        assert task.result_payload == {
            "ok": True,
            "output_files": {"diagnostic": storage_key},
        }
    else:
        assert task.status == (
            Task.StatusChoices.FAILED
            if outcome == "failure"
            else Task.StatusChoices.RUNNING
        )
        assert task.result_payload is None
