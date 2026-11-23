"""验证最终汇总不能伪造成员身份、失败信息、指标或文件。"""

import math
from copy import deepcopy
from dataclasses import replace

import pytest
from health_inference import (
    PPG_ACTIVE_OFFLINE_ALL_V1,
    LowScoreDetails,
    QualityRejectedError,
    QualityRejection,
    QualityRejectionReason,
)
from health_inference.exceptions import InferenceError
from health_inference.scenes import PPG_SCENE, OfflineScene, SceneMember


def _damage(output, kind):
    """在合法真实输出副本上破坏一个独立不变量。"""
    result = output.payload["results"]["hr_br"]
    if kind == "missing_member":
        del output.payload["results"]["diabetes"]
    elif kind == "extra_member":
        output.payload["results"]["unknown"] = deepcopy(result)
    elif kind == "wrong_key":
        result["algorithmKey"] = "ppg.active.offline.spo2.v1"
    elif kind == "wrong_outcome":
        output.payload["outcome"] = "ALL_FAILED"
    elif kind == "extra_field":
        result["failureDetail"] = {"traceback": "/private/path"}
    elif kind == "running_status":
        result["status"] = "RUNNING"
    elif kind == "successful_failure":
        result["failureCode"] = "INFERENCE.EXECUTION_ERROR"
    elif kind == "invalid_br":
        result["result"]["br"] = math.inf
    elif kind == "inconsistent_metric":
        output.metrics["hr"] += 1.0
    elif kind == "extra_metric":
        output.metrics["risk_level"] = 0
    elif kind == "missing_metric":
        del output.metrics["br"]
    elif kind == "wrong_metric_type":
        output.metrics["hr"] = True
    elif kind == "nested_files":
        result["result"]["output_files"] = {"private": "/private/path"}
    elif kind == "extra_file":
        output.output_files["unknown_processed_ppg"] = output.output_files["hr_br_processed_ppg"]
    elif kind == "missing_file":
        del output.output_files["hr_br_processed_rsp"]
    elif kind == "cross_member_file":
        output.output_files["hr_br_processed_ppg"] = output.output_files["spo2_processed_ppg"]


@pytest.mark.parametrize("kind", [
    "missing_member", "extra_member", "wrong_key", "wrong_outcome", "extra_field",
    "running_status", "successful_failure", "invalid_br", "inconsistent_metric",
    "extra_metric", "missing_metric", "wrong_metric_type", "nested_files",
    "extra_file", "missing_file", "cross_member_file",
])
def test_final_validator_rejects_damaged_real_summary(successful_scene, kind):
    """已汇总结果一旦损坏，应顶层拒绝，不能静默改成某成员失败。"""
    output = deepcopy(successful_scene)
    _damage(output, kind)
    with pytest.raises(InferenceError) as error:
        PPG_ACTIVE_OFFLINE_ALL_V1.result_validator(output)
    assert error.value.code == "RESULT.INVALID_OUTPUT"


def test_quality_rejection_is_typed_safe_and_other_members_still_compute(tmp_path, scene_case, scene_factory):
    """合法拒绝只公开白名单，失败指标和物理目录为空。"""
    def reject(resources):
        """模拟成员实际确认的合法质量拒绝。"""
        raise QualityRejectedError(QualityRejection(
            QualityRejectionReason.LOW_SCORE,
            LowScoreDetails(score=31.25, threshold=70),
        ))

    definition = scene_factory(spo2=reject)
    output = definition.create_runtime(scene_case.resources()).run(scene_case.input(tmp_path / "execution"))
    failure = output.payload["results"]["spo2"]
    assert output.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert failure["qualityFailure"] == {"reason": "LOW_SCORE", "details": {"score": 31.25, "threshold": 70}}
    assert failure["result"] is None
    assert "spo2" not in output.metrics
    assert output.payload["results"]["diabetes"]["result"]["prob"] == output.metrics["diabetes_probability"]
    definition.result_validator(output)

    damaged = deepcopy(output)
    damaged.payload["results"]["spo2"]["qualityFailure"]["details"]["private"] = "/private/raw.csv"
    with pytest.raises(InferenceError) as error:
        definition.result_validator(damaged)
    assert error.value.code == "RESULT.INVALID_OUTPUT"


@pytest.mark.parametrize("damage", ["message", "extra", "quality", "result", "metrics", "files"])
def test_failed_member_cannot_smuggle_fields_or_success_artifacts(tmp_path, scene_case, scene_factory, damage):
    """失败成员不能通过安全码之外的文案、明细或投影制造伪造汇总。"""
    def fail(resources):
        """只声明伪造质量码，必须先降级为安全执行错误。"""
        raise InferenceError("INFERENCE.QUALITY_REJECTED", "PRIVATE_INPUT /private/model")

    definition = scene_factory(spo2=fail)
    source = scene_case.input(tmp_path / "execution")
    output = definition.create_runtime(scene_case.resources()).run(source)
    failure = output.payload["results"]["spo2"]
    assert failure["failureCode"] == "INFERENCE.EXECUTION_ERROR"
    assert failure["qualityFailure"] is None
    assert "PRIVATE_INPUT" not in repr(output.payload)
    if damage == "message":
        failure["failureMessage"] = "PRIVATE_INPUT"
    elif damage == "extra":
        failure["failureDetail"] = {"raw": "PRIVATE_INPUT"}
    elif damage == "quality":
        failure["qualityFailure"] = {"reason": "NO_VALID_SIGNAL", "details": None}
    elif damage == "result":
        failure["result"] = {"spo2": 97.0}
    elif damage == "metrics":
        output.metrics["spo2"] = 97.0
    elif damage == "files":
        output.output_files["spo2_processed_ppg"] = output.output_files["hr_br_processed_ppg"]
    with pytest.raises(InferenceError) as error:
        definition.result_validator(output)
    assert error.value.code == "RESULT.INVALID_OUTPUT"


@pytest.mark.parametrize("kind", ["artifacts", "metrics", "inputs", "names"])
def test_declaration_rejects_conflicts_instead_of_overwriting_contracts(kind):
    """声明并集冲突在执行前被拒绝，不能让后项覆盖前项。"""
    first, second, third = PPG_SCENE.members
    if kind == "artifacts":
        third = replace(third, definition=replace(
            third.definition,
            required_artifacts=("spo2",),
            artifact_extensions={"spo2": frozenset({"onnx"})},
        ))
    elif kind == "metrics":
        third = replace(third, definition=replace(
            third.definition, metric_contracts=first.definition.metric_contracts,
        ))
    elif kind == "inputs":
        third = replace(third, definition=replace(
            third.definition, input_extensions={"ppg": frozenset({"json"})},
        ))
    elif kind == "names":
        third = SceneMember(first.name, third.definition)
    scene = OfflineScene(PPG_SCENE.key, (first, second, third))
    with pytest.raises(ValueError):
        scene.algorithm_definition()
