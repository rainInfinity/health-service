"""验证真实场景汇总的成功投影、任务级错误、事务回滚与失租拒绝。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.utils import timezone
from health_service.application.execution import save_task_execution_success
from health_service.management.commands import run_inference as inference_command
from health_service.models import Task, TaskInputFile, TaskMetric

from tests.integration.ppg_inputs import synthetic_ppg_csv
from tests.integration.workflows.test_diabetes_inference_consumer import _task_output_root
from tests.integration.workflows.test_scene_support import (
    artifact_path,
    create_scene_version,
    prepare_scene_result,
    run_scene_consumer,
    submit_scene_task,
)
from tests.integration.workflows.test_scene_support import (
    scene_environment as scene_environment,
)

pytestmark = pytest.mark.django_db(transaction=True)
_INVALID_PPG = "timestampFrame,avgR,avgG,avgB\n0,120,128,136\n"


def test_all_failed_commits_nonempty_success_summary_without_metrics(scene_environment):
    """所有成员拒绝输入仍保存完整成功汇总，顶层失败为空且不发布目录。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(environment, model, version, ppg_content=_INVALID_PPG)
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == "ALL_FAILED"
    assert set(task.result_payload["results"]) == {"hr", "br", "spo2", "diabetes"}
    for member in task.result_payload["results"].values():
        assert member["status"] == "FAILED"
        assert member["result"] is None
        assert member["failureCode"]
        assert member["failureMessage"]
    assert "output_files" not in task.result_payload
    assert task.failure_code is task.failure_message is task.failure_detail is None
    assert task.execution_token is task.lease_expires_at is None
    assert task.finished_at is not None
    assert not TaskMetric.objects.filter(task=task).exists()
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 0


def test_mixed_summary_projects_only_successful_and_selected_metrics(scene_environment):
    """失败 diabetes 不补默认概率，成功但未选中的 BR/血氧也不创建投影。"""
    environment = scene_environment
    model, version = create_scene_version(
        environment, selected_metrics=("hr", "diabetes_probability")
    )
    artifact_path(environment, version, "diabetes").write_bytes(b"PRIVATE-corrupt-model")
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.SUCCEEDED
    assert task.result_payload["outcome"] == "PARTIALLY_SUCCEEDED"
    members = task.result_payload["results"]
    assert members["hr"]["status"] == members["br"]["status"] == members["spo2"]["status"] == "SUCCEEDED"
    assert members["diabetes"]["status"] == "FAILED"
    assert members["diabetes"]["result"] is None
    assert {
        metric.metric.code: metric.numeric_value
        for metric in TaskMetric.objects.filter(task=task).select_related("metric")
    } == {"hr": members["hr"]["result"]["hr"]}
    assert "PRIVATE" not in str(task.result_payload)
    assert task.failure_code is task.failure_message is task.failure_detail is None


def test_invalid_final_scene_summary_becomes_task_failure(scene_environment, monkeypatch):
    """实际计算完成后损坏最终汇总，服务拒绝落库且不把错误归给某个成员。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    save_actual = inference_command.save_task_execution_success

    def corrupt_final_summary(*, task_id, execution_token, result):
        """仅破坏真实汇总的最终契约，不伪造成员计算结果。"""
        assert result.payload["outcome"] == "ALL_SUCCEEDED"
        payload = deepcopy(dict(result.payload))
        del payload["results"]["spo2"]
        return save_actual(
            task_id=task_id,
            execution_token=execution_token,
            result=replace(result, payload=payload),
        )

    monkeypatch.setattr(inference_command, "save_task_execution_success", corrupt_final_summary)
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "RESULT.INVALID_OUTPUT"
    assert task.result_payload is None
    assert task.execution_token is task.lease_expires_at is None
    assert not TaskMetric.objects.filter(task=task).exists()
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 0


def test_unreadable_raw_input_stays_task_level_failure(scene_environment):
    """已提交原始文件不可读时不伪造逐成员结果。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment, model, version, ppg_content=synthetic_ppg_csv(heart_rate_hz=1.8)
    )
    raw = TaskInputFile.objects.get(task=task, is_selected=True)
    (environment.storage.task_data_root / raw.storage_key).unlink()
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.FAILED
    assert task.failure_code == "INFERENCE.INVALID_INPUT"
    assert task.result_payload is None
    assert not TaskMetric.objects.filter(task=task).exists()
    assert not _task_output_root(environment.storage.task_data_root, task).is_symlink()


@pytest.mark.parametrize("all_failed", [False, True])
def test_scene_database_failure_rolls_back_summary_metrics_and_terminal(
    scene_environment, monkeypatch, all_failed
):
    """成功和全失败汇总在终态更新后遇数据库错误都完整回滚且不 ACK。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment,
        model,
        version,
        ppg_content=_INVALID_PPG if all_failed else synthetic_ppg_csv(heart_rate_hz=1.8),
    )
    bulk_create = TaskMetric.objects.bulk_create

    def fail_after_metric_write(objects, **kwargs):
        """执行真实指标写入后注入数据库故障，覆盖空指标和非空指标路径。"""
        bulk_create(objects, **kwargs)
        raise DatabaseError("PRIVATE-scene-transaction-failure")

    monkeypatch.setattr(TaskMetric.objects, "bulk_create", fail_after_metric_write)
    with pytest.raises(CommandError, match="推理消费者的数据库操作失败"):
        run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.failure_code is task.failure_message is task.failure_detail is None
    assert task.execution_token is not None and task.lease_expires_at is not None
    assert task.finished_at is None
    assert task.attempt_count == 1
    assert not TaskMetric.objects.filter(task=task).exists()
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 1


@pytest.mark.parametrize("all_failed", [False, True])
def test_outer_transaction_rollback_never_publishes_scene(scene_environment, all_failed):
    """合法汇总在外层事务回滚后不留下结果、指标或 on_commit 正式目录。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment,
        model,
        version,
        ppg_content=_INVALID_PPG if all_failed else synthetic_ppg_csv(heart_rate_hz=1.8),
    )
    token, result = prepare_scene_result(task)
    with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
        assert save_task_execution_success(task_id=task.pk, execution_token=token, result=result)
        raise RuntimeError("rollback")
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.execution_token == token
    assert task.finished_at is None
    assert not TaskMetric.objects.filter(task=task).exists()
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()


@pytest.mark.parametrize("all_failed", [False, True])
@pytest.mark.parametrize("lost_kind", ["expired", "replaced"])
def test_completed_scene_losing_lease_cannot_commit_or_ack(
    scene_environment, monkeypatch, all_failed, lost_kind
):
    """实际完整计算后失去租约，成功或全失败汇总都不能成为权威结果。"""
    environment = scene_environment
    model, version = create_scene_version(environment)
    task = submit_scene_task(
        environment,
        model,
        version,
        ppg_content=_INVALID_PPG if all_failed else synthetic_ppg_csv(heart_rate_hz=1.8),
    )
    save_actual = inference_command.save_task_execution_success
    replacement_token = uuid4()

    def lose_lease_before_commit(*, task_id, execution_token, result):
        """在真实汇总与保存之间撤销数据库执行权，避免依赖线程时序。"""
        assert result.payload["outcome"] == ("ALL_FAILED" if all_failed else "ALL_SUCCEEDED")
        changes = (
            {"lease_expires_at": timezone.now() - timedelta(seconds=1)}
            if lost_kind == "expired"
            else {"execution_token": replacement_token}
        )
        assert Task.objects.filter(pk=task_id, execution_token=execution_token).update(**changes) == 1
        return save_actual(task_id=task_id, execution_token=execution_token, result=result)

    monkeypatch.setattr(inference_command, "save_task_execution_success", lose_lease_before_commit)
    run_scene_consumer()
    task.refresh_from_db()
    assert task.status == Task.StatusChoices.RUNNING
    assert task.result_payload is None
    assert task.failure_code is None
    assert task.finished_at is None
    assert task.attempt_count == 1
    if lost_kind == "replaced":
        assert task.execution_token == replacement_token
    assert not TaskMetric.objects.filter(task=task).exists()
    formal = _task_output_root(environment.storage.task_data_root, task)
    assert not formal.exists() and not formal.is_symlink()
    assert environment.redis.xpending(environment.stream, environment.group)["pending"] == 1
