"""验证成员目录归属、物理残留清理和无法隔离时的顶层失败。"""

import shutil
from dataclasses import replace

import pytest
from health_inference.exceptions import InferenceError
from health_inference.scenes import PPG_SCENE


@pytest.mark.parametrize("foreign_kind", ["member", "task", "token", "traversal"])
def test_foreign_output_is_rejected_without_deleting_foreign_file(
    tmp_path, scene_case, scene_factory, foreign_kind,
):
    """真实血氧输出冒用其他成员、任务或令牌文件时只清理自身目录。"""
    original = PPG_SCENE.members[1].definition.resource_factory
    if foreign_kind in ("task", "token", "traversal"):
        parent = tmp_path / ("another-task/token" if foreign_kind == "task" else "another-token") / "spo2"
        parent.mkdir(parents=True)
        foreign = parent / "ppg-processed.csv"
        foreign.write_bytes(b"private-foreign-bytes")
    else:
        foreign = tmp_path / "execution" / "hr_br" / "ppg-processed.csv"

    def create(resources):
        """保留真实成员计算，仅破坏返回文件归属。"""
        executor = original(resources)

        def substitute(value):
            """计算写出后，用不属于本次成员的路径冒充成功文件。"""
            output = executor(value)
            path = foreign
            if foreign_kind == "traversal":
                path = value.output_dir / ".." / "hr_br" / "ppg-processed.csv"
            return replace(output, output_files={"processed_ppg": path})

        return substitute

    source = scene_case.input(tmp_path / "execution")
    output = scene_factory(spo2=create).create_runtime(scene_case.resources()).run(source)
    assert output.payload["outcome"] == "PARTIALLY_SUCCEEDED"
    assert output.payload["results"]["spo2"]["failureCode"] == "RESULT.INVALID_OUTPUT"
    assert set(output.metrics) == {"hr", "br", "diabetes_probability"}
    assert not (source.output_dir / "spo2").exists()
    assert foreign.is_file()
    if foreign_kind != "member":
        assert foreign.read_bytes() == b"private-foreign-bytes"
    assert all(not name.startswith("spo2_") for name in output.output_files)


def test_unregistered_member_residue_is_not_left_in_publishable_tree(tmp_path, scene_case, scene_factory):
    """未登记文件不能随整目录发布；该成员转失败并删除本次全部残留。"""
    original = PPG_SCENE.members[1].definition.resource_factory

    def create(resources):
        """在真实成功后写入未声明残留。"""
        executor = original(resources)

        def leave_residue(value):
            """制造字典之外的实际文件，检查物理树而非字典过滤。"""
            output = executor(value)
            nested = value.output_dir / "temporary"
            nested.mkdir()
            (nested / "private.csv").write_bytes(b"PRIVATE_RAW_DATA")
            return output

        return leave_residue

    source = scene_case.input(tmp_path / "execution")
    output = scene_factory(spo2=create).create_runtime(scene_case.resources()).run(source)
    assert output.payload["results"]["spo2"]["status"] == "FAILED"
    assert not (source.output_dir / "spo2").exists()
    actual_files = {path for path in source.output_dir.rglob("*") if path.is_file()}
    assert actual_files == set(output.output_files.values())
    assert all(b"PRIVATE_RAW_DATA" not in path.read_bytes() for path in actual_files)


@pytest.mark.parametrize("kind", ["symlink", "cleanup_error", "replaced_directory"])
def test_unprovable_failure_cleanup_rejects_entire_scene(
    tmp_path, scene_case, scene_factory, monkeypatch, kind,
):
    """清理不能证明隔离时不得假装成员失败后继续提交场景成功。"""
    original = PPG_SCENE.members[1].definition.resource_factory
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    secret = foreign / "secret.csv"
    secret.write_bytes(b"PRIVATE_FOREIGN_FILE")

    def create(resources):
        """在真实输出后注入清理无法安全完成的状态。"""
        executor = original(resources)

        def fail(value):
            """不可信符号链接或替换目录只能触发场景级拒绝。"""
            executor(value)
            if kind == "symlink":
                (value.output_dir / "escape").symlink_to(secret)
            elif kind == "replaced_directory":
                shutil.rmtree(value.output_dir)
                value.output_dir.symlink_to(foreign, target_is_directory=True)
            raise RuntimeError("PRIVATE_EXECUTION_FAILURE")

        return fail

    if kind == "cleanup_error":
        def cannot_clean(path):
            """模拟操作系统拒绝清理失败成员目录。"""
            raise PermissionError("PRIVATE_STORAGE_PATH")

        monkeypatch.setattr("health_inference.scenes.shutil.rmtree", cannot_clean)
    runtime = scene_factory(spo2=create).create_runtime(scene_case.resources())
    with pytest.raises(InferenceError) as error:
        runtime.run(scene_case.input(tmp_path / "execution"))
    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert secret.read_bytes() == b"PRIVATE_FOREIGN_FILE"


def test_preexisting_execution_content_is_not_reused_or_deleted(tmp_path, scene_case):
    """场景只拥有本次新建子目录，不能覆盖或清理先前执行的产物。"""
    from health_inference import PPG_ACTIVE_OFFLINE_ALL_V1

    source = scene_case.input(tmp_path / "execution")
    previous = source.output_dir / "previous.csv"
    previous.write_bytes(b"previous-execution")
    with pytest.raises(InferenceError) as error:
        PPG_ACTIVE_OFFLINE_ALL_V1.create_runtime(scene_case.resources()).run(source)
    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert previous.read_bytes() == b"previous-execution"


def test_symlink_execution_root_is_never_followed(tmp_path, scene_case):
    """任务根目录即使指向可访问目录，也不能借符号链接复用其他执行。"""
    from health_inference import PPG_ACTIVE_OFFLINE_ALL_V1

    source = scene_case.input(tmp_path / "real-execution")
    link = tmp_path / "linked-execution"
    link.symlink_to(source.output_dir, target_is_directory=True)
    with pytest.raises(InferenceError) as error:
        PPG_ACTIVE_OFFLINE_ALL_V1.create_runtime(scene_case.resources()).run(replace(source, output_dir=link))
    assert error.value.code == "RESULT.INVALID_OUTPUT"
    assert list(source.output_dir.iterdir()) == []
