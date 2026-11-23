"""验证本地文件存储键、上传回滚和执行输出隔离。"""

import os
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from health_service.infrastructure.storage import (
    EmptyUploadError,
    InvalidOutputFile,
    InvalidStorageKey,
    InvalidStorageName,
    LocalFileStorage,
    StorageArea,
    StorageError,
    StorageFileNotFound,
    StoredFile,
    UploadTooLargeError,
)


def _storage(tmp_path: Path) -> LocalFileStorage:
    """为单个测试创建隔离的任务和制品目录。"""
    return LocalFileStorage(tmp_path / "task-data", tmp_path / "model-artifacts")


class _NamedBytesIO(BytesIO):
    """模拟带有客户端文件名的上传流。"""

    def __init__(self, content: bytes, name: str) -> None:
        """初始化带原始文件名的内存上传流。"""
        super().__init__(content)
        self.name = name


def _save_task_input(
    storage: LocalFileStorage,
    source: object,
    *,
    source_filename: str = "signal.csv",
    allowed_extensions: frozenset[str] = frozenset({"csv"}),
    created_at: datetime = datetime(2026, 10, 31, 16, 30, 45, tzinfo=UTC),
    task_id: int = 42,
    user_id: int = 7,
    channel: str = "ios-cn",
    algorithm_key: str = "ppg.active.offline.hr_br.v1",
    logical_name: str = "ppg",
    max_bytes: int = 100,
) -> StoredFile:
    """按固定任务上下文写入测试输入。"""
    return storage.save_task_input(
        source,
        source_filename=source_filename,
        allowed_extensions=allowed_extensions,
        created_at=created_at,
        task_id=task_id,
        user_id=user_id,
        channel=channel,
        algorithm_key=algorithm_key,
        logical_name=logical_name,
        max_bytes=max_bytes,
    )


class _ChunkOnlySource:
    """提供分块接口并拒绝一次性读取的测试上传流。"""

    def __init__(self, chunks: list[bytes]) -> None:
        """保存测试分块并初始化请求记录。"""
        self._chunks = chunks
        self.requested_sizes: list[int] = []

    def chunks(self, chunk_size: int):
        """逐块返回数据并记录存储端的读取上限。"""
        self.requested_sizes.append(chunk_size)
        yield from self._chunks

    def read(self, _size: int):
        """确保存储端不调用无界读取接口。"""
        raise AssertionError("上传应通过 chunks() 流式读取")


def test_same_client_filename_gets_unique_keys_in_separate_roots(tmp_path: Path):
    """验证同名输入不覆盖，任务输入与模型文件使用不同根目录。"""
    storage = _storage(tmp_path)
    first = _save_task_input(
        storage,
        _NamedBytesIO(b"task input", "same.csv"),
        source_filename="same.csv",
    )
    second = storage.save_model_artifact(
        _NamedBytesIO(b"model artifact", "weights.ONNX"),
        source_filename="weights.ONNX",
        allowed_extensions=frozenset({"onnx"}),
        model_code="hr-br",
        version="1.0.0",
        logical_name="weights",
        max_bytes=100,
    )

    repeated = _save_task_input(
        storage,
        _NamedBytesIO(b"another task input", "same.csv"),
        source_filename="same.csv",
    )

    assert first.storage_key != repeated.storage_key
    assert first.storage_key.startswith("ios-cn/20261101/20261101_003045-42-7-")
    assert first.storage_key.endswith(".csv")
    assert "same.csv" not in first.storage_key
    assert (
        storage.resolve_file(StorageArea.TASK_DATA, first.storage_key).read_bytes() == b"task input"
    )
    assert (
        storage.resolve_file(StorageArea.MODEL_ARTIFACT, second.storage_key).read_bytes()
        == b"model artifact"
    )
    assert (
        storage.resolve_file(StorageArea.TASK_DATA, repeated.storage_key).read_bytes()
        == b"another task input"
    )
    assert re.fullmatch(
        r"hr%2Dbr/1\.0\.0/weights-[0-9a-f]{32}\.onnx",
        second.storage_key,
    )


def test_task_input_path_uses_shanghai_time_and_stable_identity_fields(tmp_path: Path):
    """验证 UTC 创建时间转换为上海日期并按契约命名新输入。"""
    stored = _save_task_input(
        _storage(tmp_path),
        _NamedBytesIO(b"signal", "signal.CSV"),
        source_filename="signal.CSV",
        algorithm_key="rppg.face.active_realtime.hr",
    )

    assert re.fullmatch(
        r"ios-cn/20261101/20261101_003045-42-7-rppg\.face\.active_realtime\.hr-ppg-[0-9a-f]{32}\.csv",
        stored.storage_key,
    )


def test_task_input_path_encodes_untrusted_segments_without_losing_data(tmp_path: Path):
    """验证字段分隔符、Unicode 与穿越片段均编码在单一路径段内。"""
    stored = _save_task_input(
        _storage(tmp_path),
        BytesIO(b"signal"),
        algorithm_key="ppg.active/..-算法",
        logical_name="raw/信号",
    )
    parts = stored.storage_key.split("/")

    assert parts[:2] == ["ios-cn", "20261101"]
    assert len(parts) == 3
    assert "%2F..%2D" in parts[2]
    assert "raw%2F%E4%BF%A1%E5%8F%B7" in parts[2]
    assert all(part not in {".", ".."} for part in parts)


def test_task_input_rejects_overlong_encoded_filename_without_truncating(
    tmp_path: Path,
):
    """验证文件系统名称超限时拒绝保存且不截断或创建文件。"""
    storage = _storage(tmp_path)
    name_max = os.pathconf(storage.task_data_root, "PC_NAME_MAX")

    with pytest.raises(InvalidStorageName):
        _save_task_input(
            storage,
            BytesIO(b"signal"),
            algorithm_key="a" * name_max,
        )

    assert list(storage.task_data_root.iterdir()) == []


def test_task_input_rejects_timezone_naive_creation_time(tmp_path: Path):
    """验证缺少时区的任务创建时间不能生成具有歧义的路径日期。"""
    with pytest.raises(InvalidStorageName):
        _save_task_input(
            _storage(tmp_path),
            BytesIO(b"signal"),
            created_at=datetime(2026, 11, 1, 0, 30, 45),
        )


def test_upload_is_written_from_bounded_chunks(tmp_path: Path):
    """验证上传流逐块读取且不调用一次性 read。"""
    storage = _storage(tmp_path)
    source = _ChunkOnlySource([b"first", b"second"])

    stored = _save_task_input(
        storage,
        source,
        source_filename="signal.csv",
    )

    assert source.requested_sizes == [64 * 1024]
    assert stored.size_bytes == 11
    assert (
        storage.resolve_file(StorageArea.TASK_DATA, stored.storage_key).read_bytes()
        == b"firstsecond"
    )


def test_empty_and_oversized_uploads_leave_existing_files_untouched(tmp_path: Path):
    """验证空文件和超限文件不留有效文件，也不删旧文件。"""
    storage = _storage(tmp_path)
    existing = _save_task_input(storage, BytesIO(b"preserve"))

    with pytest.raises(EmptyUploadError):
        _save_task_input(storage, BytesIO(b""))
    with pytest.raises(UploadTooLargeError):
        _save_task_input(
            storage,
            BytesIO(b"too large"),
            max_bytes=3,
        )

    assert (
        storage.resolve_file(StorageArea.TASK_DATA, existing.storage_key).read_bytes()
        == b"preserve"
    )
    assert len(list((storage.task_data_root / "ios-cn" / "20261101").iterdir())) == 1


def test_interrupted_upload_removes_only_its_partial_file(tmp_path: Path):
    """验证流中断后删除本次未完成文件并保留以前文件。"""
    storage = _storage(tmp_path)
    existing = _save_task_input(storage, BytesIO(b"preserve"))

    class InterruptedSource:
        """在部分数据后模拟上传中断。"""

        def chunks(self, _chunk_size: int):
            """先写入一块，再报告传输中断。"""
            yield b"partial"
            raise RuntimeError("sensitive source detail")

    with pytest.raises(StorageError, match="文件写入失败") as error:
        _save_task_input(storage, InterruptedSource())

    assert "sensitive source detail" not in str(error.value)
    assert (
        storage.resolve_file(StorageArea.TASK_DATA, existing.storage_key).read_bytes()
        == b"preserve"
    )
    assert len(list((storage.task_data_root / "ios-cn" / "20261101").iterdir())) == 1


@pytest.mark.parametrize(
    "storage_key",
    ["", "/etc/passwd", "../outside", "nested/../../outside", "C:/outside", "nested\\outside"],
)
def test_resolve_rejects_absolute_and_traversal_keys(tmp_path: Path, storage_key: str):
    """验证绝对键、目录穿越和 Windows 路径分隔符均被拒绝。"""
    with pytest.raises(InvalidStorageKey):
        _storage(tmp_path).resolve_file(StorageArea.TASK_DATA, storage_key)


def test_resolve_rejects_symlink_escape_and_missing_files(tmp_path: Path):
    """验证符号链接不能读取根目录外文件，缺失文件返回安全错误。"""
    storage = _storage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.csv").write_text("private", encoding="utf-8")
    (storage.task_data_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidStorageKey):
        storage.resolve_file(StorageArea.TASK_DATA, "escape/private.csv")
    with pytest.raises(StorageFileNotFound):
        storage.resolve_file(StorageArea.TASK_DATA, "missing.csv")


@pytest.mark.parametrize(
    "storage_key",
    ["", ".", "/etc/passwd", "../outside", "nested/../../outside", "C:/outside", "a\\b"],
)
def test_artifact_candidate_rejects_unsafe_registered_keys(tmp_path: Path, storage_key: str):
    """延迟成员加载不允许非法登记键形成候选引用。"""
    with pytest.raises(InvalidStorageKey):
        _storage(tmp_path).resolve_model_artifact_candidate(storage_key)


def test_artifact_candidate_defers_only_file_availability(tmp_path: Path):
    """候选允许最终文件缺失或为目录，默认解析仍要求普通文件。"""
    storage = _storage(tmp_path)
    directory = storage.model_artifact_root / "version"
    directory.mkdir()
    for key in ("version/missing.onnx", "version"):
        candidate = storage.resolve_model_artifact_candidate(key)
        assert candidate == storage.model_artifact_root / key
        with pytest.raises(StorageFileNotFound):
            storage.resolve_file(StorageArea.MODEL_ARTIFACT, key)
    artifact = directory / "model.onnx"
    artifact.write_bytes(b"registered model")
    assert storage.resolve_model_artifact_candidate("version/model.onnx").read_bytes() == (
        b"registered model"
    )


@pytest.mark.parametrize("dangling", [False, True])
def test_artifact_candidate_rejects_existing_symlink_escape(tmp_path: Path, dangling: bool):
    """最终文件缺失也不能绕过已有父目录及文件符号链接的根边界。"""
    storage = _storage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if not dangling:
        (outside / "private.onnx").write_bytes(b"private")
    (storage.model_artifact_root / "escape").symlink_to(outside, target_is_directory=True)
    (storage.model_artifact_root / "model.onnx").symlink_to(outside / "private.onnx")
    for key in ("escape/private.onnx", "model.onnx"):
        with pytest.raises(InvalidStorageKey):
            storage.resolve_model_artifact_candidate(key)


def test_artifact_candidate_requires_existing_original_root(tmp_path: Path):
    """已创建的存储对象不能在根目录消失或被越界链接替换后继续延迟解析。"""
    storage = _storage(tmp_path)
    storage.model_artifact_root.rmdir()
    with pytest.raises(StorageFileNotFound):
        storage.resolve_model_artifact_candidate("version/model.onnx")
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.model_artifact_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidStorageKey):
        storage.resolve_model_artifact_candidate("version/model.onnx")


def test_legacy_storage_keys_remain_readable(tmp_path: Path):
    """验证旧输入、模型制品和执行输出相对键仍可读取。"""
    storage = _storage(tmp_path)
    legacy_task_key = f"uploads/{uuid4().hex}.bin"
    legacy_output_key = f"outputs/42/{uuid4().hex}/ppg-processed.csv"
    legacy_artifact_key = f"uploads/{uuid4().hex}.bin"
    legacy_files = (
        (storage.task_data_root, legacy_task_key, b"old task input"),
        (storage.task_data_root, legacy_output_key, b"old result"),
        (storage.model_artifact_root, legacy_artifact_key, b"old model artifact"),
    )

    for root, storage_key, content in legacy_files:
        path = root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    assert (
        storage.resolve_file(StorageArea.TASK_DATA, legacy_task_key).read_bytes()
        == b"old task input"
    )
    assert (
        storage.resolve_file(StorageArea.TASK_DATA, legacy_output_key).read_bytes() == b"old result"
    )
    assert (
        storage.resolve_file(StorageArea.MODEL_ARTIFACT, legacy_artifact_key).read_bytes()
        == b"old model artifact"
    )
    assert not storage.publish_task_output_directory(
        channel="ios-cn",
        created_at=datetime(2026, 10, 31, 16, 30, 45, tzinfo=UTC),
        task_id=42,
        output_storage_keys={"report": legacy_output_key},
    )


def test_published_output_directory_atomically_tracks_latest_execution(
    tmp_path: Path,
) -> None:
    """验证正式目录只暴露当前执行文件且可重复发布。"""
    storage = _storage(tmp_path)
    created_at = datetime(2026, 10, 31, 16, 30, 45, tzinfo=UTC)
    first_token = uuid4()
    first_dir = storage.create_execution_output_dir(42, first_token)
    first_keys = {}
    for logical_name, filename, content in (
        ("pulse", "ppg-processed.csv", b"first pulse"),
        ("diagnostic", "diagnostic.json", b"first diagnostic"),
    ):
        output = first_dir / filename
        output.write_bytes(content)
        first_keys[logical_name] = storage.validate_output_file(
            42,
            first_token,
            output,
        )

    assert storage.publish_task_output_directory(
        channel="ios-cn",
        created_at=created_at,
        task_id=42,
        output_storage_keys=first_keys,
    )

    published_dir = storage.task_data_root / "ios-cn" / "20261101" / "outputs" / "42"
    first_link = os.readlink(published_dir)
    assert not Path(first_link).is_absolute()
    assert (published_dir.parent / first_link).resolve() == first_dir.resolve()
    assert sorted(path.name for path in published_dir.iterdir()) == [
        "diagnostic.json",
        "ppg-processed.csv",
    ]
    assert (published_dir / "ppg-processed.csv").read_bytes() == b"first pulse"
    assert storage.publish_task_output_directory(
        channel="ios-cn",
        created_at=created_at,
        task_id=42,
        output_storage_keys=first_keys,
    )
    assert os.readlink(published_dir) == first_link

    second_token = uuid4()
    second_dir = storage.create_execution_output_dir(42, second_token)
    second_keys = {}
    for logical_name, filename, content in (
        ("pulse", "ppg-processed.csv", b"latest pulse"),
        ("respiration", "rsp-processed.csv", b"latest respiration"),
    ):
        output = second_dir / filename
        output.write_bytes(content)
        second_keys[logical_name] = storage.validate_output_file(
            42,
            second_token,
            output,
        )

    assert storage.publish_task_output_directory(
        channel="ios-cn",
        created_at=created_at,
        task_id=42,
        output_storage_keys=second_keys,
    )

    second_link = os.readlink(published_dir)
    assert second_link != first_link
    assert (published_dir.parent / second_link).resolve() == second_dir.resolve()
    assert sorted(path.name for path in published_dir.iterdir()) == [
        "ppg-processed.csv",
        "rsp-processed.csv",
    ]
    assert (published_dir / "ppg-processed.csv").read_bytes() == b"latest pulse"
    assert (published_dir / "rsp-processed.csv").read_bytes() == b"latest respiration"
    assert first_dir.exists()


def test_failed_output_link_replacement_preserves_previous_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证原子替换失败时旧链接保留且临时链接清理。"""
    storage = _storage(tmp_path)
    created_at = datetime(2026, 10, 31, 16, 30, 45, tzinfo=UTC)
    output_token = uuid4()
    output_dir = storage.create_execution_output_dir(42, output_token)
    output = output_dir / "ppg-processed.csv"
    output.write_bytes(b"published")
    output_keys = {"pulse": storage.validate_output_file(42, output_token, output)}
    assert storage.publish_task_output_directory(
        channel="ios-cn",
        created_at=created_at,
        task_id=42,
        output_storage_keys=output_keys,
    )
    published_dir = storage.task_data_root / "ios-cn" / "20261101" / "outputs" / "42"
    previous_link = os.readlink(published_dir)
    next_token = uuid4()
    next_dir = storage.create_execution_output_dir(42, next_token)
    next_output = next_dir / "ppg-processed.csv"
    next_output.write_bytes(b"new")
    next_keys = {"pulse": storage.validate_output_file(42, next_token, next_output)}

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(StorageError):
        storage.publish_task_output_directory(
            channel="ios-cn",
            created_at=created_at,
            task_id=42,
            output_storage_keys=next_keys,
        )

    assert os.readlink(published_dir) == previous_link
    assert sorted(path.name for path in published_dir.iterdir()) == ["ppg-processed.csv"]
    assert not list(published_dir.parent.glob(".42-*.tmp"))


def test_output_publication_rejects_execution_symlink_escape(
    tmp_path: Path,
) -> None:
    """验证正式结果发布不会将根目录外执行目录暴露为结果目录。"""
    storage = _storage(tmp_path)
    outside_dir = tmp_path / "outside-execution"
    outside_dir.mkdir()
    (outside_dir / "report.csv").write_bytes(b"private")
    token = uuid4()
    execution_dir = storage.task_data_root / ".executions" / "42" / token.hex
    execution_dir.parent.mkdir(parents=True)
    execution_dir.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(InvalidStorageKey):
        storage.publish_task_output_directory(
            channel="ios-cn",
            created_at=datetime(2026, 10, 31, 16, 30, 45, tzinfo=UTC),
            task_id=42,
            output_storage_keys={"report": f".executions/42/{token.hex}/report.csv"},
        )


def test_output_references_are_bound_to_task_and_execution_token(tmp_path: Path):
    """验证输出目录按任务和令牌隔离且只能引用其中的现存文件。"""
    storage = _storage(tmp_path)
    first_token = uuid4()
    second_token = uuid4()
    first_dir = storage.create_execution_output_dir(42, first_token)
    second_dir = storage.create_execution_output_dir(42, second_token)
    output = first_dir / "diagnostic.json"
    output.write_text("{}", encoding="utf-8")
    outside = storage.task_data_root / "untrusted.json"
    outside.write_text("{}", encoding="utf-8")

    reference = storage.validate_output_file(42, first_token, output)

    assert reference == output.relative_to(storage.task_data_root).as_posix()
    assert reference.startswith(f".executions/42/{first_token.hex}/")
    assert storage.resolve_file(StorageArea.TASK_DATA, reference) == output.resolve()
    assert first_dir != second_dir
    with pytest.raises(InvalidOutputFile):
        storage.validate_output_file(42, second_token, output)
    with pytest.raises(InvalidOutputFile):
        storage.validate_output_file(42, first_token, outside)
    with pytest.raises(InvalidOutputFile):
        storage.validate_output_file(42, first_token, first_dir / "missing.json")
    with pytest.raises(InvalidOutputFile):
        storage.validate_output_file(0, first_token, output)
