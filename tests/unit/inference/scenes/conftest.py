"""提供使用真实 ONNX、joblib 和固定传感器样本的场景夹具。"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from health_inference import InferenceInput, VersionResources

from tests.integration.ppg_inputs import synthetic_ppg_csv

_SAMPLE_ROOT = Path(__file__).resolve().parents[4] / "var" / "old-assets" / "sample"


class LinearBPModel:
    """对实际六维特征计算线性值，不以固定回声代替推理。"""

    def __init__(self, intercept):
        """保存测试模型固定系数。"""
        self.intercept = intercept

    def predict(self, features):
        """执行实际特征加权计算。"""
        return [self.intercept + sum(float(value) * 0.01 for value in row[:5]) for row in features]


def _write_onnx(path: Path, *, diabetes: bool) -> None:
    """写入依赖真实特征值的合成 ONNX，而非模拟会话。"""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    width = 918 if diabetes else 4
    nodes = [helper.make_node(
        "ReduceMean", ["first_feature" if diabetes else "input"], ["mean"], axes=[1], keepdims=0,
    )]
    initializers = []
    if diabetes:
        nodes.insert(0, helper.make_node("Gather", ["input", "first_index"], ["first_feature"], axis=1))
        initializers.extend([
            numpy_helper.from_array(np.asarray(0.001, dtype=np.float32), "scale"),
            numpy_helper.from_array(np.asarray(1.0, dtype=np.float32), "one"),
            numpy_helper.from_array(np.asarray(0.5, dtype=np.float32), "half"),
            numpy_helper.from_array(np.asarray([1], dtype=np.int64), "axes"),
            numpy_helper.from_array(np.asarray([0], dtype=np.int64), "first_index"),
        ])
        nodes.extend([
            helper.make_node("Mul", ["mean", "scale"], ["logits"]),
            helper.make_node("Sigmoid", ["logits"], ["positive"]),
            helper.make_node("Sub", ["one", "positive"], ["negative"]),
            helper.make_node("Unsqueeze", ["negative", "axes"], ["negative_column"]),
            helper.make_node("Unsqueeze", ["positive", "axes"], ["positive_column"]),
            helper.make_node("Concat", ["negative_column", "positive_column"], ["probabilities"], axis=1),
            helper.make_node("Greater", ["positive", "half"], ["positive_label"]),
            helper.make_node("Cast", ["positive_label"], ["label"], to=TensorProto.INT64),
        ])
        outputs = [
            helper.make_tensor_value_info("label", TensorProto.INT64, [None]),
            helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [None, 2]),
        ]
    else:
        initializers.append(numpy_helper.from_array(np.asarray(93.0, dtype=np.float32), "bias"))
        nodes.append(helper.make_node("Add", ["mean", "bias"], ["spo2"]))
        outputs = [helper.make_tensor_value_info("spo2", TensorProto.FLOAT, [None])]
    graph = helper.make_graph(
        nodes,
        "scene-behavior-test",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, width])],
        outputs,
        initializer=initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save_model(model, path)


@dataclass
class SceneCase:
    """保存测试版本资源；每次调用创建独立输入和目录。"""

    ppg_artifacts: dict[str, Path]  # PPG 场景本次使用的固定制品。
    bp_artifacts: dict[str, Path]  # 双传感器场景本次使用的固定制品。

    def resources(self, *, bp=False):
        """创建独立场景的固定资源对象。"""
        return VersionResources({}, self.bp_artifacts if bp else self.ppg_artifacts)

    def input(self, directory, *, bp=False, ppg=None):
        """提供同一份真实样本和冻结资料，不复制处理后的波形。"""
        directory.mkdir(parents=True)
        if ppg is None and not bp:
            ppg = directory.parent / f"{directory.name}-ppg.csv"
            ppg.write_text(synthetic_ppg_csv(heart_rate_hz=1.8), encoding="utf-8")
        files = {"ppg": ppg or _SAMPLE_ROOT / "20261101_134952-5-2-bp_vlatest-ppg.csv"}
        if bp:
            files["acc"] = _SAMPLE_ROOT / "20261101_134952-5-2-bp_vlatest-acc.csv"
        return InferenceInput(
            files=files,
            artifacts=self.bp_artifacts if bp else self.ppg_artifacts,
            profile={"age_years": 24, "gender": 1, "height_cm": 170, "weight_kg": 55},
            params={},
            output_dir=directory,
            client_metadata={"nested": {"value": "original"}},
        )


def make_scene_case(root):
    """为本次隔离测试创建完整、可执行的场景资源。"""
    import joblib

    root.mkdir(parents=True, exist_ok=True)
    ppg = {name: root / filename for name, filename in {
        "spo2": "spo2.onnx", "feature_names": "feature_names.json",
        "stable_features": "stable_features.json", "diabetes": "diabetes.onnx",
    }.items()}
    _write_onnx(ppg["spo2"], diabetes=False)
    _write_onnx(ppg["diabetes"], diabetes=True)
    for name in ("feature_names", "stable_features"):
        ppg[name].write_text(json.dumps(["r_mean", "r_std", "g_mean", "b_mean"]), encoding="utf-8")
    bp = {name: root / filename for name, filename in {
        "sbp": "sbp.joblib", "dbp": "dbp.joblib", "normalize": "normalize.csv",
    }.items()}
    joblib.dump(LinearBPModel(120.0), bp["sbp"])
    joblib.dump(LinearBPModel(80.0), bp["dbp"])
    bp["normalize"].write_text(
        ",X1,age,pr,bmi,height\nmiu,0,0,0,0,0\nsegma,1,1,1,1,1\n", encoding="utf-8",
    )
    return SceneCase(ppg, bp)


@pytest.fixture
def scene_case(tmp_path):
    """每个资源破坏回归使用独立物理制品。"""
    return make_scene_case(tmp_path / "artifacts")


@pytest.fixture(scope="module")
def successful_scene(tmp_path_factory):
    """汇总篡改测试共用一次真实计算，不共享可变载荷。"""
    from health_inference import PPG_ACTIVE_OFFLINE_ALL_V1

    root = tmp_path_factory.mktemp("successful-scene")
    case = make_scene_case(root / "artifacts")
    output = PPG_ACTIVE_OFFLINE_ALL_V1.create_runtime(case.resources()).run(case.input(root / "execution"))
    assert output.payload["outcome"] == "ALL_SUCCEEDED"
    return output


@pytest.fixture
def scene_factory():
    """仅替换指定故障边界，其余成员仍执行真实完整流水线。"""
    from dataclasses import replace

    from health_inference.scenes import PPG_SCENE, OfflineScene, SceneMember

    def create(**factories):
        """构造用于故障注入的同契约场景。"""
        return OfflineScene(PPG_SCENE.key, tuple(
            SceneMember(
                member.name,
                replace(member.definition, resource_factory=factories[member.name])
                if member.name in factories else member.definition,
            )
            for member in PPG_SCENE.members
        )).algorithm_definition()

    return create
