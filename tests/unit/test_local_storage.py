"""验证本地文件存储键、上传回滚和执行输出隔离。"""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from health_service.infrastructure.storage import (
    EmptyUploadError,
    InvalidOutputFile,
    InvalidStorageKey,
    LocalFileStorage,
    StorageArea,
    StorageError,
    StorageFileNotFound,
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
    """验证同名输入不覆盖，任务和模型文件使用不同根目录。"""
    storage = _storage(tmp_path)
    first = storage.save_upload(
        _NamedBytesIO(b"task input", "same.csv"),
        area=StorageArea.TASK_DATA,
        max_bytes=100,
    )
    second = storage.save_upload(
        _NamedBytesIO(b"model artifact", "same.csv"),
        area=StorageArea.MODEL_ARTIFACT,
        max_bytes=100,
    )

    repeated = storage.save_upload(
        _NamedBytesIO(b"another task input", "same.csv"),
        area=StorageArea.TASK_DATA,
        max_bytes=100,
    )

    assert first.storage_key != repeated.storage_key
    assert "same.csv" not in first.storage_key
    assert storage.resolve_file(StorageArea.TASK_DATA, first.storage_key).read_bytes() == b"task input"
    assert (
        storage.resolve_file(StorageArea.MODEL_ARTIFACT, second.storage_key).read_bytes()
        == b"model artifact"
    )
    assert storage.resolve_file(StorageArea.TASK_DATA, repeated.storage_key).read_bytes() == b"another task input"


def test_upload_is_written_from_bounded_chunks(tmp_path: Path):
    """验证上传流逐块读取且不调用一次性 read。"""
    storage = _storage(tmp_path)
    source = _ChunkOnlySource([b"first", b"second"])

    stored = storage.save_upload(source, area=StorageArea.TASK_DATA, max_bytes=100)

    assert source.requested_sizes == [64 * 1024]
    assert stored.size_bytes == 11
    assert storage.resolve_file(StorageArea.TASK_DATA, stored.storage_key).read_bytes() == b"firstsecond"


def test_empty_and_oversized_uploads_leave_existing_files_untouched(tmp_path: Path):
    """验证空文件和超限文件不留有效文件，也不删旧文件。"""
    storage = _storage(tmp_path)
    existing = storage.save_upload(
        BytesIO(b"preserve"),
        area=StorageArea.TASK_DATA,
        max_bytes=100,
    )

    with pytest.raises(EmptyUploadError):
        storage.save_upload(BytesIO(b""), area=StorageArea.TASK_DATA, max_bytes=100)
    with pytest.raises(UploadTooLargeError):
        storage.save_upload(BytesIO(b"too large"), area=StorageArea.TASK_DATA, max_bytes=3)

    assert storage.resolve_file(StorageArea.TASK_DATA, existing.storage_key).read_bytes() == b"preserve"
    assert len(list((storage.task_data_root / "uploads").iterdir())) == 1


def test_interrupted_upload_removes_only_its_partial_file(tmp_path: Path):
    """验证流中断后删除本次未完成文件并保留以前文件。"""
    storage = _storage(tmp_path)
    existing = storage.save_upload(
        BytesIO(b"preserve"),
        area=StorageArea.TASK_DATA,
        max_bytes=100,
    )

    class InterruptedSource:
        """在部分数据后模拟上传中断。"""

        def chunks(self, _chunk_size: int):
            """先写入一块，再报告传输中断。"""
            yield b"partial"
            raise RuntimeError("sensitive source detail")

    with pytest.raises(StorageError, match="文件写入失败") as error:
        storage.save_upload(InterruptedSource(), area=StorageArea.TASK_DATA, max_bytes=100)

    assert "sensitive source detail" not in str(error.value)
    assert storage.resolve_file(StorageArea.TASK_DATA, existing.storage_key).read_bytes() == b"preserve"
    assert len(list((storage.task_data_root / "uploads").iterdir())) == 1


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
